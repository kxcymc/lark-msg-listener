"""claude_code_cli 的运行时支持与配置卡片支持。"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ...analysis_cli_types import AnalysisCliCardSupport
from .analyzer import ClaudeCodeCliAnalyzer, ClaudeCodeCliConfig
from ....common.ccr_gateway import (
    build_ccr_env,
    ensure_gateway_ready,
    load_ccr_config,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
MODEL_FIELD_KEY = "phase2.analysis_cli_model_name"
UNAVAILABLE_MODEL_VALUE = "__analysis_cli_model_unavailable__"
DEFAULT_MODEL_OPTIONS = ("sonnet", "opus", "haiku")
_INSTALL_PACKAGE = "@anthropic-ai/claude-code"


def _build_claude_config(cfg: dict) -> ClaudeCodeCliConfig:
    phase2_cfg = cfg.get("phase2") or {}
    claude_cfg = phase2_cfg.get("claude") or {}
    timeout = float(phase2_cfg.get("analysis_timeout_seconds", 240))
    args = claude_cfg.get("args") or ["-p", "--output-format", "json"]
    if not isinstance(args, list):
        args = ["-p", "--output-format", "json"]
    allowed_tools = claude_cfg.get("allowed_tools") or []
    if not isinstance(allowed_tools, list):
        allowed_tools = []
    disallowed_tools = claude_cfg.get("disallowed_tools") or [
        "Edit",
        "Write",
        "MultiEdit",
        "Bash",
    ]
    if not isinstance(disallowed_tools, list):
        disallowed_tools = ["Edit", "Write", "MultiEdit", "Bash"]
    ccr_cfg = load_ccr_config(cfg)
    return ClaudeCodeCliConfig(
        command=str(claude_cfg.get("command", "claude")) or "claude",
        args=[str(arg) for arg in args],
        prompt_via=str(claude_cfg.get("prompt_via", "argv")) or "argv",
        analysis_prompt_template=str(claude_cfg.get("analysis_prompt_template", "")),
        model_name=str(phase2_cfg.get("analysis_cli_model_name", "")),
        allowed_tools=[str(tool) for tool in allowed_tools if tool],
        disallowed_tools=[str(tool) for tool in disallowed_tools if tool],
        timeout_seconds=timeout,
        ccr_env=build_ccr_env(ccr_cfg),   # 注入 CCR 路由环境；analyzer 子进程据此连 CCR 网关
    )


def build_analyzer(cfg: dict) -> ClaudeCodeCliAnalyzer:
    return ClaudeCodeCliAnalyzer(_build_claude_config(cfg))


async def build_card_support(cfg: dict) -> AnalysisCliCardSupport:
    claude_config = _build_claude_config(cfg)
    command = claude_config.command
    if not _is_command_available(command):
        return AnalysisCliCardSupport(
            available=False,
            dynamic_select_options={MODEL_FIELD_KEY: (UNAVAILABLE_MODEL_VALUE,)},
            option_labels={MODEL_FIELD_KEY: {UNAVAILABLE_MODEL_VALUE: "暂不可用"}},
            unavailable_option_values={MODEL_FIELD_KEY: (UNAVAILABLE_MODEL_VALUE,)},
        )
    current_model = claude_config.model_name.strip()
    return AnalysisCliCardSupport(
        available=True,
        dynamic_select_options={MODEL_FIELD_KEY: DEFAULT_MODEL_OPTIONS},
        current_option_values={MODEL_FIELD_KEY: current_model},
    )


async def ensure_ready(cfg: dict) -> None:
    claude_config = _build_claude_config(cfg)
    command = claude_config.command
    if not _is_command_available(command) and not _install_claude_code_cli():
        sys.exit(1)
    if not _is_command_available(command):
        logger.error("Claude Code CLI 安装后仍未在 PATH 中检测到 `%s`。", command)
        sys.exit(1)

    # 命令可用后先保活 CCR 网关；analyzer 子进程依赖网关路由，未就绪则直接退出
    try:
        ensure_gateway_ready(load_ccr_config(cfg))
    except RuntimeError as exc:
        logger.error("CCR 网关未就绪：%s", exc)
        sys.exit(1)

    configured = claude_config.model_name.strip()
    if not configured:
        logger.error(
            "phase2.analysis_cli_model_name 未配置。\n"
            "请发送 `/config` 打开配置卡片后选择或填写 Claude 模型。"
        )
        sys.exit(1)
    logger.info("Claude Code CLI 已就绪，当前 model_name=%r", configured)


def _is_command_available(command: str) -> bool:
    return shutil.which(command) is not None


def _resolve_npm_argv() -> list[str] | None:
    """Resolve the npm executable on the current platform.

    Windows 上 `subprocess.run(["npm", ...])` 不会自动解析 `npm.cmd`，
    会抛 `FileNotFoundError`。这里返回带扩展名的绝对路径，找不到则返回 None。
    """
    resolved = shutil.which("npm")
    if resolved:
        return [resolved]
    if os.name == "nt":
        for candidate in ("npm.cmd", "npm.exe", "npm.bat"):
            located = shutil.which(candidate)
            if located:
                return [located]
    return None


def _install_claude_code_cli() -> bool:
    logger.info("未检测到 Claude Code CLI，开始通过 npm 自动安装 %s", _INSTALL_PACKAGE)
    npm_argv = _resolve_npm_argv()
    if npm_argv is None:
        logger.error(
            "自动安装 Claude Code CLI 失败：未找到 npm。\n"
            "请先安装 Node.js/npm 后重试，或手动执行：npm install -g %s",
            _INSTALL_PACKAGE,
        )
        return False
    try:
        res = subprocess.run(
            [*npm_argv, "install", "-g", _INSTALL_PACKAGE],
            cwd=PROJECT_ROOT,
            check=False,
        )
    except FileNotFoundError as exc:
        logger.error(
            "自动安装 Claude Code CLI 失败：%s\n"
            "请手动执行：npm install -g %s",
            exc,
            _INSTALL_PACKAGE,
        )
        return False
    if res.returncode != 0:
        logger.error(
            "自动安装 Claude Code CLI 失败，退出码=%s。\n"
            "请手动执行：npm install -g %s",
            res.returncode,
            _INSTALL_PACKAGE,
        )
        return False
    logger.info("Claude Code CLI 安装命令执行完成")
    return True
