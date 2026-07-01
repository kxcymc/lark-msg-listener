"""DevTaskOrchestrator：管理后台任务生命周期、重试、并发与恢复。"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger as _logger

from ..analysis.record_repository import (
    DEMAND_DEV_CONFIRMED,
    RecordRepository,
)
from ..dev_task_executor_support.types import (
    DevTaskExecutor,
    TaskRequest,
    TaskSnapshot,
)
from .models import DevTask
from .repository import DevTaskRepository

if TYPE_CHECKING:
    from ..git_inventory.repository import RepoInventoryRepository
    from ..notifications.bubble_notifier import BubbleNotifier

logger = logging.getLogger(__name__)

NON_RETRYABLE_ERRORS: set[str] = {
    "repo_not_found",
    "dirty_worktree",
    "claude_not_found",
    "permission_denied",
    "task_not_found",
}


class DevTaskConcurrencyLimitError(RuntimeError):
    """Raised when the selected executor has reached max active dev tasks."""


class DevTaskRepoBusyError(RuntimeError):
    """Raised when another active dev task already owns the selected repo."""


@dataclass
class DevTaskOrchestratorConfig:
    max_concurrency: int = 3
    max_attempts: int = 3
    max_wait_minutes: float = 60.0
    retry_backoff_base_seconds: float = 5.0
    startup_submit_timeout_seconds: float = 30.0


class DevTaskOrchestrator:
    """编排开发任务：派发 + 等待事件终态 + 重试 + 终态写库 + 气泡通知。

    生命周期与 bot-agent 进程对齐；通过 stop() 优雅终止后台任务。
    """

    def __init__(
        self,
        *,
        executor: DevTaskExecutor,
        record_repository: RecordRepository,
        dev_task_repository: DevTaskRepository,
        bubble_notifier: "BubbleNotifier",
        config: DevTaskOrchestratorConfig,
        repo_inventory_repository: "RepoInventoryRepository | None" = None,
    ) -> None:
        self.executor = executor
        self.record_repository = record_repository
        self.dev_task_repository = dev_task_repository
        self.bubble_notifier = bubble_notifier
        self.config = config
        self.repo_inventory_repository = repo_inventory_repository
        self._semaphore = asyncio.Semaphore(max(1, config.max_concurrency))
        self._tasks: set[asyncio.Task[None]] = set()
        self._stopping = False

    # ---------- 公共接口 ----------

    @property
    def is_local_executor(self) -> bool:
        return bool(getattr(self.executor, "is_local_executor", False))

    async def dispatch(
        self,
        *,
        demand_id: str,
        repo_id: str,
        base_branch: str,
        instance_id: str,
    ) -> str:
        """提交执行器成功后才返回，保证卡片关闭时后台任务已开始工作。"""
        if self._stopping:
            raise RuntimeError("DevTaskOrchestrator 已停止，无法派发新任务")
        task_id = f"dt_{uuid.uuid4().hex[:16]}"
        task = DevTask(
            task_id=task_id,
            demand_id=demand_id,
            card_instance_id=instance_id,
            repo_id=repo_id,
            base_branch=base_branch,
            status="queued",
            attempts=0,
            max_attempts=self.config.max_attempts,
        )
        insert_result = self.dev_task_repository.try_insert_task_with_capacity_and_repo_idle(
            task,
            max_active=self.config.max_concurrency,
        )
        if insert_result == "capacity_full":
            raise DevTaskConcurrencyLimitError("当前任务并发数已达上限，请稍后重试")
        if insert_result == "repo_busy":
            raise DevTaskRepoBusyError("同仓库下其他需求正在开发，请稍等")
        self.dev_task_repository.update_dispatch_status(
            demand_id=demand_id,
            dispatch_status="dispatching",
            current_task_id=task_id,
        )
        try:
            executor_task_id = await self._submit_task(task_id)
        except Exception:
            self.dev_task_repository.update_dispatch_status(
                demand_id=demand_id,
                dispatch_status="failed",
                current_task_id=task_id,
            )
            raise
        if self.is_local_executor:
            snapshot = TaskSnapshot(
                task_id=executor_task_id,
                status="succeeded",
                summary="已打开 Trae 侧聊，请在 Trae 中继续开发。",
            )
            self.dev_task_repository.update_status(
                task_id=task_id,
                status="succeeded",
                raw_snapshot_json=_dump_snapshot(snapshot),
                error="",
            )
            self.dev_task_repository.update_dispatch_status(
                demand_id=demand_id,
                dispatch_status="local_succeeded",
                current_task_id=task_id,
            )
            self.record_repository.update_demand_status(demand_id, DEMAND_DEV_CONFIRMED)
            logger.info(
                "本地开发任务已完成 demand=%s task=%s repo=%s base=%s",
                demand_id,
                task_id,
                repo_id,
                base_branch,
            )
            return task_id
        # 只有执行器确认接单后才进入 dev_confirmed。
        self.record_repository.update_demand_status(demand_id, DEMAND_DEV_CONFIRMED)
        # 接单成功后再发送派发气泡，避免误报。
        self._spawn_notify_dispatched(demand_id=demand_id, task_id=task_id)
        self._spawn_task(task_id)
        logger.info(
            "已派发开发任务 demand=%s task=%s repo=%s base=%s",
            demand_id,
            task_id,
            repo_id,
            base_branch,
        )
        return task_id

    async def resume_pending_tasks(self) -> int:
        rows = self.dev_task_repository.list_pending_tasks()
        if not rows:
            return 0
        # running 状态先回退为 queued，避免重复计数 attempts。
        for row in rows:
            if row["status"] == "running":
                self.dev_task_repository.update_status(
                    task_id=row["task_id"],
                    status="queued",
                    error="resumed from running",
                )
        for row in rows:
            self._spawn_task(row["task_id"])
        logger.info("启动恢复 %d 条 pending dev task", len(rows))
        return len(rows)

    async def reconcile_terminal_notifications(self) -> int:
        """补偿进程退出窗口内遗漏的终态气泡。"""
        rows = self.dev_task_repository.list_terminal_tasks_missing_bubbles()
        if not rows:
            return 0
        sent = 0
        for row in rows:
            status = str(row.get("status") or "")
            if status == "succeeded":
                snapshot = TaskSnapshot(
                    task_id=str(row.get("executor_task_id") or ""),
                    status="succeeded",
                    work_branch=str(row.get("work_branch") or ""),
                    mr_url=str(row.get("mr_url") or ""),
                    commit_sha=str(row.get("commit_sha") or ""),
                )
                await self._notify_success(row=row, snapshot=snapshot)
                sent += 1
                continue
            if status == "failed":
                await self._notify_failed(
                    row=row,
                    error=str(row.get("error") or "executor_failed"),
                )
                sent += 1
        logger.info("已补偿 %d 条终态 dev task 气泡", sent)
        return sent

    async def stop(self) -> None:
        self._stopping = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if not tasks:
            return
        await asyncio.gather(*tasks, return_exceptions=True)

    # ---------- 内部 ----------

    def _spawn_task(self, task_id: str) -> None:
        task = asyncio.create_task(self._run_task(task_id), name=f"dev-task-{task_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _spawn_notify_dispatched(self, *, demand_id: str, task_id: str) -> None:
        coro = self._notify_dispatched(demand_id=demand_id, task_id=task_id)
        task = asyncio.create_task(coro, name=f"notify-dispatched-{task_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _submit_task(self, task_id: str) -> str:
        row = self.dev_task_repository.get_task(task_id)
        if row is None:
            raise RuntimeError(f"dev task 不存在，无法提交 task={task_id}")
        attempts = int(row["attempts"]) + 1
        self.dev_task_repository.update_status(
            task_id=task_id,
            status="running",
            attempts=attempts,
        )
        demand_row = self.record_repository.get_demand(row["demand_id"]) or {}
        record_row = self.record_repository.get_record_row(str(demand_row.get("record_id") or ""))
        request = _build_task_request(
            row=row,
            demand_row=demand_row,
            record_row=record_row,
            repo_local_path=self._resolve_repo_local_path(str(row["repo_id"])),
        )
        try:
            executor_task_id = await asyncio.wait_for(
                self.executor.submit(request),
                timeout=max(0.1, self.config.startup_submit_timeout_seconds),
            )
        except asyncio.TimeoutError as exc:
            self.dev_task_repository.update_status(
                task_id=task_id,
                status="failed",
                error="kxcymc_busy",
            )
            raise RuntimeError("kxcymc_busy") from exc
        except Exception as exc:
            self.dev_task_repository.update_status(
                task_id=task_id,
                status="failed",
                error=_classify_error(exc, default="submit_error"),
            )
            raise
        self.dev_task_repository.update_status(
            task_id=task_id,
            status="running",
            executor_task_id=executor_task_id,
        )
        logger.info(
            "开发任务执行器已开始工作 demand=%s task=%s executor_task=%s",
            row["demand_id"],
            task_id,
            executor_task_id,
        )
        return executor_task_id

    async def _run_task(self, task_id: str) -> None:
        bound = _logger.bind(task_id=task_id)
        async with self._semaphore:
            try:
                await self._run_task_inner(task_id)
            except asyncio.CancelledError:
                bound.info("dev task 已取消 task=%s", task_id)
                raise
            except Exception as exc:  # noqa: BLE001
                bound.exception("dev task 未捕获异常 task=%s: %s", task_id, exc)

    async def _run_task_inner(self, task_id: str) -> None:
        row = self.dev_task_repository.get_task(task_id)
        if row is None:
            logger.warning("dev task 不存在，跳过 task=%s", task_id)
            return
        executor_task_id = str(row.get("executor_task_id") or "")
        attempts = int(row["attempts"]) + (0 if executor_task_id else 1)
        max_attempts = int(row["max_attempts"]) or self.config.max_attempts
        if executor_task_id:
            self.dev_task_repository.update_status(task_id=task_id, status="running")
        else:
            self.dev_task_repository.update_status(
                task_id=task_id,
                status="running",
                attempts=attempts,
            )
            demand_row = self.record_repository.get_demand(row["demand_id"]) or {}
            record_row = self.record_repository.get_record_row(str(demand_row.get("record_id") or ""))
            request = _build_task_request(
                row=row,
                demand_row=demand_row,
                record_row=record_row,
                repo_local_path=self._resolve_repo_local_path(str(row["repo_id"])),
            )
            try:
                executor_task_id = await self.executor.submit(request)
            except Exception as exc:  # noqa: BLE001
                await self._handle_failure(
                    row=row,
                    attempts=attempts,
                    max_attempts=max_attempts,
                    error=_classify_error(exc, default="submit_error"),
                )
                return
            self.dev_task_repository.update_status(
                task_id=task_id,
                status="running",
                executor_task_id=executor_task_id,
            )

        if self._stopping:
            logger.info("orchestrator 退出，停止等待任务事件 task=%s", task_id)
            return
        try:
            snapshot = await self.executor.wait(
                executor_task_id,
                timeout_minutes=self.config.max_wait_minutes,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            await self._handle_failure(
                row=row,
                attempts=attempts,
                max_attempts=max_attempts,
                error=_classify_error(exc, default="event_error"),
            )
            return

        if snapshot.status == "succeeded":
            await self._handle_success(row=row, snapshot=snapshot)
        else:
            await self._handle_failure(
                row=row,
                attempts=attempts,
                max_attempts=max_attempts,
                error=snapshot.error or "executor_failed",
                raw_snapshot=snapshot,
            )

    async def _handle_success(
        self,
        *,
        row: dict,
        snapshot: TaskSnapshot,
    ) -> None:
        self.dev_task_repository.update_status(
            task_id=row["task_id"],
            status="succeeded",
            work_branch=snapshot.work_branch,
            mr_url=snapshot.mr_url,
            commit_sha=snapshot.commit_sha,
            raw_snapshot_json=_dump_snapshot(snapshot),
            error="",
        )
        self.dev_task_repository.update_dispatch_status(
            demand_id=row["demand_id"],
            dispatch_status="succeeded",
            mr_url=snapshot.mr_url,
        )
        await self._notify_success(row=row, snapshot=snapshot)

    async def _handle_failure(
        self,
        *,
        row: dict,
        attempts: int,
        max_attempts: int,
        error: str,
        raw_snapshot: TaskSnapshot | None = None,
    ) -> None:
        task_id = row["task_id"]
        if error == "kxcymc_canceled" or error.split(":", 1)[0] in NON_RETRYABLE_ERRORS:
            self.dev_task_repository.update_status(
                task_id=task_id,
                status="failed",
                error=error,
                raw_snapshot_json=_dump_snapshot(raw_snapshot) if raw_snapshot else None,
            )
            self.dev_task_repository.update_dispatch_status(
                demand_id=row["demand_id"],
                dispatch_status="failed",
            )
            await self._notify_failed(row=row, error=error)
            return
        if attempts < max_attempts:
            backoff = self.config.retry_backoff_base_seconds * attempts
            next_run_at = datetime.now().astimezone().isoformat(timespec="seconds")
            self.dev_task_repository.update_status(
                task_id=task_id,
                status="queued",
                executor_task_id="",
                next_run_at=next_run_at,
                error=error,
                raw_snapshot_json=_dump_snapshot(raw_snapshot) if raw_snapshot else None,
            )
            await self._notify_retry(
                row=row,
                attempts=attempts,
                max_attempts=max_attempts,
                error=error,
                next_run_at=next_run_at,
            )
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                raise
            # 重新入队：递归一次（受 semaphore 约束，不会爆栈）
            self._spawn_task(task_id)
            return
        self.dev_task_repository.update_status(
            task_id=task_id,
            status="failed",
            error=error,
            raw_snapshot_json=_dump_snapshot(raw_snapshot) if raw_snapshot else None,
        )
        self.dev_task_repository.update_dispatch_status(
            demand_id=row["demand_id"],
            dispatch_status="failed",
        )
        await self._notify_failed(row=row, error=error)

    # ---------- 气泡通知封装 ----------

    async def _notify_dispatched(self, *, demand_id: str, task_id: str) -> None:
        demand_row = self.record_repository.get_demand(demand_id) or {}
        await self.bubble_notifier.notify_dispatched(
            demand_row=demand_row,
            task_id=task_id,
        )

    async def _notify_retry(
        self,
        *,
        row: dict,
        attempts: int,
        max_attempts: int,
        error: str,
        next_run_at: str,
    ) -> None:
        demand_row = self.record_repository.get_demand(row["demand_id"]) or {}
        await self.bubble_notifier.notify_retry(
            demand_row=demand_row,
            task_id=row["task_id"],
            attempts=attempts,
            max_attempts=max_attempts,
            reason=error,
            next_attempt_at=next_run_at,
        )

    async def _notify_success(self, *, row: dict, snapshot: TaskSnapshot) -> None:
        demand_row = self.record_repository.get_demand(row["demand_id"]) or {}
        await self.bubble_notifier.notify_success(
            demand_row=demand_row,
            task_id=row["task_id"],
            mr_url=snapshot.mr_url,
            work_branch=snapshot.work_branch,
            summary=snapshot.summary,
        )

    async def _notify_failed(self, *, row: dict, error: str) -> None:
        demand_row = self.record_repository.get_demand(row["demand_id"]) or {}
        await self.bubble_notifier.notify_failed(
            demand_row=demand_row,
            task_id=row["task_id"],
            reason=error,
        )

    def _resolve_repo_local_path(self, repo_id: str) -> str:
        if self.repo_inventory_repository is None:
            return ""
        repo = self.repo_inventory_repository.get_by_repo_id(repo_id)
        return repo.local_path if repo is not None else ""


def _build_task_request(
    *,
    row: dict,
    demand_row: dict,
    record_row: dict | None = None,
    repo_local_path: str = "",
) -> TaskRequest:
    summary = str(demand_row.get("summary") or "")
    prompt = str(demand_row.get("prompt") or "")
    detail = prompt if prompt else summary
    work_branch_hint = f"feature/auto-{row['demand_id']}"
    return TaskRequest(
        demand_id=row["demand_id"],
        repo_id=row["repo_id"],
        base_branch=row["base_branch"],
        requirement_summary=summary or row["demand_id"],
        requirement_detail=detail or summary or "",
        work_branch_hint=work_branch_hint,
        repo_local_path=repo_local_path,
        media_files=_resolve_media_files(demand_row=demand_row, record_row=record_row),
    )


def _resolve_media_files(*, demand_row: dict, record_row: dict | None) -> list[dict]:
    try:
        raw_media = json.loads(str(demand_row.get("media_json") or "[]"))
    except json.JSONDecodeError:
        return []
    if not isinstance(raw_media, list):
        return []
    record_path = Path(str((record_row or {}).get("record_path") or ""))
    resolved: list[dict] = []
    for item in raw_media:
        if not isinstance(item, dict):
            continue
        local_path = str(item.get("local_path") or "")
        if not local_path:
            continue
        path = Path(local_path)
        if not path.is_absolute() and record_path:
            path = record_path / path
        enriched = dict(item)
        enriched["local_path"] = str(path)
        resolved.append(enriched)
    return resolved


def _classify_error(exc: Exception, *, default: str) -> str:
    code = getattr(exc, "error_code", None)
    if isinstance(code, str) and code:
        return code
    text = str(exc)
    if "timeout" in text.lower():
        return "event_timeout"
    return default or "kxcymc_failed"


def _dump_snapshot(snapshot: TaskSnapshot | None) -> str:
    if snapshot is None:
        return ""
    return json.dumps(
        {
            "task_id": snapshot.task_id,
            "status": snapshot.status,
            "base_branch": snapshot.base_branch,
            "work_branch": snapshot.work_branch,
            "mr_url": snapshot.mr_url,
            "commit_sha": snapshot.commit_sha,
            "summary": snapshot.summary,
            "error": snapshot.error,
        },
        ensure_ascii=False,
    )
