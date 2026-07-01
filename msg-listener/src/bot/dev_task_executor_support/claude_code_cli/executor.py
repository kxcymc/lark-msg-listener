"""ClaudeCodeCliExecutor：提交本地 worker 并轮询持久化结果文件。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from ...dev_tasks.prompt import build_cli_dev_prompt
from ..types import TaskRequest, TaskSnapshot, TaskStatus
from .models import (
    ERROR_CLAUDE_NOT_FOUND,
    ERROR_EVENT_TIMEOUT,
    ERROR_REPO_NOT_FOUND,
    ERROR_TASK_NOT_FOUND,
    ClaudeCodeCliConfig,
)

logger = logging.getLogger(__name__)


class ClaudeCodeCliError(RuntimeError):
    """claude_code_cli 通用错误，error_code 用于分类写入 dev_tasks.error。"""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(f"{error_code}: {message}")
        self.error_code = error_code
        self.message = message


class ClaudeCodeCliExecutor:
    """本地 Claude Code CLI 执行器。"""

    def __init__(self, config: ClaudeCodeCliConfig) -> None:
        self.config = config

    async def submit(self, request: TaskRequest) -> str:
        repo_path = Path(request.repo_local_path).expanduser()
        if not request.repo_local_path or not repo_path.is_dir():
            raise ClaudeCodeCliError(
                ERROR_REPO_NOT_FOUND,
                f"仓库本地路径不存在 repo={request.repo_id} path={request.repo_local_path}",
            )
        executor_task_id = f"claude_{uuid.uuid4().hex}"
        task_dir = self.config.state_dir / executor_task_id
        task_dir.mkdir(parents=True, exist_ok=False)
        metadata_path = task_dir / "metadata.json"
        result_path = task_dir / "result.json"
        stdout_path = task_dir / "stdout.log"
        stderr_path = task_dir / "stderr.log"
        prompt = build_cli_dev_prompt(
            base_branch=request.base_branch,
            work_branch_hint=request.work_branch_hint,
            requirement_summary=request.requirement_summary,
            requirement_detail=request.requirement_detail,
        )
        metadata = {
            "executor_task_id": executor_task_id,
            "repo_id": request.repo_id,
            "repo_local_path": str(repo_path),
            "base_branch": request.base_branch,
            "requirement_summary": request.requirement_summary,
            "requirement_detail": request.requirement_detail,
            "media_files": request.media_files,
            "command": self.config.command,
            "model": self.config.model,
            "output_format": self.config.output_format,
            "disallowed_tools": list(self.config.disallowed_tools),
            "prompt": prompt,
            "created_at": time.time(),
            # CCR 路由信息：worker 子进程据此构造 claude env，避免 worker 直接读 config.toml。
            "ccr_enabled": self.config.ccr_enabled,
            "ccr_base_url": self.config.ccr_base_url,
        }
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        cmd = [
            sys.executable,
            "-m",
            "src.bot.dev_task_executor_support.claude_code_cli.worker",
            "--metadata",
            str(metadata_path),
            "--result",
            str(result_path),
            "--stdout",
            str(stdout_path),
            "--stderr",
            str(stderr_path),
        ]
        project_root = Path(__file__).resolve().parents[4]
        popen_kwargs: dict[str, Any] = {
            "cwd": str(project_root),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "env": {**os.environ, "PYTHONUNBUFFERED": "1"},
        }
        if os.name == "nt":
            # Windows 没有 setsid；用 DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP
            # 让 worker 脱离父进程的控制台并独立成组，避免父进程 Ctrl-C 牵连。
            popen_kwargs["creationflags"] = (
                getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
        else:
            popen_kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)
        except FileNotFoundError as exc:
            raise ClaudeCodeCliError(
                ERROR_CLAUDE_NOT_FOUND,
                f"无法启动 Python worker: {exc}",
            ) from exc
        (task_dir / "process.json").write_text(
            json.dumps(
                {
                    "pid": proc.pid,
                    "started_at": time.time(),
                    "metadata": str(metadata_path),
                    "result": str(result_path),
                    "stdout": str(stdout_path),
                    "stderr": str(stderr_path),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info(
            "Claude Code CLI worker 已启动 demand=%s executor_task=%s pid=%s repo=%s",
            request.demand_id,
            executor_task_id,
            proc.pid,
            request.repo_id,
        )
        return executor_task_id

    async def wait(self, task_id: str, timeout_minutes: float) -> TaskSnapshot:
        task_dir = self.config.state_dir / task_id
        result_path = task_dir / "result.json"
        metadata_path = task_dir / "metadata.json"
        if not task_dir.is_dir():
            return TaskSnapshot(
                task_id=task_id,
                status="failed",
                error=ERROR_TASK_NOT_FOUND,
            )
        timeout_seconds = max(1.0, float(timeout_minutes) * 60.0)
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if result_path.is_file():
                return _snapshot_from_result(
                    task_id=task_id,
                    result=_read_json(result_path),
                    metadata=_read_json(metadata_path),
                )
            await asyncio.sleep(2.0)
        raise ClaudeCodeCliError(
            ERROR_EVENT_TIMEOUT,
            f"等待 Claude Code CLI 任务结果超时 task_id={task_id}",
        )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _snapshot_from_result(
    *,
    task_id: str,
    result: dict[str, Any],
    metadata: dict[str, Any],
) -> TaskSnapshot:
    raw_status = str(result.get("status") or "").lower()
    status: TaskStatus = "succeeded" if raw_status == "succeeded" else "failed"
    error = "" if status == "succeeded" else str(result.get("error") or "claude_cli_failed")
    summary = str(result.get("summary") or "")
    changed_files = result.get("changed_files")
    if isinstance(changed_files, list) and changed_files and not summary:
        summary = "已修改文件：" + ", ".join(str(item) for item in changed_files[:20])
    return TaskSnapshot(
        task_id=task_id,
        status=status,
        base_branch=str(metadata.get("base_branch") or result.get("base_branch") or ""),
        work_branch=str(result.get("work_branch") or metadata.get("work_branch_hint") or ""),
        mr_url="",
        commit_sha=str(result.get("commit_sha") or ""),
        summary=summary,
        error=error,
    )
