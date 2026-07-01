"""气泡通知器：幂等地通过 BotNotifier.send_text 推送任务状态文本。

每条气泡都必须能独立说明：处理的是哪个需求、当前状态、成功时 MR 在哪。
所有事件都使用 `bubble_id = "bubble-{task_id}-{event}"` 做幂等。
"""
from __future__ import annotations

import logging

from ..dev_tasks.repository import BubbleRepository
from ..notifier import BotNotifier, NotifierError

logger = logging.getLogger(__name__)


# 事件类型与 SQLite 字面量保持一致。
EVENT_DISPATCHED = "dispatched"
EVENT_RETRY = "retry"
EVENT_SUCCESS = "success"
EVENT_FAILED = "failed"
EVENT_CANCELLED = "cancelled"


class BubbleNotifier:
    """编排器与卡片场景共用的气泡通知通道。

    - 所有气泡都走 `BotNotifier.send_text`，纯文本格式。
    - 通过 `bubbles` 表实现幂等；重复事件不重复发送。
    - 取消事件没有 `task_id`，使用 `demand_id` 作为幂等键的占位。
    """

    def __init__(
        self,
        *,
        bot_notifier: BotNotifier,
        bubble_repository: BubbleRepository,
    ) -> None:
        self.bot_notifier = bot_notifier
        self.bubble_repository = bubble_repository

    # ---------- 公共 API ----------

    async def notify_dispatched(
        self,
        *,
        demand_row: dict,
        task_id: str,
    ) -> None:
        text = (
            "【需求执行】已开始处理\n"
            f"需求：{_demand_label(demand_row)}\n"
            f"仓库：{_repo_id(demand_row)}\n"
            f"分支：{_base_branch(demand_row)}\n"
            "状态：执行器已开始工作，后续结果将继续通知"
        )
        await self._send_once(
            demand_row=demand_row,
            task_id=task_id,
            event=EVENT_DISPATCHED,
            text=text,
        )

    async def notify_retry(
        self,
        *,
        demand_row: dict,
        task_id: str,
        attempts: int,
        max_attempts: int,
        reason: str,
        next_attempt_at: str,
    ) -> None:
        text = (
            "【需求执行】重试中\n"
            f"需求：{_demand_label(demand_row)}\n"
            f"仓库：{_repo_id(demand_row)}\n"
            f"分支：{_base_branch(demand_row)}\n"
            f"状态：第 {attempts}/{max_attempts} 次尝试失败\n"
            f"原因：{reason or 'unknown'}\n"
            f"下次尝试：{next_attempt_at or '-'}"
        )
        # 重试事件按 attempts 划分幂等键，避免覆盖
        event = f"{EVENT_RETRY}-{attempts}"
        await self._send_once(
            demand_row=demand_row,
            task_id=task_id,
            event=event,
            text=text,
        )

    async def notify_success(
        self,
        *,
        demand_row: dict,
        task_id: str,
        mr_url: str,
        work_branch: str,
        summary: str = "",
    ) -> None:
        title = "【需求执行】已完成"
        if summary.startswith("已打开 Trae 侧聊"):
            title = "【需求执行】已打开 Trae 侧聊"
        lines = [
            title,
            f"需求：{_demand_label(demand_row)}",
            f"仓库：{_repo_id(demand_row)}",
            f"分支：{_base_branch(demand_row)}",
        ]
        if summary:
            lines.append(f"状态：{summary}")
        if mr_url:
            lines.append(f"MR：{mr_url}")
        if work_branch:
            lines.append(f"工作分支：{work_branch}")
        text = "\n".join(lines)
        await self._send_once(
            demand_row=demand_row,
            task_id=task_id,
            event=EVENT_SUCCESS,
            text=text,
        )

    async def notify_failed(
        self,
        *,
        demand_row: dict,
        task_id: str,
        reason: str,
    ) -> None:
        text = (
            "【需求执行】已失败\n"
            f"需求：{_demand_label(demand_row)}\n"
            f"仓库：{_repo_id(demand_row)}\n"
            f"分支：{_base_branch(demand_row)}\n"
            f"原因：{reason or 'unknown'}"
        )
        await self._send_once(
            demand_row=demand_row,
            task_id=task_id,
            event=EVENT_FAILED,
            text=text,
        )

    async def notify_cancelled(self, *, demand_row: dict) -> None:
        text = (
            "【需求执行】已取消\n"
            f"需求：{_demand_label(demand_row)}\n"
            "状态：not-resolved"
        )
        # 取消事件没有真实 task_id，用 demand_id 占位以保证幂等键唯一。
        await self._send_once(
            demand_row=demand_row,
            task_id=str(demand_row.get("demand_id") or ""),
            event=EVENT_CANCELLED,
            text=text,
        )

    # ---------- 内部 ----------

    async def _send_once(
        self,
        *,
        demand_row: dict,
        task_id: str,
        event: str,
        text: str,
    ) -> None:
        demand_id = str(demand_row.get("demand_id") or "")
        bubble_id = f"bubble-{task_id or demand_id}-{event}"
        if self.bubble_repository.has(bubble_id):
            logger.info(
                "气泡已发送过，跳过 bubble_id=%s demand=%s",
                bubble_id,
                demand_id,
            )
            return
        try:
            message_id = await self.bot_notifier.send_text(
                text,
                idempotency_key=bubble_id,
            )
        except NotifierError as exc:
            logger.warning(
                "气泡发送失败 bubble_id=%s demand=%s: %s",
                bubble_id,
                demand_id,
                exc,
            )
            return
        self.bubble_repository.record(
            bubble_id=bubble_id,
            demand_id=demand_id,
            task_id=task_id,
            event=event,
            message_id=message_id,
        )
        logger.info(
            "气泡已发送 bubble_id=%s demand=%s task=%s message=%s",
            bubble_id,
            demand_id,
            task_id,
            message_id,
        )


def _demand_label(demand_row: dict) -> str:
    demand_id = str(demand_row.get("demand_id") or "-")
    summary = str(demand_row.get("summary") or "").strip()
    if not summary:
        summary = (str(demand_row.get("prompt") or "").strip().splitlines() or ["-"])[0]
    return f"{demand_id} / {summary or '-'}"


def _repo_id(demand_row: dict) -> str:
    return str(demand_row.get("repo") or "-")


def _base_branch(demand_row: dict) -> str:
    return str(demand_row.get("branch") or "-")
