"""bot 身份的飞书消息发送通道。

提供：
- send_card：发送 interactive 卡片
- send_image / send_video：单独媒体消息（绑定 owner p2p）
- send_text：纯文本（命令回复用）
- reply_text：基于 message-id 回复

所有调用都走 `lark-cli ... --as bot`。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..common import cli

logger = logging.getLogger(__name__)


class NotifierError(RuntimeError):
    pass


class BotNotifier:
    """通过 lark-cli 以 bot 身份给 owner 单聊发送内容。"""

    def __init__(self, *, owner_open_id: str) -> None:
        self.owner_open_id = owner_open_id

    async def send_card(
        self,
        *,
        card_json: str,
        idempotency_key: str,
    ) -> str:
        """发送 interactive 卡片，返回 message_id。"""
        args = [
            "im",
            "+messages-send",
            "--user-id",
            self.owner_open_id,
            "--msg-type",
            "interactive",
            "--content",
            card_json,
            "--idempotency-key",
            idempotency_key,
        ]
        try:
            data = await cli.run(args, identity="bot", timeout=60.0)
        except cli.CLIError as exc:
            raise NotifierError(f"发送卡片失败: {exc}") from exc
        message_id = ""
        if isinstance(data, dict):
            message_id = str(data.get("message_id") or "")
        if not message_id:
            logger.warning("卡片响应未带 message_id: %s", data)
        return message_id

    async def update_card(
        self,
        *,
        message_id: str,
        card: dict[str, Any],
    ) -> None:
        """更新已发送 interactive 卡片。"""
        content = json.dumps(card, ensure_ascii=False)
        args = [
            "api",
            "PATCH",
            f"/open-apis/im/v1/messages/{message_id}",
            "--data",
            json.dumps({"content": content}, ensure_ascii=False),
        ]
        try:
            await cli.run(args, identity="bot", timeout=60.0)
        except cli.CLIError as exc:
            raise NotifierError(f"更新卡片失败: {exc}") from exc

    async def reply_card(
        self,
        *,
        message_id: str,
        card: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> str:
        """基于 message-id 回复 interactive 卡片。"""
        args = [
            "im",
            "+messages-reply",
            "--message-id",
            message_id,
            "--msg-type",
            "interactive",
            "--content",
            json.dumps(card, ensure_ascii=False),
        ]
        if idempotency_key:
            args += ["--idempotency-key", idempotency_key]
        try:
            data = await cli.run(args, identity="bot", timeout=60.0)
        except cli.CLIError as exc:
            raise NotifierError(f"回复卡片失败: {exc}") from exc
        reply_id = ""
        if isinstance(data, dict):
            reply_id = str(data.get("message_id") or "")
        return reply_id

    async def send_image(
        self,
        *,
        local_path: Path,
        idempotency_key: str,
    ) -> str:
        image_path = local_path.resolve()
        upload_args = [
            "im",
            "images",
            "create",
            "--data",
            json.dumps({"image_type": "message"}, ensure_ascii=False),
            "--file",
            f"image={image_path.name}",
        ]
        try:
            uploaded = await cli.run(
                upload_args,
                identity="bot",
                timeout=120.0,
                cwd=str(image_path.parent),
            )
        except cli.CLIError as exc:
            raise NotifierError(f"上传图片失败 ({local_path}): {exc}") from exc
        image_key = ""
        if isinstance(uploaded, dict):
            image_key = str(uploaded.get("image_key") or "")
        if not image_key:
            raise NotifierError(f"上传图片响应未带 image_key ({local_path}): {uploaded}")
        logger.debug("图片已上传 file=%s image_key=%s", image_path.name, image_key)
        args = [
            "im",
            "+messages-send",
            "--user-id",
            self.owner_open_id,
            "--image",
            image_key,
            "--idempotency-key",
            idempotency_key,
        ]
        try:
            data = await cli.run(
                args,
                identity="bot",
                timeout=120.0,
            )
        except cli.CLIError as exc:
            raise NotifierError(f"发送图片失败 ({local_path}): {exc}") from exc
        message_id = ""
        if isinstance(data, dict):
            message_id = str(data.get("message_id") or "")
        return message_id

    async def send_text(self, text: str, *, idempotency_key: str | None = None) -> str:
        args = [
            "im",
            "+messages-send",
            "--user-id",
            self.owner_open_id,
            "--text",
            text,
        ]
        if idempotency_key:
            args += ["--idempotency-key", idempotency_key]
        try:
            data = await cli.run(args, identity="bot", timeout=30.0)
        except cli.CLIError as exc:
            raise NotifierError(f"发送文本失败: {exc}") from exc
        message_id = ""
        if isinstance(data, dict):
            message_id = str(data.get("message_id") or "")
        return message_id

    async def reply_text(
        self,
        *,
        message_id: str,
        text: str,
        idempotency_key: str | None = None,
    ) -> str:
        args = [
            "im",
            "+messages-reply",
            "--message-id",
            message_id,
            "--text",
            text,
        ]
        if idempotency_key:
            args += ["--idempotency-key", idempotency_key]
        try:
            data = await cli.run(args, identity="bot", timeout=30.0)
        except cli.CLIError as exc:
            raise NotifierError(f"回复文本失败: {exc}") from exc
        reply_id = ""
        if isinstance(data, dict):
            reply_id = str(data.get("message_id") or "")
        return reply_id
