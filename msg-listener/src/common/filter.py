"""命中过滤 + 无效消息过滤。

- 启动时优先从 `lark-cli auth status` 直接取当前用户 open_id；不可用时再降级到 `contact +get-user`
- 私聊（chat_type == p2p）：天然命中
- 群聊（chat_type == group）：调 `lark-cli im +messages-mget` 取 mentions，比对 open_id
- 丢弃 sticker
- 丢弃文本/post 去掉空白后只剩 emoji / 飞书表情字符的消息
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from pathlib import Path

from . import cli

logger = logging.getLogger(__name__)


# 飞书表情形如 [Smile] / [玫瑰]，规范文本中常以 [xxx] 出现
_EMOTICON_BRACKET_RE = re.compile(r"\[[^\[\]\s]{1,32}\]")
# Markdown 链接 / 图片占位 [text](url) - 不应被认为是表情
_MARKDOWN_LINK_RE = re.compile(r"\[[^\[\]]+\]\([^()]+\)")


async def _open_id_via_auth_status() -> tuple[str | None, str | None]:
    """从 `lark-cli auth status` 解析当前用户的 open_id / name。

    兼容两种返回结构：
      - lark-cli 1.0.44+（嵌套）：
          { "identity": "user",
            "identities": { "user": { "openId": "ou_xxx", "userName": "...",
                                       "tokenStatus": "valid", ... } }, ... }
      - 旧版本（扁平）：
          { "identity": "user", "userOpenId": "ou_xxx", "userName": "...",
            "tokenStatus": "valid|needs_refresh|expired", ... }
    """
    try:
        data = await cli.run(["auth", "status"], identity=None, auto_format=False)
    except cli.CLIError as exc:
        logger.debug("auth status 调用失败: %s", exc)
        return None, None
    if not isinstance(data, dict):
        return None, None

    # 优先读新版嵌套结构 identities.user
    identities = data.get("identities")
    user_block = identities.get("user") if isinstance(identities, dict) else None
    if isinstance(user_block, dict):
        open_id = (
            user_block.get("openId")
            or user_block.get("open_id")
            or user_block.get("userOpenId")
        )
        name = user_block.get("userName") or user_block.get("user_name") or user_block.get("name")
        if open_id:
            return open_id, (name or None)

    # 回退到旧版扁平字段
    open_id = data.get("userOpenId") or data.get("user_open_id")
    name = data.get("userName") or data.get("user_name")
    return (open_id or None), (name or None)


async def _open_id_via_contact_get_user() -> tuple[str | None, str | None]:
    """降级方案：调用 `lark-cli contact +get-user`。"""
    try:
        data = await cli.run(["contact", "+get-user"], identity="user")
    except cli.CLIError as exc:
        logger.debug("contact +get-user 调用失败: %s", exc)
        return None, None
    user = (data or {}).get("user") or {}
    open_id = user.get("open_id") or user.get("user_id")
    name = user.get("name") or ""
    return (open_id or None), (name or None)


class OwnerCache:
    """缓存当前用户 open_id 到 cache/owner.json。"""

    def __init__(self, cache_dir: Path) -> None:
        self.path = cache_dir / "owner.json"
        self.open_id: str | None = None
        self.name: str | None = None

    async def load_or_fetch(self) -> str:
        if self.open_id:
            return self.open_id
        env_open_id = os.environ.get("MSG_LISTENER_OWNER_OPEN_ID")
        if env_open_id:
            self.open_id = env_open_id
            self.name = os.environ.get("MSG_LISTENER_OWNER_NAME") or None
            return self.open_id
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("open_id"):
                    self.open_id = data["open_id"]
                    self.name = data.get("name")
                    return self.open_id
            except (json.JSONDecodeError, OSError):
                pass

        # 优先：auth status（不依赖任何 API scope，最稳定）
        open_id, name = await _open_id_via_auth_status()
        # 降级：contact +get-user（依赖 contact:user.basic_profile:readonly 等 scope）
        if not open_id:
            logger.warning("auth status 未返回 userOpenId，降级到 `contact +get-user`")
            open_id, name = await _open_id_via_contact_get_user()

        if not open_id:
            raise RuntimeError(
                "无法获取当前用户 open_id：`lark-cli auth status` 与 "
                "`lark-cli contact +get-user --as user` 均未返回 open_id。"
                "请检查：\n"
                "  1) 是否完成 `lark-cli auth login`；\n"
                "  2) 当前应用是否授予了 `contact:user.basic_profile:readonly` scope。"
            )

        self.open_id = open_id
        self.name = name or ""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"open_id": open_id, "name": self.name}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return open_id


def is_only_emoji_or_emoticon(text: str) -> bool:
    """去掉空白后只剩 emoji / 飞书表情字符则视为无效。"""
    if text is None:
        return False
    # 移除 Markdown 链接（保留链接说明就当作有效文本）
    if _MARKDOWN_LINK_RE.search(text):
        return False
    # 移除 [xxx] 形式的飞书表情
    stripped = _EMOTICON_BRACKET_RE.sub("", text)
    # 移除空白
    stripped = re.sub(r"\s+", "", stripped)
    if not stripped:
        return True
    # 检查剩余字符是否全是 emoji / 符号
    for ch in stripped:
        cat = unicodedata.category(ch)
        # Letter / Number / Punctuation 视为有效字符
        if cat[0] in {"L", "N"}:
            return False
        if cat[0] == "P":
            return False
    return True


async def fetch_mentions(message_id: str) -> list[dict]:
    """调 `lark-cli im +messages-mget` 取消息的 mentions 数组。"""
    try:
        data = await cli.run(
            ["im", "+messages-mget", "--message-ids", message_id],
            identity="user",
        )
    except cli.CLIError as exc:
        logger.warning("messages-mget 失败 (%s): %s", message_id, exc)
        return []
    messages = (data or {}).get("messages") or []
    if not messages:
        return []
    msg = messages[0] if isinstance(messages[0], dict) else {}
    mentions = msg.get("mentions") or []
    return mentions if isinstance(mentions, list) else []


class HitFilter:
    """命中判断器。"""

    def __init__(self, owner: OwnerCache, *, filter_sticker: bool, filter_emoji_only: bool) -> None:
        self.owner = owner
        self.filter_sticker = filter_sticker
        self.filter_emoji_only = filter_emoji_only

    async def is_hit(self, event: dict) -> bool:
        message_type = event.get("message_type", "")
        if self.filter_sticker and message_type == "sticker":
            return False

        chat_type = event.get("chat_type", "")
        content = event.get("content") or ""

        if self.filter_emoji_only and message_type in {"text", "post"}:
            if is_only_emoji_or_emoticon(content):
                return False

        if chat_type == "p2p":
            # 私聊只把「发送给我」的消息当锚点：owner 自己发出的消息不应触发命中，
            # 但仍会作为前/后文上下文被收集（collect_before/after 不区分发送者）。
            owner_id = await self.owner.load_or_fetch()
            sender_id = event.get("sender_id") or ""
            if sender_id and sender_id == owner_id:
                return False
            return True

        if chat_type == "group":
            if event.get("source") == "user_at_me_poll":
                return True
            owner_id = await self.owner.load_or_fetch()
            mentions = await fetch_mentions(event["message_id"])
            for m in mentions:
                if not isinstance(m, dict):
                    continue
                mid = m.get("id")
                if mid == owner_id:
                    return True
            return False

        return False
