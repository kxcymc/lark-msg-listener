"""claude_code_cli 配置与错误码。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


CLAUDE_CODE_CLI_DEFAULT_COMMAND = "claude"
CLAUDE_CODE_CLI_DEFAULT_OUTPUT_FORMAT = "json"
CLAUDE_CODE_CLI_DEFAULT_STATE_DIR = "out/claude_code_cli_tasks"
CLAUDE_CODE_CLI_PERMISSION_MODE = "bypassPermissions"
# 固定拒绝高风险 Bash 指令：进程终止、强制递归删除、提权，以及常见 Windows 等价命令。
CLAUDE_CODE_CLI_DISALLOWED_TOOLS = (
    "Bash(kill *)",
    "Bash(pkill *)",
    "Bash(killall *)",
    "Bash(shutdown *)",
    "Bash(reboot *)",
    "Bash(halt *)",
    "Bash(sudo *)",
    "Bash(doas *)",
    "Bash(su *)",
    "Bash(rm -rf *)",
    "Bash(rm -fr *)",
    "Bash(rm -r -f *)",
    "Bash(rm -f -r *)",
    "Bash(taskkill *)",
    "Bash(Stop-Process *)",
    "Bash(stop-process *)",
    "Bash(Stop-Computer *)",
    "Bash(stop-computer *)",
    "Bash(Restart-Computer *)",
    "Bash(restart-computer *)",
    "Bash(Remove-Item -Recurse -Force *)",
    "Bash(Remove-Item -Force -Recurse *)",
    "Bash(remove-item -recurse -force *)",
    "Bash(remove-item -force -recurse *)",
    "Bash(del /f /s /q *)",
    "Bash(erase /f /s /q *)",
    "Bash(rd /s /q *)",
    "Bash(rmdir /s /q *)",
    "Bash(runas *)",
    "Bash(Start-Process * -Verb RunAs*)",
    "Bash(start-process * -verb runas*)",
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
    state_dir: Path = Path(CLAUDE_CODE_CLI_DEFAULT_STATE_DIR)
    submit_timeout_seconds: float = 30.0
    # CCR 网关配置：executor 把这两项写入 worker metadata，
    # 让独立 worker 子进程据此把 claude 请求路由到 CCR（worker 不读 config.toml）。
    ccr_enabled: bool = True
    ccr_base_url: str = "http://127.0.0.1:3456"
