"""Dev task 执行器协议与公共数据结构。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol


@dataclass(frozen=True)
class TaskRequest:
    demand_id: str
    repo_id: str
    base_branch: str
    requirement_summary: str
    requirement_detail: str
    work_branch_hint: str
    repo_local_path: str = ""
    media_files: list[dict] = field(default_factory=list)


TaskStatus = Literal["queued", "running", "succeeded", "failed"]


@dataclass(frozen=True)
class TaskSnapshot:
    task_id: str
    status: TaskStatus
    base_branch: str = ""
    work_branch: str = ""
    mr_url: str = ""
    commit_sha: str = ""
    summary: str = ""
    error: str = ""


class DevTaskExecutor(Protocol):
    async def submit(self, request: TaskRequest) -> str:
        """提交一次任务，返回 executor 端的 task_id。"""

    async def wait(self, task_id: str, timeout_minutes: float) -> TaskSnapshot:
        """阻塞等待 executor 端任务终态，返回最终快照。"""


@dataclass
class DevTaskExecutorCardSupport:
    """配置卡片所需的执行器扩展信息。"""

    available: bool = True
    dynamic_select_options: dict[str, tuple[str, ...]] = field(default_factory=dict)
    current_option_values: dict[str, str] = field(default_factory=dict)
    option_labels: dict[str, dict[str, str]] = field(default_factory=dict)
    unavailable_option_values: dict[str, tuple[str, ...]] = field(default_factory=dict)
