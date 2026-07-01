"""Project-local Lark app configuration helpers.

The app is snapshotted from the user's current lark-cli profile during bootstrap
and then read from `.local/lark-cli/config.json` for all later runs.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_LOCAL_DIR = PROJECT_ROOT / ".local"
PROJECT_LARK_CLI_DIR = PROJECT_LOCAL_DIR / "lark-cli"
PROJECT_LARK_CLI_CONFIG_PATH = PROJECT_LARK_CLI_DIR / "config.json"
GLOBAL_LARK_CLI_CONFIG_PATH = Path.home() / ".lark-cli" / "config.json"


@dataclass(frozen=True)
class LarkAppCredentials:
    app_id: str
    app_secret: str
    brand: str


class LarkConfigError(RuntimeError):
    pass


_LARK_CLI_SERVICE = "lark-cli"
_MASTER_KEY_ACCOUNT = "master.key"
_KEYRING_BASE64_PREFIX = "go-keyring-base64:"
_IV_BYTES = 12
_TAG_BYTES = 16
_SAFE_FILE_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]")


def project_lark_cli_config_exists() -> bool:
    return PROJECT_LARK_CLI_CONFIG_PATH.exists()


def lark_cli_subprocess_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env["LARKSUITE_CLI_CONFIG_DIR"] = str(PROJECT_LARK_CLI_DIR)
    return env


def load_lark_app_credentials() -> LarkAppCredentials:
    data = _read_json_config(PROJECT_LARK_CLI_CONFIG_PATH, label="项目内 lark-cli 配置")
    app = _current_app(data)
    app_id = str(app.get("appId") or app.get("app_id") or "")
    if not app_id:
        raise LarkConfigError("项目内 lark-cli 配置缺少 appId，请先执行 npm run bootstrap")
    return LarkAppCredentials(
        app_id=app_id,
        app_secret=_resolve_secret(app.get("appSecret")),
        brand=str(app.get("brand") or "feishu").lower(),
    )


def snapshot_current_lark_cli_config_to_project() -> Path:
    data = _read_json_config(GLOBAL_LARK_CLI_CONFIG_PATH, label="全局 lark-cli 配置")
    app = _current_app(data)
    app_id = str(app.get("appId") or app.get("app_id") or "")
    if not app_id:
        raise LarkConfigError("全局 lark-cli 当前应用缺少 appId")
    app_secret = _resolve_secret(app.get("appSecret"))
    brand = str(app.get("brand") or "feishu").lower()
    profile_name = str(app.get("name") or app_id)
    local_app: dict[str, Any] = {
        "appId": app_id,
        "appSecret": app_secret,
        "brand": brand,
        "users": app.get("users") if isinstance(app.get("users"), list) else [],
    }
    for key in ("name", "lang", "defaultAs", "strictMode"):
        if key in app and app[key] not in (None, ""):
            local_app[key] = app[key]
    local_config = {
        "currentApp": profile_name,
        "apps": [local_app],
    }
    if "strictMode" in data and data["strictMode"] not in (None, ""):
        local_config["strictMode"] = data["strictMode"]
    PROJECT_LARK_CLI_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = PROJECT_LARK_CLI_CONFIG_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(local_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _chmod_if_posix(tmp_path, 0o600)
    tmp_path.replace(PROJECT_LARK_CLI_CONFIG_PATH)
    _chmod_if_posix(PROJECT_LARK_CLI_CONFIG_PATH, 0o600)
    return PROJECT_LARK_CLI_CONFIG_PATH


def _chmod_if_posix(path: Path, mode: int) -> None:
    """仅在 POSIX 平台调用 os.chmod 设置权限位。

    Windows 上 os.chmod 仅支持只读位，POSIX 权限掩码会被忽略且容易误导，
    因此这里直接跳过。
    """
    if os.name == "nt":
        return
    try:
        os.chmod(path, mode)
    except OSError:
        # 某些受限文件系统（例如 Linux 上的 /tmp tmpfs 配置）可能不支持，
        # 忽略以保证主流程可继续。
        pass


def _read_json_config(path: Path, *, label: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LarkConfigError(f"{label}不存在: {path}") from exc
    except OSError as exc:
        raise LarkConfigError(f"读取{label}失败: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LarkConfigError(f"{label}不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LarkConfigError(f"{label}格式非法")
    return data


def _current_app(data: dict[str, Any]) -> dict[str, Any]:
    apps = data.get("apps") or []
    if not isinstance(apps, list) or not apps:
        return data
    current = str(data.get("currentApp") or "")
    if current:
        for item in apps:
            if not isinstance(item, dict):
                continue
            if current in {str(item.get("name") or ""), str(item.get("appId") or "")}:
                return item
        raise LarkConfigError(f"currentApp={current} 在 apps 中不存在")
    first = apps[0]
    return first if isinstance(first, dict) else {}


def _resolve_secret(raw: Any) -> str:
    if isinstance(raw, str) and raw:
        return raw
    if isinstance(raw, dict) and isinstance(raw.get("ref"), dict):
        raw = raw["ref"]
    if not isinstance(raw, dict):
        raise LarkConfigError("lark-cli 配置缺少 appSecret")
    source = str(raw.get("source") or "")
    ref_id = str(raw.get("id") or "")
    if source == "file" and ref_id:
        try:
            return Path(ref_id).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise LarkConfigError(f"读取 appSecret 文件失败: {exc}") from exc
    if source == "keychain" and ref_id:
        # macOS Keychain 引用：仅 macOS 上有效。
        if sys.platform == "darwin":
            return _read_macos_keychain_secret(ref_id)
        raise LarkConfigError(
            "appSecret 引用 source=keychain 仅在 macOS 上可用；"
            "请在当前平台重新执行 `lark-cli config init`，或在配置中显式填入 appSecret 明文。"
        )
    if source in {"libsecret", "secret-service"} and ref_id:
        # Linux Secret Service。
        if sys.platform.startswith("linux"):
            return _read_libsecret_secret(ref_id)
        raise LarkConfigError(
            "appSecret 引用 source=libsecret 仅在 Linux 上可用。"
        )
    if source in {"wincred", "credential-manager"} and ref_id:
        if os.name == "nt":
            return _read_windows_credential_secret(ref_id)
        raise LarkConfigError(
            "appSecret 引用 source=wincred 仅在 Windows 上可用。"
        )
    raise LarkConfigError(f"不支持的 appSecret 引用: source={source or '-'}")


def _read_macos_keychain_secret(account: str) -> str:
    encrypted_path = _macos_storage_dir() / _safe_file_name(account)
    if encrypted_path.exists():
        return _read_encrypted_secret(encrypted_path, _read_keychain_entry)
    return _read_keychain_entry(account)


def _read_libsecret_secret(account: str) -> str:
    """通过 secret-tool / 本地加密文件读取 Linux 上 lark-cli 存储的 secret。"""
    encrypted_path = _linux_storage_dir() / _safe_file_name(account)
    if encrypted_path.exists():
        return _read_encrypted_secret(encrypted_path, _read_secret_tool_entry)
    return _read_secret_tool_entry(account)


def _read_windows_credential_secret(account: str) -> str:
    encrypted_path = _windows_storage_dir() / _safe_file_name(account)
    if encrypted_path.exists():
        return _read_encrypted_secret(encrypted_path, _read_wincred_entry)
    return _read_wincred_entry(account)


def _read_encrypted_secret(encrypted_path: Path, master_key_reader) -> str:
    try:
        data = encrypted_path.read_bytes()
    except OSError as exc:
        raise LarkConfigError(f"读取加密 appSecret 文件失败: {exc}") from exc

    errors: list[str] = []
    file_key_path = encrypted_path.parent / "master.key.file"
    if file_key_path.exists():
        try:
            file_key = file_key_path.read_bytes()
            return _decrypt_aes_gcm(data, file_key)
        except (OSError, ValueError) as exc:
            errors.append(f"本地 master.key.file 解密失败: {exc}")

    try:
        master_key_b64 = master_key_reader(_MASTER_KEY_ACCOUNT)
        master_key = _decode_master_key(master_key_b64)
        return _decrypt_aes_gcm(data, master_key)
    except (ValueError, LarkConfigError) as exc:
        errors.append(f"系统密钥库 master.key 解密失败: {exc}")

    detail = "；".join(errors) if errors else "未知原因"
    raise LarkConfigError(f"解密 appSecret 失败：{detail}")


def _decrypt_aes_gcm(data: bytes, key: bytes) -> str:
    if len(key) != 32:
        raise ValueError("master key 长度非法")
    if len(data) < _IV_BYTES + _TAG_BYTES:
        raise ValueError("加密数据长度非法")
    iv = data[:_IV_BYTES]
    ciphertext = data[_IV_BYTES:]
    try:
        plaintext = AESGCM(key).decrypt(iv, ciphertext, None)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("AES-GCM 解密失败") from exc
    return plaintext.decode("utf-8")


def _decode_master_key(value: str) -> bytes:
    encoded = value.strip()
    if encoded.startswith(_KEYRING_BASE64_PREFIX):
        wrapped = encoded[len(_KEYRING_BASE64_PREFIX) :]
        encoded = base64.b64decode(wrapped, validate=True).decode("utf-8")
    return base64.b64decode(encoded, validate=True)


def _lark_cli_storage_dir() -> Path:
    """返回当前平台 lark-cli 加密文件存储目录。"""
    if sys.platform == "darwin":
        return _macos_storage_dir()
    if os.name == "nt":
        return _windows_storage_dir()
    return _linux_storage_dir()


def _macos_storage_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / _LARK_CLI_SERVICE


def _windows_storage_dir() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / _LARK_CLI_SERVICE
    return Path.home() / "AppData" / "Roaming" / _LARK_CLI_SERVICE


def _linux_storage_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / _LARK_CLI_SERVICE
    return Path.home() / ".local" / "share" / _LARK_CLI_SERVICE


def _safe_file_name(account: str) -> str:
    return _SAFE_FILE_NAME_RE.sub("_", account) + ".enc"


def _read_keychain_entry(account: str) -> str:
    try:
        res = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                _LARK_CLI_SERVICE,
                "-a",
                account,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LarkConfigError("读取 macOS Keychain 条目失败") from exc
    if res.returncode != 0:
        raise LarkConfigError("读取 macOS Keychain 条目失败，请确认钥匙串可访问")
    secret = res.stdout.strip()
    if not secret:
        raise LarkConfigError("macOS Keychain 返回了空值")
    return secret


def _read_secret_tool_entry(account: str) -> str:
    """通过 secret-tool 从 Linux Secret Service 读取条目。"""
    try:
        res = subprocess.run(
            [
                "secret-tool",
                "lookup",
                "service",
                _LARK_CLI_SERVICE,
                "account",
                account,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LarkConfigError(
            "读取 Linux Secret Service 条目失败：未安装 secret-tool 或无法访问 D-Bus"
        ) from exc
    if res.returncode != 0:
        raise LarkConfigError(
            "读取 Linux Secret Service 条目失败，请确认已登录 keyring 并安装 libsecret-tools"
        )
    secret = res.stdout.strip()
    if not secret:
        raise LarkConfigError("Linux Secret Service 返回了空值")
    return secret


def _read_wincred_entry(account: str) -> str:
    """Windows Credential Manager 暂不支持直接读取密码本体。

    cmdkey 仅能列出条目存在性，无法返回密码字段；从 Win32 API CredRead
    读取需要额外的 PowerShell P/Invoke 桥接，复杂且对受限策略不友好。
    因此此处显式抛错，引导用户走加密文件兜底路径或显式 appSecret 明文。
    """
    target = f"{_LARK_CLI_SERVICE}:{account}"
    try:
        subprocess.run(
            ["cmdkey", "/list:" + target],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LarkConfigError(
            "无法访问 Windows Credential Manager（cmdkey 调用失败）"
        ) from exc
    raise LarkConfigError(
        "Windows 上 lark-cli 的 wincred 条目密码无法直接读取，请确认 "
        f"{_windows_storage_dir() / _safe_file_name(account)} 加密文件存在，"
        "或在配置中显式填入 appSecret 明文。"
    )
