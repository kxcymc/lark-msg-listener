"""Collect messages sent by the owner and aggregate nearby chat context."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from src.common import cli

logger = logging.getLogger(__name__)

LOCAL_TZ = datetime.now().astimezone().tzinfo


@dataclass(frozen=True)
class MyworkCollectConfig:
    lookback_days: int = 1
    context_window: int = 10
    chat_history_buffer_hours: int = 24
    page_size: int = 50
    max_pages_per_chat: int = 40


def iso_local(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S%z")[:-2] + ":" + dt.strftime("%z")[-2:]


def parse_chat_create_time(value: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=LOCAL_TZ)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return datetime.fromtimestamp(0, tz=LOCAL_TZ)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=LOCAL_TZ)


async def collect_mywork(
    *,
    owner_open_id: str,
    owner_name: str | None,
    config: MyworkCollectConfig,
) -> dict[str, Any]:
    now = datetime.now(LOCAL_TZ)
    window_start = now - timedelta(hours=24 * config.lookback_days)
    logger.info(
        "搜索 owner=%s 在 %s ~ %s 内发送的消息",
        owner_open_id,
        iso_local(window_start),
        iso_local(now),
    )
    anchors = await _search_my_messages(
        owner_open_id,
        window_start,
        now,
        page_size=config.page_size,
        page_limit=config.max_pages_per_chat,
    )

    chats = _group_anchor_messages(anchors, owner_open_id)
    skipped = len(anchors) - sum(len(item["anchor_messages"]) for item in chats.values())
    logger.info("找到锚点消息 %d 条，涉及聊天 %d 个，过滤 %d 条", len(anchors), len(chats), skipped)

    results: list[dict[str, Any]] = []
    for idx, (chat_id, entry) in enumerate(chats.items(), 1):
        chat_result = await _collect_chat_context(
            chat_id=chat_id,
            entry=entry,
            owner_open_id=owner_open_id,
            now=now,
            config=config,
        )
        results.append(chat_result)
        logger.info(
            "[%d/%d] %s anchors=%d kept=%d",
            idx,
            len(chats),
            chat_result.get("chat_name") or chat_id,
            len(entry["anchor_messages"]),
            len(chat_result["messages"]),
        )

    return {
        "self": {"open_id": owner_open_id, "name": owner_name or ""},
        "lookback_days": config.lookback_days,
        "context_window": config.context_window,
        "chat_count": len(results),
        "chats": results,
    }


async def _search_my_messages(
    open_id: str,
    start: datetime,
    end: datetime,
    *,
    page_size: int,
    page_limit: int,
) -> list[dict[str, Any]]:
    data = await cli.run(
        [
            "im",
            "+messages-search",
            "--sender",
            open_id,
            "--start",
            iso_local(start),
            "--end",
            iso_local(end),
            "--page-size",
            str(min(page_size, 50)),
            "--page-all",
            "--page-limit",
            str(page_limit),
            "--no-reactions",
        ],
        identity="user",
    )
    return _extract_messages(data)


def _group_anchor_messages(
    anchors: list[dict[str, Any]],
    owner_open_id: str,
) -> dict[str, dict[str, Any]]:
    chats: dict[str, dict[str, Any]] = {}
    for message in anchors:
        chat_id = message.get("chat_id")
        if not chat_id:
            continue
        partner_open_id = (message.get("chat_partner") or {}).get("open_id")
        if message.get("chat_type") == "p2p" and (
            not partner_open_id or partner_open_id == owner_open_id
        ):
            continue
        entry = chats.setdefault(
            chat_id,
            {
                "chat_id": chat_id,
                "chat_type": message.get("chat_type"),
                "chat_name": message.get("chat_name"),
                "chat_partner_open_id": partner_open_id,
                "anchor_ids": set(),
                "anchor_messages": [],
            },
        )
        message_id = message.get("message_id")
        if message_id and message_id not in entry["anchor_ids"]:
            entry["anchor_ids"].add(message_id)
            entry["anchor_messages"].append(message)
    return chats


async def _collect_chat_context(
    *,
    chat_id: str,
    entry: dict[str, Any],
    owner_open_id: str,
    now: datetime,
    config: MyworkCollectConfig,
) -> dict[str, Any]:
    anchor_msgs = sorted(
        entry["anchor_messages"],
        key=lambda item: parse_chat_create_time(str(item.get("create_time", ""))),
    )
    buffer = timedelta(hours=config.chat_history_buffer_hours)
    earliest = parse_chat_create_time(str(anchor_msgs[0].get("create_time", ""))) - buffer
    latest = parse_chat_create_time(str(anchor_msgs[-1].get("create_time", ""))) + buffer
    if latest > now:
        latest = now

    chat_type = entry.get("chat_type")
    partner_open_id = entry.get("chat_partner_open_id")
    if chat_type == "p2p" and partner_open_id and partner_open_id != owner_open_id:
        history = await _list_chat_messages(
            chat_id=None,
            user_id=partner_open_id,
            start=earliest,
            end=latest,
            page_size=config.page_size,
            max_pages=config.max_pages_per_chat,
        )
    else:
        history = await _list_chat_messages(
            chat_id=chat_id,
            user_id=None,
            start=earliest,
            end=latest,
            page_size=config.page_size,
            max_pages=config.max_pages_per_chat,
        )

    if not history:
        history = list(anchor_msgs)
    history.sort(key=lambda item: parse_chat_create_time(str(item.get("create_time", ""))))
    index_by_id = {
        item.get("message_id"): idx
        for idx, item in enumerate(history)
        if item.get("message_id")
    }

    kept: dict[str, dict[str, Any]] = {}
    for anchor in anchor_msgs:
        message_id = anchor.get("message_id")
        index = index_by_id.get(message_id)
        if index is None:
            if message_id:
                kept[message_id] = anchor
            continue
        lo = max(0, index - config.context_window)
        hi = min(len(history), index + config.context_window + 1)
        for message in history[lo:hi]:
            current_id = message.get("message_id")
            if current_id:
                kept[current_id] = message

    final_messages = sorted(
        kept.values(),
        key=lambda item: parse_chat_create_time(str(item.get("create_time", ""))),
    )
    if chat_type == "p2p":
        chat_label = await _resolve_p2p_partner_name(partner_open_id) if partner_open_id else chat_id
    else:
        chat_label = entry.get("chat_name") or chat_id
    return {
        "chat_id": chat_id,
        "chat_type": chat_type,
        "chat_name": chat_label,
        "messages": [_normalize_message(item) for item in final_messages],
    }


async def _list_chat_messages(
    *,
    chat_id: str | None,
    user_id: str | None,
    start: datetime,
    end: datetime,
    page_size: int,
    max_pages: int,
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    page_token = ""
    pages = 0
    while True:
        args = [
            "im",
            "+chat-messages-list",
            "--start",
            iso_local(start),
            "--end",
            iso_local(end),
            "--page-size",
            str(min(page_size, 50)),
            "--sort",
            "asc",
            "--no-reactions",
        ]
        if chat_id:
            args += ["--chat-id", chat_id]
        elif user_id:
            args += ["--user-id", user_id]
        else:
            return []
        if page_token:
            args += ["--page-token", page_token]

        try:
            data = await cli.run(args, identity="user")
        except cli.CLIError as exc:
            logger.warning("chat-messages-list 失败 chat_id=%s user_id=%s: %s", chat_id, user_id, exc)
            break
        payload = _extract_payload(data)
        collected.extend(_extract_messages(payload))
        page_token = str(payload.get("page_token") or "")
        pages += 1
        if not payload.get("has_more") or not page_token or pages >= max_pages:
            break
    return collected


def _extract_payload(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    nested = data.get("data")
    if isinstance(nested, dict):
        return nested
    return data


def _extract_messages(data: Any) -> list[dict[str, Any]]:
    payload = _extract_payload(data)
    raw = payload.get("messages") or payload.get("items") or []
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _normalize_message(message: dict[str, Any]) -> dict[str, Any]:
    sender = message.get("sender") or {}
    return {
        "msg_type": message.get("msg_type") or message.get("message_type"),
        "content": message.get("content"),
        "sender": {"name": sender.get("name")},
        "create_time": message.get("create_time"),
    }


async def _resolve_p2p_partner_name(open_id: str | None) -> str:
    if not open_id:
        return ""
    try:
        data = await cli.run(
            [
                "contact",
                "+get-user",
                "--user-id",
                open_id,
                "--user-id-type",
                "open_id",
            ],
            identity="user",
        )
    except cli.CLIError as exc:
        logger.debug("解析私聊对象名称失败 open_id=%s: %s", open_id, exc)
        return open_id
    user = (_extract_payload(data).get("user") or _extract_payload(data))
    if not isinstance(user, dict):
        return open_id
    return str(user.get("name") or user.get("nickname") or open_id)
