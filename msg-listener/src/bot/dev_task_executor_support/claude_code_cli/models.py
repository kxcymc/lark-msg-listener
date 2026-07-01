"""claude_code_cli 配置与错误码。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ....common.claude_code_cli import (
    CLAUDE_CODE_CLI_DEFAULT_COMMAND,
    CLAUDE_CODE_CLI_DEFAULT_OUTPUT_FORMAT,
    CLAUDE_CODE_CLI_DEV_TASK_STATE_DIR,
)

ERROR_REPO_NOT_FOUND = "repo_not_found"
ERROR_DIRTY_WORKTREE = "dirty_worktree"
ERROR_CLAUDE_NOT_FOUND = "claude_not_found"
ERROR_PERMISSION_DENIED = "permission_denied"
ERROR_CLAUDE_CLI_FAILED = "claude_cli_failed"
ERROR_TASK_NOT_FOUND = "task_not_found"
ERROR_EVENT_TIMEOUT = "event_timeout"
ERROR_INVALID_JSON_OUTPUT = "invalid_json_output"


@dataclass(frozen=True)
class ClaudeCodeCliConfig:
    """claude_code_cli 执行器运行时配置。"""

    command: str = CLAUDE_CODE_CLI_DEFAULT_COMMAND
    model: str = ""
    output_format: str = CLAUDE_CODE_CLI_DEFAULT_OUTPUT_FORMAT
    disallowed_tools: tuple[str, ...] = field(default_factory=tuple)
    state_dir: Path = Path(CLAUDE_CODE_CLI_DEV_TASK_STATE_DIR)
    submit_timeout_seconds: float = 30.0
    # CCR 网关配置：executor 把这两项写入 worker metadata，
    # 让独立 worker 子进程据此把 claude 请求路由到 CCR（worker 不读 config.toml）。
    ccr_enabled: bool = True
    ccr_base_url: str = "http://127.0.0.1:3456"
