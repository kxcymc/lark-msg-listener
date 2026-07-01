"""Dev task executor that opens Trae side chat with the generated prompt."""
from __future__ import annotations

import asyncio
import logging
import uuid

from ...dev_tasks.prompt import build_cli_dev_prompt
from ...trae_side_chat import open_trae_side_chat
from ..types import TaskRequest, TaskSnapshot

logger = logging.getLogger(__name__)


class TraeSideChatExecutor:
    is_local_executor = True

    async def submit(self, request: TaskRequest) -> str:
        prompt = build_cli_dev_prompt(
            base_branch=request.base_branch,
            work_branch_hint=request.work_branch_hint,
            requirement_summary=request.requirement_summary,
            requirement_detail=request.requirement_detail,
        )
        prompt = _append_media_paths(prompt, request.media_files)
        task_id = f"trae_side_chat_{uuid.uuid4().hex[:16]}"
        task = asyncio.create_task(
            open_trae_side_chat(prompt, repo_path=request.repo_local_path),
            name=f"open-trae-side-chat-{request.demand_id}",
        )
        task.add_done_callback(
            lambda done: _log_open_result(done, request.demand_id, task_id)
        )
        logger.info(
            "Trae 侧聊打开任务已提交 demand=%s task=%s repo=%s",
            request.demand_id,
            task_id,
            request.repo_local_path or request.repo_id,
        )
        return task_id

    async def wait(self, task_id: str, timeout_minutes: float) -> TaskSnapshot:
        del timeout_minutes
        return TaskSnapshot(
            task_id=task_id,
            status="succeeded",
            summary="已打开 Trae 侧聊，请在 Trae 中继续开发。",
        )


def _append_media_paths(prompt: str, media_files: list[dict]) -> str:
    paths = [
        str(item.get("local_path") or "").strip()
        for item in media_files
        if isinstance(item, dict) and str(item.get("local_path") or "").strip()
    ]
    if not paths:
        return prompt
    lines = "\n".join(f"- {path}" for path in paths)
    return f"{prompt}\n\n需求关联素材文件：\n{lines}"


def _log_open_result(done: asyncio.Task[None], demand_id: str, task_id: str) -> None:
    try:
        done.result()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Trae 侧聊打开失败 demand=%s task=%s: %s", demand_id, task_id, exc)
        return
    logger.info("Trae 侧聊已打开 demand=%s task=%s", demand_id, task_id)
