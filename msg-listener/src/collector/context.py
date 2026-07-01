"""上下文聚合：拉前 N 条 + 收集后 N 条 + 兜底再补齐。

调用 lark-cli 的 `im +chat-messages-list` 与 `im +messages-mget`。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Optional

from ..common import cli
from ..common.filter import is_only_emoji_or_emoticon
from ..common.utils import extract_links, ms_to_iso_local, normalize_local_time_string

logger = logging.getLogger(__name__)


# record.json 不暴露内部 open_id，但 sink 阶段需要拿发送人 open_id 与 owner 比较
# 算出 is_self；这里用带下划线前缀的临时键承载，约定由 sink 落盘前移除。
SENDER_OPEN_ID_KEY = "_sender_open_id"
# 锚点消息的会话名用于补齐 record.json 顶层 chat_name，sink 落盘前会移除。
CHAT_NAME_KEY = "_chat_name"


def _sender_open_id(sender: object) -> str:
    """从 lark-cli 返回的 sender 取 open_id；键名兼容 open_id / id，读不到置空串。

    与 collector/poller.py 的 _sender_open_id 口径保持一致，避免两处对 sender
    结构的理解出现分歧。
    """
    if not isinstance(sender, dict):
        return ""
    return str(sender.get("open_id") or sender.get("id") or "")


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


def _extract_payload(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    nested = data.get("data")
    if isinstance(nested, dict):
        return nested
    return data


def _extract_chat_name(data: Any) -> str:
    payload = _extract_payload(data)
    chat = payload.get("chat") or payload
    if not isinstance(chat, dict):
        return ""
    return str(chat.get("name") or chat.get("chat_name") or "")


def _extract_user_name(data: Any) -> str:
    payload = _extract_payload(data)
    user = payload.get("user") or payload
    if not isinstance(user, dict):
        return ""
    return str(
        user.get("name")
        or user.get("en_name")
        or user.get("nickname")
        or user.get("open_id")
        or ""
    )


def _ms_to_unix_seconds_str(ms: int | str | None) -> str:
    if ms in (None, ""):
        return ""
    try:
        n = int(ms)
    except (TypeError, ValueError):
        return ""
    if n > 10_000_000_000:
        n //= 1000
    return str(n)


async def fetch_messages_mget(message_ids: list[str]) -> list[dict]:
    if not message_ids:
        return []
    # mget 上限 50
    out: list[dict] = []
    for i in range(0, len(message_ids), 50):
        batch = message_ids[i : i + 50]
        try:
            data = await cli.run(
                ["im", "+messages-mget", "--message-ids", ",".join(batch)],
                identity="user",
            )
        except cli.CLIError as exc:
            logger.warning("messages-mget 失败: %s", exc)
            continue
        msgs = (data or {}).get("messages") or []
        out.extend([m for m in msgs if isinstance(m, dict)])
    return out


async def fetch_chat_messages_list(
    chat_id: str,
    *,
    sort: str,
    page_size: int = 10,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> list[dict]:
    args = [
        "im",
        "+chat-messages-list",
        "--chat-id",
        chat_id,
        "--sort",
        sort,
        "--page-size",
        str(page_size),
    ]
    if start:
        args += ["--start", start]
    if end:
        args += ["--end", end]
    try:
        data = await cli.run(args, identity="user")
    except cli.CLIError as exc:
        logger.warning("chat-messages-list 失败 (chat=%s, sort=%s): %s", chat_id, sort, exc)
        return []
    messages = (data or {}).get("messages") or []
    return [m for m in messages if isinstance(m, dict)]


async def resolve_chat_name(event: dict) -> str:
    """补齐 record.json 顶层 chat_name，优先使用消息事件已有值。

    messages-search 通常已由 lark-cli enrich 出 chat_name；若没有，为保证最终
    record.json 带会话名，这里按会话类型做一次精确查询兜底。
    """
    existing = event.get("chat_name")
    if existing:
        return str(existing)

    chat_type = event.get("chat_type") or ""
    if chat_type == "p2p":
        partner_open_id = event.get("chat_partner_open_id") or ""
        if not partner_open_id:
            return ""
        try:
            data = await cli.run(
                [
                    "contact",
                    "+get-user",
                    "--user-id",
                    partner_open_id,
                    "--user-id-type",
                    "open_id",
                ],
                identity="user",
            )
        except cli.CLIError as exc:
            logger.warning("解析私聊会话名失败 open_id=%s: %s", partner_open_id, exc)
            return ""
        return _extract_user_name(data)

    chat_id = event.get("chat_id") or ""
    if not chat_id:
        return ""
    try:
        data = await cli.run(
            ["im", "chats", "get", "--chat-id", chat_id],
            identity="user",
        )
    except cli.CLIError as exc:
        logger.warning("解析群聊会话名失败 chat_id=%s: %s", chat_id, exc)
        return ""
    return _extract_chat_name(data)


def normalize_message(m: dict, position: str) -> dict:
    """把 lark-cli 返回的格式化 message 转成 record.json 中的 messages[] 结构。"""
    sender = m.get("sender") or {}
    sender_name = ""
    if isinstance(sender, dict):
        sender_name = sender.get("name") or ""
    msg_type = m.get("msg_type") or m.get("message_type") or ""
    content = m.get("content") or ""
    create_time = m.get("create_time") or ""
    item: dict = {
        "position": position,
        "sender_name": sender_name,
        "create_time": normalize_local_time_string(create_time),
        "type": msg_type,
        "message_id": m.get("message_id") or "",
        # 临时携带发送人 open_id 供 sink 比较 owner 算 is_self；落盘前由 sink 移除。
        SENDER_OPEN_ID_KEY: _sender_open_id(sender),
    }
    if msg_type in {"image", "video", "media", "file", "audio"}:
        # 媒体类消息：local_path 由 sink 阶段注入
        item["text"] = content  # 占位说明文本（如 "[Image: img_xxx]"）
    else:
        item["text"] = content
        links = extract_links(content)
        if links:
            item["links"] = links
    return item


def is_noise_context_message(m: dict, *, filter_sticker: bool, filter_emoji_only: bool) -> bool:
    """上下文消息是否属于无效噪音（sticker / 仅表情或 emoji）。"""
    msg_type = m.get("msg_type") or m.get("message_type") or ""
    if filter_sticker and msg_type == "sticker":
        return True
    if filter_emoji_only and msg_type in {"text", "post"}:
        content = m.get("content") or ""
        if is_only_emoji_or_emoticon(content):
            return True
    return False


async def collect_before(
    chat_id: str,
    anchor_create_time_ms: int,
    count: int,
    *,
    exclude_message_ids: set[str] | None = None,
    is_message_seen: Callable[[str], bool] | None = None,
    filter_sticker: bool = True,
    filter_emoji_only: bool = True,
) -> list[dict]:
    end_unix = _ms_to_unix_seconds_str(anchor_create_time_ms)
    page_size = min(50, max(count * 3, count + 1))
    raw = await fetch_chat_messages_list(
        chat_id, sort="desc", page_size=page_size, end=end_unix
    )
    # desc 排序：返回的是从新到旧；需要剔除锚点本身（如有），并按时间正序输出
    filtered: list[dict] = []
    exclude = exclude_message_ids or set()
    seen: set[str] = set()
    for m in raw:
        mid = m.get("message_id") or ""
        if not mid or mid in exclude or mid in seen or (is_message_seen and is_message_seen(mid)):
            continue
        if is_noise_context_message(
            m, filter_sticker=filter_sticker, filter_emoji_only=filter_emoji_only
        ):
            continue
        seen.add(mid)
        filtered.append(m)
        if len(filtered) >= count:
            break
    # 取最近 count 条
    filtered.reverse()  # 时间正序
    return [normalize_message(m, "before") for m in filtered]


async def collect_after(
    chat_id: str,
    anchor_create_time_ms: int,
    count: int,
    anchor_message_id: str,
    *,
    exclude_message_ids: set[str] | None = None,
    is_message_seen: Callable[[str], bool] | None = None,
    filter_sticker: bool = True,
    filter_emoji_only: bool = True,
) -> list[dict]:
    """按锚点时间向后拉普通消息；不要求后文消息 @我。"""
    start_unix = _ms_to_unix_seconds_str(anchor_create_time_ms)
    page_size = min(50, max(count * 3, count + 1))
    raw = await fetch_chat_messages_list(
        chat_id, sort="asc", page_size=page_size, start=start_unix
    )
    filtered: list[dict] = []
    exclude = set(exclude_message_ids or set())
    exclude.add(anchor_message_id)
    seen: set[str] = set()
    for m in raw:
        mid = m.get("message_id") or ""
        if not mid or mid in exclude or mid in seen or (is_message_seen and is_message_seen(mid)):
            continue
        if is_noise_context_message(
            m, filter_sticker=filter_sticker, filter_emoji_only=filter_emoji_only
        ):
            continue
        seen.add(mid)
        filtered.append(m)
        if len(filtered) >= count:
            break
    return [normalize_message(m, "after") for m in filtered]


async def build_anchor(message_id: str, event: dict) -> dict:
    """通过 messages-mget 取锚点消息的 sender_name 和格式化 content。"""
    msgs = await fetch_messages_mget([message_id])
    if msgs:
        m = msgs[0]
        sender = m.get("sender") or {}
        sender_name = sender.get("name") if isinstance(sender, dict) else ""
        msg_type = m.get("msg_type") or m.get("message_type") or event.get("message_type") or ""
        content = m.get("content") or event.get("content") or ""
        create_time = normalize_local_time_string(m.get("create_time") or "")
        # 优先用 mget 返回的 sender open_id；缺失时回退到事件里的 sender_id。
        sender_open_id = _sender_open_id(sender) or (event.get("sender_id") or "")
        chat_name = _chat_name(m)
    else:
        sender_name = ""
        msg_type = event.get("message_type") or ""
        content = event.get("content") or ""
        create_time = ms_to_iso_local(event.get("create_time"))
        # mget 失败时锚点 sender open_id 退化到事件携带的 sender_id。
        sender_open_id = event.get("sender_id") or ""
        chat_name = ""

    anchor = {
        "sender_name": sender_name or "",
        "create_time": create_time or ms_to_iso_local(event.get("create_time")),
        "type": msg_type,
        "message_id": message_id,
        "text": content,
        "links": extract_links(content),
        # 临时携带发送人 open_id 供 sink 比较 owner 算 is_self；落盘前由 sink 移除。
        SENDER_OPEN_ID_KEY: sender_open_id,
        CHAT_NAME_KEY: chat_name,
    }
    return anchor
