"""dev_tasks / bubbles / demands 派发字段的数据模型。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


DevTaskStatus = Literal["queued", "running", "succeeded", "failed"]
DispatchStatus = Literal[
    "pending",
    "dispatching",
    "succeeded",
    "local_succeeded",
    "failed",
    "not-resolved",
]


@dataclass
class DevTask:
    """`dev_tasks` 行视图。"""

    task_id: str
    demand_id: str
    card_instance_id: str
    repo_id: str
    base_branch: str
    executor_task_id: str = ""
    work_branch: str = ""
    mr_url: str = ""
    commit_sha: str = ""
    status: DevTaskStatus = "queued"
    attempts: int = 0
    max_attempts: int = 3
    next_run_at: str = ""
    error: str = ""
    raw_snapshot_json: str = ""
    created_at: str = ""
    updated_at: str = ""
