"""kxcymc_cli 的运行时支持与配置卡片支持。"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ...analysis_cli_types import AnalysisCliCardSupport
from .analyzer import kxcymcCliAnalyzer, kxcymcCliConfig
from .models import kxcymcModelsError, discover_models, is_kxcymc_available

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
MODEL_FIELD_KEY = "phase2.kxcymc.model"
UNAVAILABLE_MODEL_VALUE = "__analysis_cli_model_unavailable__"
_kxcymc_INSTALL_MAC_LINUX = (
    'sh -c "$(curl -L https://kxcymc.com/download.sh)" '
    "&& export PATH=~/.local/bin:$PATH"
)
_kxcymc_INSTALL_WINDOWS = (
    "irm https://kxcymc.com/download.ps1 | iex"
)


def _build_kxcymc_config(cfg: dict) -> kxcymcCliConfig:
    phase2_cfg = cfg.get("phase2") or {}
    kxcymc_cfg = phase2_cfg.get("kxcymc") or {}
    timeout = float(phase2_cfg.get("analysis_timeout_seconds", 240))
    args = kxcymc_cfg.get("args") or ["--json", "-p"]
    if not isinstance(args, list):
        args = ["--json", "-p"]
    allowed_tools = kxcymc_cfg.get("allowed_tools") or []
    disallowed_tools = kxcymc_cfg.get("disallowed_tools") or [
        "Edit",
        "Write",
        "MultiEdit",
        "Bash",
    ]
    return kxcymcCliConfig(
        command=str(kxcymc_cfg.get("command", "kxcymc")) or "kxcymc",
        args=[str(arg) for arg in args],
        prompt_via=str(kxcymc_cfg.get("prompt_via", "argv")) or "argv",
        analysis_prompt_template=str(kxcymc_cfg.get("analysis_prompt_template", "")),
        model_name=str(kxcymc_cfg.get("model", "")),
        allowed_tools=[str(tool) for tool in allowed_tools if tool],
        disallowed_tools=[str(tool) for tool in disallowed_tools if tool],
        timeout_seconds=timeout,
    )


def build_analyzer(cfg: dict) -> kxcymcCliAnalyzer:
    return kxcymcCliAnalyzer(_build_kxcymc_config(cfg))


async def build_card_support(cfg: dict) -> AnalysisCliCardSupport:
    command = _build_kxcymc_config(cfg).command
    if not is_kxcymc_available(command):
        _ensure_local_bin_in_path()
    if not is_kxcymc_available(command):
        return AnalysisCliCardSupport(
            available=False,
            dynamic_select_options={MODEL_FIELD_KEY: (UNAVAILABLE_MODEL_VALUE,)},
            option_labels={MODEL_FIELD_KEY: {UNAVAILABLE_MODEL_VALUE: "暂不可用"}},
            unavailable_option_values={MODEL_FIELD_KEY: (UNAVAILABLE_MODEL_VALUE,)},
        )
    try:
        result = await discover_models(command=command, cwd=PROJECT_ROOT)
    except kxcymcModelsError as exc:
        logger.warning("获取 kxcymc 模型列表失败，配置卡片将标记模型暂不可用：%s", exc)
        return AnalysisCliCardSupport(
            available=False,
            dynamic_select_options={MODEL_FIELD_KEY: (UNAVAILABLE_MODEL_VALUE,)},
            option_labels={MODEL_FIELD_KEY: {UNAVAILABLE_MODEL_VALUE: "暂不可用"}},
            unavailable_option_values={MODEL_FIELD_KEY: (UNAVAILABLE_MODEL_VALUE,)},
        )
    if not result.models:
        return AnalysisCliCardSupport(
            available=False,
            dynamic_select_options={MODEL_FIELD_KEY: (UNAVAILABLE_MODEL_VALUE,)},
            option_labels={MODEL_FIELD_KEY: {UNAVAILABLE_MODEL_VALUE: "暂不可用"}},
            unavailable_option_values={MODEL_FIELD_KEY: (UNAVAILABLE_MODEL_VALUE,)},
        )
    configured_model = str(((cfg.get("phase2") or {}).get("kxcymc") or {}).get("model", "")).strip()
    return AnalysisCliCardSupport(
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
        logger.error(
            "kxcymc 当前没有可用模型，请确认插件已安装并启用：`kxcymc plugin list`"
        )
        sys.exit(1)

    configured = kxcymc_config.model_name.strip()
    if not configured:
        logger.error(
            "phase2.kxcymc.model 未配置。\n可用模型：%s\n"
            "请发送 `/config` 打开配置卡片后选择合法候选值。",
            ", ".join(result.names),
        )
        sys.exit(1)
    if result.find(configured) is None:
        logger.error(
            "phase2.kxcymc.model=%s 不在 kxcymc 当前可用模型列表中。\n"
            "可用模型：%s\n"
            "请发送 `/config` 打开配置卡片后切换为合法候选值。",
            configured,
            ", ".join(result.names),
        )
        sys.exit(1)

    logger.info(
        "已发现 %d 个 kxcymc 模型，当前 model_name=%r",
        len(result.models),
        configured,
    )


def _format_install_hint() -> str:
    return (
        "请安装 kxcymc CLI 后再启动服务：\n"
        f"  - macOS / Linux: {_kxcymc_INSTALL_MAC_LINUX}\n"
        f"  - Windows: {_kxcymc_INSTALL_WINDOWS}"
    )


def _ensure_local_bin_in_path() -> None:
    candidates: list[str] = [str(Path.home() / ".local" / "bin")]
    if sys.platform.startswith("win"):
        # Windows 上 kxcymc 安装产物常见目录候选。
        userprofile = os.environ.get("USERPROFILE") or str(Path.home())
        local_appdata = os.environ.get("LOCALAPPDATA") or str(Path(userprofile) / "AppData" / "Local")
        candidates.extend(
            [
                str(Path(userprofile) / ".local" / "bin"),
                str(Path(userprofile) / ".kxcymc" / "bin"),
                str(Path(local_appdata) / "Programs" / "kxcymc"),
            ]
        )
    current = os.environ.get("PATH", "")
    entries = current.split(os.pathsep) if current else []
    additions = [c for c in candidates if c and c not in entries]
    if not additions:
        return
    os.environ["PATH"] = os.pathsep.join([*additions, *entries]) if entries else os.pathsep.join(additions)


def _install_kxcymc_cli(command: str) -> bool:
    logger.info("未检测到 %s，开始自动安装 kxcymc CLI", command)
    if sys.platform.startswith("win"):
        # 选择 PowerShell 7 (pwsh) 优先；回退到 Windows PowerShell。
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
        res = subprocess.run(install_argv, cwd=PROJECT_ROOT, check=False)
    except FileNotFoundError as exc:
        logger.error("自动安装 kxcymc CLI 失败：%s\n%s", exc, _format_install_hint())
        return False
    if res.returncode != 0:
        logger.error(
            "自动安装 kxcymc CLI 失败，退出码=%s。\n%s",
            res.returncode,
            _format_install_hint(),
        )
        return False
    _ensure_local_bin_in_path()
    if is_kxcymc_available(command):
        logger.info("kxcymc CLI 安装完成，已继续启动流程")
        return True
    logger.error("kxcymc CLI 安装完成后仍未在 PATH 中检测到 `%s`。", command)
    return False
