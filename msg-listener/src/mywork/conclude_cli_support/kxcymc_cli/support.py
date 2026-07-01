"""Runtime and config-card support for kxcymc mywork conclusion."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from src.bot.analysis_cli_support.kxcymc_cli.models import (
    kxcymcModelsError,
    discover_models,
    is_kxcymc_available,
)
from src.mywork.conclude_cli_types import ConcludeCliCardSupport

from .concluder import kxcymcCliConcluder, kxcymcConcludeCliConfig

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
MODEL_FIELD_KEY = "mywork.conclude_cli_model_name"
UNAVAILABLE_MODEL_VALUE = "__mywork_conclude_cli_model_unavailable__"
_kxcymc_INSTALL_MAC_LINUX = (
    'sh -c "$(curl -L https://kxcymc.com/download.sh)" '
    "&& export PATH=~/.local/bin:$PATH"
)
_kxcymc_INSTALL_WINDOWS = "irm https://kxcymc.com/download.ps1 | iex"


def _build_kxcymc_config(cfg: dict) -> kxcymcConcludeCliConfig:
    mywork_cfg = cfg.get("mywork") or {}
    providers = mywork_cfg.get("conclude_cli_providers") or {}
    kxcymc_cfg = providers.get("kxcymc_cli") if isinstance(providers, dict) else {}
    if not isinstance(kxcymc_cfg, dict):
        kxcymc_cfg = {}
    return kxcymcConcludeCliConfig(
        command=str(kxcymc_cfg.get("command", "kxcymc")) or "kxcymc",
        model_name=str(mywork_cfg.get("conclude_cli_model_name", "")),
        timeout_seconds=float(kxcymc_cfg.get("timeout_seconds", 3600)),
        cwd=str(PROJECT_ROOT),
    )


def build_concluder(cfg: dict) -> kxcymcCliConcluder:
    return kxcymcCliConcluder(_build_kxcymc_config(cfg))


async def build_card_support(cfg: dict) -> ConcludeCliCardSupport:
    command = _build_kxcymc_config(cfg).command
    if not is_kxcymc_available(command):
        _ensure_local_bin_in_path()
    if not is_kxcymc_available(command):
        return _unavailable_support()
    try:
        result = await discover_models(command=command, cwd=PROJECT_ROOT)
    except kxcymcModelsError as exc:
        logger.warning("获取 kxcymc 模型列表失败，mywork 配置卡片将标记模型暂不可用：%s", exc)
        return _unavailable_support()
    if not result.models:
        return _unavailable_support()
    configured_model = str((cfg.get("mywork") or {}).get("conclude_cli_model_name", "")).strip()
    return ConcludeCliCardSupport(
        available=True,
        dynamic_select_options={MODEL_FIELD_KEY: tuple(result.names)},
        current_option_values={MODEL_FIELD_KEY: configured_model},
    )


async def ensure_ready(cfg: dict) -> None:
    kxcymc_config = _build_kxcymc_config(cfg)
    command = kxcymc_config.command
    if not is_kxcymc_available(command):
        _ensure_local_bin_in_path()
    if not is_kxcymc_available(command) and not _install_kxcymc_cli(command):
        logger.error("请手动安装 kxcymc CLI 后重试。\n%s", _format_install_hint())
        sys.exit(1)

    try:
        result = await discover_models(command=command, cwd=PROJECT_ROOT)
    except kxcymcModelsError as exc:
        logger.error("获取 kxcymc 模型列表失败：%s\n%s", exc, _format_install_hint())
        sys.exit(1)
    if not result.models:
        logger.error("kxcymc 当前没有可用模型，请确认插件已安装并启用：`kxcymc plugin list`")
        sys.exit(1)
    configured = kxcymc_config.model_name.strip()
    if not configured:
        logger.error(
            "mywork.conclude_cli_model_name 未配置。\n可用模型：%s\n请发送 `/config` 选择合法候选值。",
            ", ".join(result.names),
        )
        sys.exit(1)
    if result.find(configured) is None:
        logger.error(
            "mywork.conclude_cli_model_name=%s 不在 kxcymc 当前可用模型列表中。\n可用模型：%s",
            configured,
            ", ".join(result.names),
        )
        sys.exit(1)


def _unavailable_support() -> ConcludeCliCardSupport:
    return ConcludeCliCardSupport(
        available=False,
        dynamic_select_options={MODEL_FIELD_KEY: (UNAVAILABLE_MODEL_VALUE,)},
        option_labels={MODEL_FIELD_KEY: {UNAVAILABLE_MODEL_VALUE: "暂不可用"}},
        unavailable_option_values={MODEL_FIELD_KEY: (UNAVAILABLE_MODEL_VALUE,)},
    )


def _format_install_hint() -> str:
    return (
        "请安装 kxcymc CLI 后再启动服务：\n"
        f"  - macOS / Linux: {_kxcymc_INSTALL_MAC_LINUX}\n"
        f"  - Windows: {_kxcymc_INSTALL_WINDOWS}"
    )


def _ensure_local_bin_in_path() -> None:
    candidates = [str(Path.home() / ".local" / "bin")]
    if sys.platform.startswith("win"):
        userprofile = os.environ.get("USERPROFILE") or str(Path.home())
        local_appdata = os.environ.get("LOCALAPPDATA") or str(Path(userprofile) / "AppData" / "Local")
        candidates.extend(
            [
                str(Path(userprofile) / ".local" / "bin"),
                str(Path(userprofile) / ".kxcymc" / "bin"),
                str(Path(local_appdata) / "Programs" / "kxcymc"),
            ]
        )
    entries = os.environ.get("PATH", "").split(os.pathsep)
    additions = [candidate for candidate in candidates if candidate and candidate not in entries]
    if additions:
        os.environ["PATH"] = os.pathsep.join([*additions, *entries])


def _install_kxcymc_cli(command: str) -> bool:
    logger.info("未检测到 %s，开始自动安装 kxcymc CLI", command)
    if sys.platform.startswith("win"):
        shell = "pwsh" if shutil.which("pwsh") else "powershell"
        install_argv = [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            _kxcymc_INSTALL_WINDOWS,
        ]
    else:
        install_argv = ["sh", "-c", _kxcymc_INSTALL_MAC_LINUX]
    try:
        result = subprocess.run(install_argv, cwd=PROJECT_ROOT, check=False)
    except FileNotFoundError as exc:
        logger.error("自动安装 kxcymc CLI 失败：%s\n%s", exc, _format_install_hint())
        return False
    if result.returncode != 0:
        logger.error("自动安装 kxcymc CLI 失败，退出码=%s。\n%s", result.returncode, _format_install_hint())
        return False
    _ensure_local_bin_in_path()
    return is_kxcymc_available(command)
