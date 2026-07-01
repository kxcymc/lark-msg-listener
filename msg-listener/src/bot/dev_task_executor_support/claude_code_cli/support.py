"""claude_code_cli 启动期校验与 /config 卡片支持。"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from ..types import DevTaskExecutorCardSupport
from .executor import ClaudeCodeCliExecutor
from .models import (
    CLAUDE_CODE_CLI_DEFAULT_COMMAND,
    CLAUDE_CODE_CLI_DEFAULT_OUTPUT_FORMAT,
    CLAUDE_CODE_CLI_DEFAULT_STATE_DIR,
    ClaudeCodeCliConfig,
)
from ....common.ccr_gateway import ensure_gateway_ready, load_ccr_config

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[4]


def _project_path(value: str) -> Path:
    text = str(value or CLAUDE_CODE_CLI_DEFAULT_STATE_DIR)
    path = Path(text).expanduser()
    if path.is_absolute():
        return path
    return (PROJECT_ROOT / path).resolve()


def _build_config(cfg: dict) -> ClaudeCodeCliConfig:
    dev_task_cfg = ((cfg.get("phase3") or {}).get("dev_task") or {})
    cli_cfg = dev_task_cfg.get("claude_code_cli") or {}
    # 读取顶层 [ccr]，把网关开关与地址固化进配置，供 executor 写入 worker metadata。
    ccr_cfg = load_ccr_config(cfg)
    return ClaudeCodeCliConfig(
        command=str(cli_cfg.get("command", CLAUDE_CODE_CLI_DEFAULT_COMMAND))
        or CLAUDE_CODE_CLI_DEFAULT_COMMAND,
        model=str(cli_cfg.get("model", "")),
        output_format=str(
            cli_cfg.get("output_format", CLAUDE_CODE_CLI_DEFAULT_OUTPUT_FORMAT)
        )
        or CLAUDE_CODE_CLI_DEFAULT_OUTPUT_FORMAT,
        state_dir=_project_path(
            str(cli_cfg.get("state_dir", CLAUDE_CODE_CLI_DEFAULT_STATE_DIR)),
        ),
        submit_timeout_seconds=float(dev_task_cfg.get("submit_timeout_seconds", 30)),
        ccr_enabled=ccr_cfg.enabled,
        ccr_base_url=ccr_cfg.base_url,
    )


def build_executor(cfg: dict) -> ClaudeCodeCliExecutor:
    return ClaudeCodeCliExecutor(_build_config(cfg))


async def build_card_support(cfg: dict) -> DevTaskExecutorCardSupport:
    config = _build_config(cfg)
    return DevTaskExecutorCardSupport(
        available=bool(config.command.strip()) and shutil.which(config.command) is not None
    )


async def ensure_ready(cfg: dict) -> None:
    config = _build_config(cfg)
    if not config.command.strip():
        logger.warning("phase3.dev_task.claude_code_cli.command 为空，请通过 /config 修正")
        return
    if shutil.which(config.command) is None:
        logger.warning(
            "未在 PATH 中找到 Claude CLI：%s。服务将继续启动，以便通过 /config 修正。",
            config.command,
        )
    config.state_dir.mkdir(parents=True, exist_ok=True)
    logger.debug("claude_code_cli 状态目录：%s", config.state_dir)
    # 执行器侧风格：保活失败不阻断启动，仅告警，运行期 worker 会回退直连。
    try:
        ensure_gateway_ready(load_ccr_config(cfg))
    except RuntimeError as exc:
        logger.warning("CCR 网关未就绪（开发任务执行将回退直连）：%s", exc)
