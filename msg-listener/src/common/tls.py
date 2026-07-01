"""本地 Python 运行时的 TLS 信任辅助逻辑。

部分受管 macOS / Windows / Linux 环境只把公司根证书安装到系统证书库
（macOS Keychain、Windows Cert:\\LocalMachine\\Root、Linux 系统 bundle）。
基于 OpenSSL 的 Python wheel 默认仍使用 certifi/OpenSSL 的 CA 文件，
因此 HTTPS 调用可能报 CERTIFICATE_VERIFY_FAILED。这里会把 certifi
与系统根证书合并为本地 CA bundle，并通过环境变量让常见 TLS 客户端
使用它。
"""
from __future__ import annotations

import logging
import os
import platform
import ssl
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_CA_BUNDLE = PROJECT_ROOT / ".local" / "certs" / "system-ca-bundle.pem"

_PEM_BEGIN = "-----BEGIN CERTIFICATE-----"
_PEM_END = "-----END CERTIFICATE-----"

_LINUX_CA_CANDIDATES = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/cert.pem",
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
)


def ensure_system_tls_trust() -> Path | None:
    """为 Python HTTP/WebSocket 客户端安装进程级 CA bundle。

    如果外部已经显式配置 CA，则不覆盖。其他情况下按平台分发：
    - Darwin：合并 certifi + Keychain；
    - Windows：合并 certifi + Windows 证书库（Root/CA）；
    - Linux：若发现系统 CA bundle，合并 certifi + 系统 bundle。
    """

    explicit_bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if explicit_bundle:
        if not os.environ.get("NODE_EXTRA_CA_CERTS"):
            os.environ["NODE_EXTRA_CA_CERTS"] = explicit_bundle
        return Path(explicit_bundle)

    system = platform.system()
    if system == "Darwin":
        bundle = _build_ca_bundle(_read_macos_keychain_certificates())
    elif os.name == "nt" or system == "Windows":
        bundle = _build_ca_bundle(_read_windows_root_certificates())
    elif system == "Linux" or sys.platform.startswith("linux"):
        bundle = _build_ca_bundle(_read_linux_system_ca_bundle())
    else:
        return None

    if bundle is None:
        return None

    os.environ["REQUESTS_CA_BUNDLE"] = str(bundle)
    os.environ["SSL_CERT_FILE"] = str(bundle)
    if not os.environ.get("NODE_EXTRA_CA_CERTS"):
        os.environ["NODE_EXTRA_CA_CERTS"] = str(bundle)
    logger.info("已启用系统 TLS CA bundle: %s", bundle)
    return bundle


def _build_ca_bundle(extra_pem: str) -> Path | None:
    base_pem = _read_base_ca_bundle()
    certs = _dedupe_pem_certificates(base_pem + "\n" + (extra_pem or ""))
    if not certs:
        logger.warning("未能生成系统 TLS CA bundle：没有可用证书")
        return None

    LOCAL_CA_BUNDLE.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(certs) + "\n"
    if LOCAL_CA_BUNDLE.exists() and LOCAL_CA_BUNDLE.read_text(encoding="utf-8") == content:
        return LOCAL_CA_BUNDLE
    LOCAL_CA_BUNDLE.write_text(content, encoding="utf-8")
    return LOCAL_CA_BUNDLE


def _read_base_ca_bundle() -> str:
    try:
        import certifi  # type: ignore[import-not-found]

        return Path(certifi.where()).read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        cafile = ssl.get_default_verify_paths().cafile
        if not cafile:
            return ""
        try:
            return Path(cafile).read_text(encoding="utf-8")
        except OSError:
            return ""


def _read_macos_keychain_certificates() -> str:
    outputs: list[str] = []
    commands = [
        ["security", "find-certificate", "-a", "-p"],
        [
            "security",
            "find-certificate",
            "-a",
            "-p",
            "/System/Library/Keychains/SystemRootCertificates.keychain",
            "/Library/Keychains/System.keychain",
        ],
    ]
    for command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            logger.debug("读取 macOS Keychain 证书失败 command=%s: %s", command, exc)
            continue
        if result.returncode == 0 and result.stdout:
            outputs.append(result.stdout)
        else:
            logger.debug(
                "读取 macOS Keychain 证书返回非零 command=%s code=%s stderr=%s",
                command,
                result.returncode,
                (result.stderr or "").strip(),
            )
    return "\n".join(outputs)


def _read_windows_root_certificates() -> str:
    """通过 ssl 内置的 enum_certificates 读取 Windows 证书库；失败时尝试 certutil。"""
    blocks: list[str] = []
    try:
        for store in ("ROOT", "CA"):
            try:
                certs = ssl.enum_certificates(store)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                continue
            for cert_bytes, _enc, trust in certs or []:
                # trust=True 表示该证书在该 store 中被信任。
                if trust is False:
                    continue
                try:
                    pem = ssl.DER_cert_to_PEM_cert(cert_bytes)
                except Exception:  # noqa: BLE001
                    continue
                blocks.append(pem)
    except AttributeError:
        # 非 Windows 上 ssl.enum_certificates 不存在
        pass

    if blocks:
        return "\n".join(blocks)

    # 兜底：调用 certutil 导出根证书。
    try:
        result = subprocess.run(
            ["certutil", "-store", "Root"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("读取 Windows 根证书失败：%s", exc)
        return ""
    return result.stdout if result.returncode == 0 else ""


def _read_linux_system_ca_bundle() -> str:
    for candidate in _LINUX_CA_CANDIDATES:
        path = Path(candidate)
        if not path.exists():
            continue
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return ""


def _dedupe_pem_certificates(pem: str) -> list[str]:
    certs: list[str] = []
    seen: set[str] = set()
    start = 0
    while True:
        begin = pem.find(_PEM_BEGIN, start)
        if begin < 0:
            break
        end = pem.find(_PEM_END, begin)
        if end < 0:
            break
        end += len(_PEM_END)
        block = pem[begin:end].strip()
        start = end
        if block in seen:
            continue
        seen.add(block)
        certs.append(block)
    return certs
