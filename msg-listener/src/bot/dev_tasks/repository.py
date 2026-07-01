"""dev_tasks / bubbles / demands 派发字段的仓储层。

只暴露给 bot-agent 进程使用；与 RecordRepository 共用同一份 IndexDB。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Any

from ...common.index import IndexDB
from .models import DevTask, DevTaskStatus, DispatchStatus

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class DevTaskRepository:
    """`dev_tasks` + `demands.dispatch_*` 字段的读写。"""

    def __init__(self, index_db: IndexDB) -> None:
        self.index_db = index_db

    # ---------- dev_tasks ----------

    def insert_task(self, task: DevTask) -> DevTask:
        now = _now_iso()
        task.created_at = task.created_at or now
        task.updated_at = now
        with closing(self.index_db.connect()) as conn:
            conn.execute(
                """
                INSERT INTO dev_tasks(
                  task_id, executor_task_id, demand_id, card_instance_id, repo_id, base_branch,
                  work_branch, mr_url, commit_sha, status, attempts, max_attempts,
                  next_run_at, error, raw_snapshot_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.task_id,
                    task.executor_task_id,
                    task.demand_id,
                    task.card_instance_id,
                    task.repo_id,
                    task.base_branch,
                    task.work_branch,
                    task.mr_url,
                    task.commit_sha,
                    task.status,
                    task.attempts,
                    task.max_attempts,
                    task.next_run_at,
                    task.error,
                    task.raw_snapshot_json,
                    task.created_at,
                    task.updated_at,
                ),
            )
            conn.commit()
        return task

    def try_insert_task_with_capacity(self, task: DevTask, *, max_active: int) -> bool:
        """Atomically reserve a dev task slot when active tasks are below capacity."""
        now = _now_iso()
        task.created_at = task.created_at or now
        task.updated_at = now
        capacity = max(1, int(max_active))
        with closing(self.index_db.connect()) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    """
                    SELECT COUNT(1)
                    FROM dev_tasks
                    WHERE status IN ('queued', 'running')
                    """
                ).fetchone()
                active_count = int(row[0] if row else 0)
                if active_count >= capacity:
                    conn.rollback()
                    return False
                conn.execute(
                    """
                    INSERT INTO dev_tasks(
                      task_id, executor_task_id, demand_id, card_instance_id, repo_id, base_branch,
                      work_branch, mr_url, commit_sha, status, attempts, max_attempts,
                      next_run_at, error, raw_snapshot_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task.task_id,
                        task.executor_task_id,
                        task.demand_id,
                        task.card_instance_id,
                        task.repo_id,
                        task.base_branch,
                        task.work_branch,
                        task.mr_url,
                        task.commit_sha,
                        task.status,
                        task.attempts,
                        task.max_attempts,
                        task.next_run_at,
                        task.error,
                        task.raw_snapshot_json,
                        task.created_at,
                        task.updated_at,
                    ),
                )
                conn.commit()
                return True
            except sqlite3.Error:
                conn.rollback()
                raise

    def has_active_task_for_repo(
        self,
        repo_id: str,
        exclude_demand_id: str | None = None,
    ) -> bool:
        sql = """
            SELECT 1
            FROM dev_tasks
            WHERE repo_id = ?
              AND status IN ('queued', 'running')
        """
        values: list[Any] = [repo_id]
        if exclude_demand_id:
            sql += " AND demand_id != ?"
            values.append(exclude_demand_id)
        sql += " LIMIT 1"
        with closing(self.index_db.connect()) as conn:
            row = conn.execute(sql, values).fetchone()
        return row is not None

    def try_insert_task_with_capacity_and_repo_idle(
        self,
        task: DevTask,
        *,
        max_active: int,
    ) -> str:
        """Atomically reserve a dev task slot and ensure same repo is idle."""
        now = _now_iso()
        task.created_at = task.created_at or now
        task.updated_at = now
        capacity = max(1, int(max_active))
        with closing(self.index_db.connect()) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    """
                    SELECT COUNT(1)
                    FROM dev_tasks
                    WHERE status IN ('queued', 'running')
                    """
                ).fetchone()
                active_count = int(row[0] if row else 0)
                if active_count >= capacity:
                    conn.rollback()
                    return "capacity_full"
                row = conn.execute(
                    """
                    SELECT 1
                    FROM dev_tasks
                    WHERE repo_id = ?
                      AND demand_id != ?
                      AND status IN ('queued', 'running')
                    LIMIT 1
                    """,
                    (task.repo_id, task.demand_id),
                ).fetchone()
                if row is not None:
                    conn.rollback()
                    return "repo_busy"
                conn.execute(
                    """
                    INSERT INTO dev_tasks(
                      task_id, executor_task_id, demand_id, card_instance_id, repo_id, base_branch,
                      work_branch, mr_url, commit_sha, status, attempts, max_attempts,
                      next_run_at, error, raw_snapshot_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task.task_id,
                        task.executor_task_id,
                        task.demand_id,
                        task.card_instance_id,
                        task.repo_id,
                        task.base_branch,
                        task.work_branch,
                        task.mr_url,
                        task.commit_sha,
                        task.status,
                        task.attempts,
                        task.max_attempts,
                        task.next_run_at,
                        task.error,
                        task.raw_snapshot_json,
                        task.created_at,
                        task.updated_at,
                    ),
                )
                conn.commit()
                return "inserted"
            except sqlite3.Error:
                conn.rollback()
                raise

    def update_status(
        self,
        *,
        task_id: str,
        status: DevTaskStatus,
        attempts: int | None = None,
        next_run_at: str | None = None,
        error: str | None = None,
        executor_task_id: str | None = None,
        work_branch: str | None = None,
        mr_url: str | None = None,
        commit_sha: str | None = None,
        raw_snapshot_json: str | None = None,
    ) -> None:
        fields: list[str] = ["status = ?", "updated_at = ?"]
        values: list[Any] = [status, _now_iso()]
        if attempts is not None:
            fields.append("attempts = ?")
            values.append(attempts)
        if next_run_at is not None:
            fields.append("next_run_at = ?")
            values.append(next_run_at)
        if error is not None:
            fields.append("error = ?")
            values.append(error[:4000])
        if executor_task_id is not None:
            fields.append("executor_task_id = ?")
            values.append(executor_task_id)
        if work_branch is not None:
            fields.append("work_branch = ?")
            values.append(work_branch)
        if mr_url is not None:
            fields.append("mr_url = ?")
            values.append(mr_url)
        if commit_sha is not None:
            fields.append("commit_sha = ?")
            values.append(commit_sha)
        if raw_snapshot_json is not None:
            fields.append("raw_snapshot_json = ?")
            values.append(raw_snapshot_json)
        sql = f"UPDATE dev_tasks SET {', '.join(fields)} WHERE task_id = ?"
        values.append(task_id)
        with closing(self.index_db.connect()) as conn:
            conn.execute(sql, values)
            conn.commit()

    def get_task(self, task_id: str) -> dict | None:
        with closing(self.index_db.connect()) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM dev_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_pending_tasks(self) -> list[dict]:
        """启动时挑出 queued / running 任务用于恢复。"""
        with closing(self.index_db.connect()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM dev_tasks
                WHERE status IN ('queued', 'running')
                ORDER BY created_at ASC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def list_terminal_tasks_missing_bubbles(self) -> list[dict]:
        """启动时补偿已终态但终态气泡未落库的任务。"""
        with closing(self.index_db.connect()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT dt.*
                FROM dev_tasks dt
                JOIN demands d ON d.demand_id = dt.demand_id
                LEFT JOIN bubbles b
                  ON b.bubble_id =
                    CASE
                      WHEN dt.status = 'succeeded' THEN 'bubble-' || dt.task_id || '-success'
                      WHEN dt.status = 'failed' THEN 'bubble-' || dt.task_id || '-failed'
                      ELSE ''
                    END
                WHERE dt.status IN ('succeeded', 'failed')
                  AND b.bubble_id IS NULL
                  AND d.dispatch_status != 'local_succeeded'
                ORDER BY dt.updated_at ASC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- demands dispatch fields ----------

    def update_dispatch_status(
        self,
        *,
        demand_id: str,
        dispatch_status: DispatchStatus,
        current_task_id: str | None = None,
        mr_url: str | None = None,
    ) -> None:
        fields: list[str] = ["dispatch_status = ?", "updated_at = ?"]
        values: list[Any] = [dispatch_status, _now_iso()]
        if current_task_id is not None:
            fields.append("current_task_id = ?")
            values.append(current_task_id)
        if mr_url is not None:
            fields.append("mr_url = ?")
            values.append(mr_url)
        sql = f"UPDATE demands SET {', '.join(fields)} WHERE demand_id = ?"
        values.append(demand_id)
        with closing(self.index_db.connect()) as conn:
            conn.execute(sql, values)
            conn.commit()


class BubbleRepository:
    """气泡通知幂等表。"""

    def __init__(self, index_db: IndexDB) -> None:
        self.index_db = index_db

    def has(self, bubble_id: str) -> bool:
        with closing(self.index_db.connect()) as conn:
            row = conn.execute(
                "SELECT 1 FROM bubbles WHERE bubble_id = ? LIMIT 1",
                (bubble_id,),
            ).fetchone()
        return row is not None

    def record(
        self,
        *,
        bubble_id: str,
        demand_id: str,
        task_id: str,
        event: str,
        message_id: str = "",
    ) -> None:
        now = _now_iso()
        with closing(self.index_db.connect()) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO bubbles(
                  bubble_id, demand_id, task_id, event, message_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (bubble_id, demand_id, task_id, event, message_id, now),
            )
            conn.commit()


def parse_evidence(text: str) -> list[str]:
    try:
        data = json.loads(text or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in data] if isinstance(data, list) else []
