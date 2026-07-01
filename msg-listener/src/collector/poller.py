"""轮询 user 身份可见的消息。

时序模型：
- 每个消息来源独立持久化游标：每轮搜 `(last_poll_until - overlap, now]`。
- overlap 仅做 API 时序/时区误差兜底，不承担去重责任。
- 重复由上层 `IndexDB.is_message_seen` / `seen_messages` 表彻底阻断。
- 首次启动没有游标时，使用 `bootstrap_lookback_seconds` 决定首次回看窗口。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

from ..common import cli
from ..common.command_parser import parse_command
from ..common.index import IndexDB
from ..common.utils import local_time_string_to_ms

logger = logging.getLogger(__name__)


_META_LEGACY_LAST_POLL_UNTIL = "poller.last_poll_until"
_META_GROUP_AT_ME_LAST_POLL_UNTIL = "poller.group_at_me.last_poll_until"
_META_P2P_LAST_POLL_UNTIL = "poller.p2p.last_poll_until"


class AtMePoller:
    """用 `im +messages-search` 拉取群聊 @我消息和私聊消息。"""

    def __init__(
        self,
        *,
        owner_open_id: str,
        index_db: IndexDB,
        interval_seconds: float = 10.0,
        overlap_seconds: float = 30.0,
        bootstrap_lookback_seconds: float = 60.0,
        page_size: int = 50,
        page_limit: int = 2,
        group_at_me_enabled: bool = True,
        p2p_enabled: bool = True,
        ignored_p2p_chat_ids: set[str] | None = None,
        discover_owner_p2p_commands: bool = True,
        command_prefix: str = "/",
    ) -> None:
        self.owner_open_id = owner_open_id
        self.index_db = index_db
        self.interval_seconds = max(1.0, interval_seconds)
        self.overlap_seconds = max(0.0, overlap_seconds)
        self.bootstrap_lookback_seconds = max(self.interval_seconds, bootstrap_lookback_seconds)
        self.page_size = max(1, min(page_size, 50))
        self.page_limit = max(1, min(page_limit, 40))
        self.group_at_me_enabled = group_at_me_enabled
        self.p2p_enabled = p2p_enabled
        self.ignored_p2p_chat_ids = ignored_p2p_chat_ids or set()
        self.discover_owner_p2p_commands = discover_owner_p2p_commands
        self.command_prefix = command_prefix
        self._stop = False

    async def stop(self) -> None:
        self._stop = True

    async def stream(self) -> AsyncIterator[dict]:
        logger.info(
            "启动消息轮询：interval=%.1fs overlap=%.1fs bootstrap_lookback=%.1fs group_at_me=%s p2p=%s",
            self.interval_seconds,
            self.overlap_seconds,
            self.bootstrap_lookback_seconds,
            self.group_at_me_enabled,
            self.p2p_enabled,
        )
        while not self._stop:
            try:
                async for event in self._poll_once():
                    yield event
            except Exception as exc:  # noqa: BLE001
                logger.warning("消息轮询异常: %s", exc)
            await asyncio.sleep(self.interval_seconds)

    def _load_cursor(self, meta_key: str, *, fallback_meta_key: str | None = None) -> datetime | None:
        raw = self.index_db.get_meta(meta_key)
        if not raw and fallback_meta_key:
            raw = self.index_db.get_meta(fallback_meta_key)
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            logger.warning("游标解析失败，按首次启动处理：%s", raw)
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone()

    def _save_cursor(self, meta_key: str, end: datetime) -> None:
        self.index_db.set_meta(meta_key, end.isoformat(timespec="seconds"))

    async def _poll_once(self) -> AsyncIterator[dict]:
        events: list[dict] = []
        cursors_to_save: list[tuple[str, datetime]] = []

        if self.group_at_me_enabled:
            group_events, group_cursor = await self._poll_source(
                source="user_at_me_poll",
                label="@我群聊",
                meta_key=_META_GROUP_AT_ME_LAST_POLL_UNTIL,
                fallback_meta_key=_META_LEGACY_LAST_POLL_UNTIL,
                extra_args=[
                    "--at-chatter-ids",
                    self.owner_open_id,
                    "--chat-type",
                    "group",
                ],
                default_chat_type="group",
            )
            events.extend(group_events)
            if group_cursor:
                cursors_to_save.append((_META_GROUP_AT_ME_LAST_POLL_UNTIL, group_cursor))
        if self.p2p_enabled:
            p2p_events, p2p_cursor = await self._poll_source(
                source="user_p2p_poll",
                label="私聊",
                meta_key=_META_P2P_LAST_POLL_UNTIL,
                extra_args=["--chat-type", "p2p"],
                default_chat_type="p2p",
            )
            events.extend(p2p_events)
            if p2p_cursor:
                cursors_to_save.append((_META_P2P_LAST_POLL_UNTIL, p2p_cursor))

        events.sort(key=lambda event: int(event.get("create_time") or 0))
        for event in events:
            if (
                event.get("chat_type") == "p2p"
                and event.get("chat_id") in self.ignored_p2p_chat_ids
            ):
                continue
            yield event
        # 事件交给主流程后再推进游标，异常退出时下次仍能通过 overlap 兜底。
        for meta_key, end in cursors_to_save:
            self._save_cursor(meta_key, end)

    async def _poll_source(
        self,
        *,
        source: str,
        label: str,
        meta_key: str,
        extra_args: list[str],
        default_chat_type: str,
        fallback_meta_key: str | None = None,
    ) -> tuple[list[dict], datetime | None]:
        end = datetime.now().astimezone()
        cursor = self._load_cursor(meta_key, fallback_meta_key=fallback_meta_key)
        if cursor is None:
            start = end - timedelta(seconds=self.bootstrap_lookback_seconds)
            window_kind = "bootstrap"
        else:
            start = cursor - timedelta(seconds=self.overlap_seconds)
            window_kind = "cursor"
        if start >= end:
            logger.debug("%s轮询：游标 >= 当前时间，跳过本轮", label)
            return [], None

        try:
            data = await cli.run(
                [
                    "im",
                    "+messages-search",
                    *extra_args,
                    "--start",
                    start.isoformat(timespec="seconds"),
                    "--end",
                    end.isoformat(timespec="seconds"),
                    "--page-size",
                    str(self.page_size),
                    "--page-all",
                    "--page-limit",
                    str(self.page_limit),
                ],
                identity="user",
            )
        except cli.CLIError as exc:
            logger.warning("%s轮询失败: %s", label, exc)
            return [], None
        messages = (data or {}).get("messages") or []
        if not isinstance(messages, list):
            logger.debug("%s轮询：返回格式异常，跳过", label)
            # 即使返回异常也推进游标，避免下次 overlap 越拉越大
            self._save_cursor(meta_key, end)
            return [], None

        events = [
            self._message_to_event(m, source=source, default_chat_type=default_chat_type)
            for m in messages
            if isinstance(m, dict)
        ]
        events = [event for event in events if event.get("message_id")]
        if default_chat_type == "p2p" and self.ignored_p2p_chat_ids:
            ignored_count = 0
            kept_events: list[dict] = []
            for event in events:
                if event.get("chat_id") in self.ignored_p2p_chat_ids:
                    ignored_count += 1
                    continue
                kept_events.append(event)
            if ignored_count:
                logger.debug("%s轮询：忽略机器人单聊消息 %d 条", label, ignored_count)
            events = kept_events
        events.sort(
            key=lambda event: (
                0 if is_bot_sender_event(event) else 1,
                0 if self._is_owner_p2p_command_event(event) else 1,
                int(event.get("create_time") or 0),
            )
        )

        new_events: list[dict] = []
        skipped_seen = 0
        for event in events:
            message_id = event["message_id"]
            if self.index_db.is_message_seen(message_id):
                skipped_seen += 1
                continue
            new_events.append(event)
            logger.debug(
                "轮询发现%s消息：%s/%s type=%s message_id=%s",
                label,
                event.get("chat_type") or "-",
                event.get("chat_id") or "-",
                event.get("message_type") or "-",
                message_id,
            )
        logger.debug(
            "本轮%s轮询(%s)：返回 %d 条，新增 %d 条，已收集跳过 %d 条",
            label,
            window_kind,
            len(events),
            len(new_events),
            skipped_seen,
        )
        return new_events, end

    @staticmethod
    def _message_to_event(message: dict, *, source: str, default_chat_type: str) -> dict:
        create_time_ms = local_time_string_to_ms(message.get("create_time"))
        return {
            "source": source,
            "message_id": message.get("message_id") or "",
            "chat_id": message.get("chat_id") or "",
            "chat_name": _chat_name(message),
            "chat_partner_open_id": _chat_partner_open_id(message.get("chat_partner")),
            "chat_type": message.get("chat_type") or default_chat_type,
            "message_type": message.get("msg_type") or message.get("message_type") or "",
            "content": message.get("content") or "",
            "create_time": str(create_time_ms),
            "sender_id": _sender_open_id(message.get("sender")),
            "sender_id_type": _sender_id_type(message.get("sender")),
            "sender_type": _sender_type(message.get("sender")),
        }

    def _is_owner_p2p_command_event(self, event: dict) -> bool:
        if not self.discover_owner_p2p_commands:
            return False
        if (event.get("chat_type") or "") != "p2p":
            return False
        if (event.get("sender_id") or "") != self.owner_open_id:
            return False
        if (event.get("message_type") or "") not in {"text", "post"}:
            return False
        text = event.get("content") or ""
        if not isinstance(text, str):
            return False
        return parse_command(text, prefix=self.command_prefix) is not None


def _chat_partner_open_id(chat_partner: object) -> str:
    if not isinstance(chat_partner, dict):
        return ""
    return str(chat_partner.get("open_id") or "")


def _chat_name(message: dict) -> str:
    raw = message.get("chat_name") or message.get("chatName")
    if raw:
        return str(raw)
    chat = message.get("chat")
    if isinstance(chat, dict):
        return str(chat.get("chat_name") or chat.get("name") or "")
    partner = message.get("chat_partner")
    if isinstance(partner, dict):
        return str(partner.get("name") or "")
    return ""


def _sender_open_id(sender: object) -> str:
    if not isinstance(sender, dict):
        return ""
    return str(sender.get("open_id") or sender.get("id") or "")


def _sender_id_type(sender: object) -> str:
    if not isinstance(sender, dict):
        return ""
    return str(sender.get("id_type") or "")


def _sender_type(sender: object) -> str:
    if not isinstance(sender, dict):
        return ""
    return str(sender.get("sender_type") or "")


def is_bot_sender_event(event: dict) -> bool:
    sender_type = str(event.get("sender_type") or "").lower()
    sender_id_type = str(event.get("sender_id_type") or "").lower()
    sender_id = str(event.get("sender_id") or "")
    return (
        sender_type in {"app", "bot"}
        or sender_id_type == "app_id"
        or sender_id.startswith("cli_")
    )
