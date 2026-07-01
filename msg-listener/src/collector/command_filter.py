"""collector 端：识别机器人单聊命令并短路，避免命令文本被收为 record。"""
from __future__ import annotations

import logging

from ..common.command_parser import parse_command

logger = logging.getLogger(__name__)


def is_command_message(
    event: dict,
    *,
    prefix: str = "/",
) -> bool:
    """是否是机器人单聊命令消息。

    判定条件：
    - collector 侧开启命令短路 (`commands.ignore_in_collector`)
    - chat_type == p2p
    - message_type in {text, post}
    - 文本必须直接以 prefix 开头
    """
    if (event.get("chat_type") or "") != "p2p":
        return False
    if (event.get("message_type") or "") not in {"text", "post"}:
        return False
    text = event.get("content") or ""
    if not isinstance(text, str):
        return False
    parsed = parse_command(text, prefix=prefix)
    if parsed is None:
        return False
    logger.debug(
        "collector 短路命令消息：message_id=%s cmd=/%s",
        event.get("message_id") or "-",
        parsed.name,
    )
    return True
