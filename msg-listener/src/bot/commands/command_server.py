"""CommandServer：处理 SDK WebSocket 转发的 /help /show /config /del-out 消息事件。"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ...common import cli
from ...common.command_parser import ParsedCommand, parse_command
from ..cards.base import CardOpenContext
from ..cards.scenes.meego_scene import MEEGO_MODE_CHAT, MEEGO_MODE_URL
from ..cards.service import CardService
from ..notifier import BotNotifier, NotifierError
from .out_reset import ResetReport, reset_out_dir

logger = logging.getLogger(__name__)


SHOW_MESSAGE = "你好，我是你的需求探测小助手！发送 /help 查看命令帮助。"

# 机器人自定义菜单事件 event_key
BOT_MENU_OPEN_CONFIG = "open_config"
BOT_MENU_SHOW_HELP = "show_help"
BOT_MENU_OPEN_DEVTASK = "open_devtask"
BOT_MENU_OPEN_MEEGO_URL = "open_meego_url"
BOT_MENU_OPEN_MEEGO_CHAT = "open_meego_chat"


@dataclass
class CommandServerConfig:
    prefix: str = "/"
    del_out_confirm_ttl_seconds: float = 120.0


class CommandServer:
    """事件消费 + 命令分发。

    与 AnalyzerLoop 共享 `out_lock`：`/del-out confirm` 拿到锁后才执行 reset。
    """

    def __init__(
        self,
        *,
        owner_open_id: str,
        notifier: BotNotifier,
        out_lock: asyncio.Lock,
        out_dir: Path,
        project_root: Path,
        config_path: Path,
        cmd_config: CommandServerConfig,
        card_service: CardService | None = None,
    ) -> None:
        self.owner_open_id = owner_open_id
        self.notifier = notifier
        self.out_lock = out_lock
        self.out_dir = out_dir
        self.project_root = project_root
        self.config_path = config_path
        self.cmd_config = cmd_config
        self.card_service = card_service

        # 单 owner 单 chat：用一个时间戳记录最近一次 /del-out 的请求时间
        self._del_out_pending_until: float = 0.0
        # 已处理 message_id 集合（进程内幂等）
        self._handled_message_ids: set[str] = set()
        # 已处理机器人菜单事件集合（进程内短窗口幂等）
        self._handled_bot_menu_events: set[str] = set()

    async def stop(self) -> None:
        self._del_out_pending_until = 0.0
        self._handled_message_ids.clear()
        self._handled_bot_menu_events.clear()

    async def handle_message_event(self, raw_event: dict) -> None:
        event = _normalize_message_event(raw_event)
        if event is None:
            return
        await self._dispatch(event)

    async def handle_bot_menu_event(self, raw_event: dict) -> None:
        event_obj = raw_event.get("event")
        if not isinstance(event_obj, dict):
            return
        event_key = str(event_obj.get("event_key") or "")
        timestamp = str(event_obj.get("timestamp") or "")
        operator = event_obj.get("operator")
        operator_open_id = ""
        if isinstance(operator, dict):
            operator_id = operator.get("operator_id")
            if isinstance(operator_id, dict):
                operator_open_id = str(operator_id.get("open_id") or "")

        if not operator_open_id:
            logger.warning("机器人菜单事件缺少操作者 open_id，忽略 event_key=%s", event_key or "-")
            return
        if operator_open_id != self.owner_open_id:
            logger.info("机器人菜单事件操作者非 owner，忽略 event_key=%s", event_key or "-")
            return

        idem_key = f"menu-{event_key}-{timestamp}"
        dedup_key = f"menu:{operator_open_id}:{event_key}:{timestamp}"
        if dedup_key in self._handled_bot_menu_events:
            return
        self._handled_bot_menu_events.add(dedup_key)
        if len(self._handled_bot_menu_events) > 4096:
            keys = list(self._handled_bot_menu_events)[2048:]
            self._handled_bot_menu_events = set(keys)

        logger.info("处理机器人菜单事件 event_key=%s timestamp=%s", event_key or "-", timestamp or "-")

        if event_key == BOT_MENU_OPEN_CONFIG:
            await self._handle_config(
                ParsedCommand(
                    name="config",
                    args=[],
                    kvs={},
                    raw_text="",
                    raw_after_strip="",
                ),
                "",
                idem_key,
            )
        elif event_key == BOT_MENU_SHOW_HELP:
            await self._handle_help("", idem_key)
        elif event_key == BOT_MENU_OPEN_DEVTASK:
            await self._open_devtask_scene("")
        elif event_key == BOT_MENU_OPEN_MEEGO_URL:
            await self._handle_meego(MEEGO_MODE_URL, "", idem_key)
        elif event_key == BOT_MENU_OPEN_MEEGO_CHAT:
            await self._handle_meego(MEEGO_MODE_CHAT, "", idem_key)
        else:
            logger.warning("未知菜单操作：%s", event_key or "-")
            await self._reply("", f"未知菜单操作：{event_key}", idem_key)

    async def _dispatch(self, event: dict) -> None:
        # 安全边界
        if (event.get("chat_type") or "") != "p2p":
            return
        sender_id = event.get("sender_id") or ""
        if sender_id != self.owner_open_id:
            return
        msg_type = event.get("message_type") or ""
        if msg_type not in {"text", "post"}:
            return
        message_id = event.get("message_id") or ""
        if message_id and message_id in self._handled_message_ids:
            return

        text = event.get("content") or ""
        if not isinstance(text, str):
            return
        parsed = parse_command(text, prefix=self.cmd_config.prefix)
        if parsed is None:
            return

        if message_id:
            self._handled_message_ids.add(message_id)
            # 简单上限，避免无限增长
            if len(self._handled_message_ids) > 4096:
                # 丢弃前一半；O(n) 但调用很少
                ids = list(self._handled_message_ids)[2048:]
                self._handled_message_ids = set(ids)

        idem_key = f"cmd-{message_id}" if message_id else None
        logger.info(
            "处理命令 message_id=%s cmd=/%s args=%s kvs=%s",
            message_id or "-",
            parsed.name,
            parsed.args,
            parsed.kvs,
        )

        if parsed.name == "help":
            await self._handle_help(message_id, idem_key)
        elif parsed.name == "show":
            await self._handle_show(message_id, idem_key)
        elif parsed.name == "config":
            await self._handle_config(parsed, message_id, idem_key)
        elif parsed.name == "meego-chat":
            await self._handle_meego(MEEGO_MODE_CHAT, message_id, idem_key)
        elif parsed.name == "meego-url":
            await self._handle_meego(MEEGO_MODE_URL, message_id, idem_key)
        elif parsed.name == "devtask":
            await self._handle_devtask(event, message_id, idem_key)
        elif parsed.name == "del-out":
            await self._handle_del_out(parsed, message_id, idem_key)
        else:
            await self._reply(
                message_id,
                f"未知命令：/{parsed.name}\n发送 `/help` 查看可用命令。",
                idem_key,
            )

    async def _reply(
        self,
        message_id: str,
        text: str,
        idempotency_key: Optional[str],
    ) -> None:
        try:
            if message_id:
                await self.notifier.reply_text(
                    message_id=message_id,
                    text=text,
                    idempotency_key=idempotency_key,
                )
            else:
                await self.notifier.send_text(text, idempotency_key=idempotency_key)
        except NotifierError as exc:
            logger.warning("命令回复失败 message_id=%s: %s", message_id or "-", exc)

    async def _handle_help(self, message_id: str, idem_key: Optional[str]) -> None:
        text = (
            "可用命令：\n"
            "- /help：查看帮助。\n"
            "- /show：查看小助手介绍。\n"
            "- /config：打开配置交互卡片。\n"
            "- /meego-chat：按飞书群聊名称收集 Meego 需求信息并生成 prompt。\n"
            "- /meego-url：按 Meego 链接收集需求信息并生成 prompt。\n"
            "- /devtask：手动创建开发任务；回复普通气泡消息时会自动带入需求 prompt。\n"
            # "- /del-out：清空输出目录前的确认提示。\n"
            # "- /del-out confirm：在确认 TTL 内确认清空 `out_dir` 并重建空 `index.sqlite`。"
        )
        await self._reply(message_id, text, idem_key)

    async def _handle_show(self, message_id: str, idem_key: Optional[str]) -> None:
        await self._reply(message_id, SHOW_MESSAGE, idem_key)

    async def send_startup_welcome(self) -> None:
        """单聊服务首次就绪后主动发送欢迎气泡。"""
        try:
            await self.notifier.send_text(SHOW_MESSAGE)
        except NotifierError as exc:
            logger.warning("发送启动欢迎语失败: %s", exc)

    async def _handle_config(
        self,
        _parsed: ParsedCommand,
        message_id: str,
        idem_key: Optional[str],
    ) -> None:
        del _parsed
        if self.card_service is None:
            await self._reply(message_id, "配置卡片服务尚未初始化。", idem_key)
            return
        await self.card_service.open_scene(
            scene_key="config",
            open_context=CardOpenContext(
                owner_open_id=self.owner_open_id,
                trigger_message_id=message_id or None,
            ),
            expire_previous=True,
        )

    async def _handle_meego(
        self,
        mode: str,
        message_id: str,
        idem_key: Optional[str],
    ) -> None:
        if self.card_service is None:
            await self._reply(message_id, "Meego 卡片服务尚未初始化。", idem_key)
            return
        await self.card_service.open_scene(
            scene_key="meego",
            open_context=CardOpenContext(
                owner_open_id=self.owner_open_id,
                trigger_message_id=message_id or None,
                extra={"mode": mode},
            ),
            expire_previous=True,
        )

    async def _open_devtask_scene(self, message_id: str) -> None:
        if self.card_service is None:
            return
        await self.card_service.open_scene(
            scene_key="devtask",
            open_context=CardOpenContext(
                owner_open_id=self.owner_open_id,
                trigger_message_id=message_id or None,
            ),
            expire_previous=False,
        )

    async def _handle_devtask(
        self,
        event: dict,
        message_id: str,
        idem_key: Optional[str],
    ) -> None:
        if self.card_service is None:
            await self._reply(message_id, "开发任务卡片服务尚未初始化。", idem_key)
            return
        reply_message_id = _reply_target_message_id(event, message_id)
        if not reply_message_id:
            await self._open_devtask_scene(message_id)
            return
        reply_message = await self._fetch_message_by_id(reply_message_id)
        if not reply_message:
            await self._reply(
                message_id,
                "无法读取被回复消息，请确认消息仍可访问后重试。",
                idem_key,
            )
            return

        if _is_card_message(reply_message):
            await self._reply(message_id, "不支持通过卡片触发/devtask指令", idem_key)
            return

        reply_message_type = _message_type(reply_message)
        if reply_message_type not in {"text", "post"}:
            await self._reply(
                message_id,
                "请回复文本或富文本气泡消息触发 /devtask。",
                idem_key,
            )
            return

        prompt = _message_content(reply_message, reply_message_type)
        if not prompt.strip():
            await self._reply(
                message_id,
                "被回复消息没有可用文本，请回复包含需求内容的气泡消息。",
                idem_key,
            )
            return

        await self.card_service.open_scene(
            scene_key="devtask",
            open_context=CardOpenContext(
                owner_open_id=self.owner_open_id,
                trigger_message_id=message_id or None,
                extra={
                    "prompt": prompt,
                    "source_message_id": reply_message_id,
                    "source_message_type": reply_message_type,
                },
            ),
            expire_previous=False,
        )

    async def _fetch_message_by_id(self, message_id: str) -> dict | None:
        if not message_id:
            return None
        try:
            data = await cli.run(
                ["im", "+messages-mget", "--message-ids", message_id],
                identity="user",
            )
        except cli.CLIError as exc:
            logger.warning("读取被回复消息失败 message_id=%s: %s", message_id, exc)
            return None
        messages = (data or {}).get("messages") or []
        for message in messages:
            if isinstance(message, dict):
                return message
        return None

    async def _handle_del_out(
        self,
        parsed: ParsedCommand,
        message_id: str,
        idem_key: Optional[str],
    ) -> None:
        confirm = bool(parsed.args and parsed.args[0].lower() == "confirm")
        now = time.monotonic()
        if not confirm:
            self._del_out_pending_until = now + self.cmd_config.del_out_confirm_ttl_seconds
            ttl = int(self.cmd_config.del_out_confirm_ttl_seconds)
            await self._reply(
                message_id,
                (
                    "⚠️ /del-out 是危险操作，将清空：\n"
                    f"- 目录：`{self.out_dir}`\n"
                    "- 包含：所有 record.json、媒体文件、analysis_jobs / demands / "
                    "card_deliveries 状态、轮询游标。\n\n"
                    f"如果确认执行，请在 {ttl} 秒内发送 /del-out confirm。"
                ),
                idem_key,
            )
            return

        if now > self._del_out_pending_until:
            await self._reply(
                message_id,
                "确认已过期或未发起。请先发送 /del-out 触发二次确认提示。",
                idem_key,
            )
            return
        # 拿锁执行
        self._del_out_pending_until = 0.0
        try:
            async with self.out_lock:
                report: ResetReport = await asyncio.to_thread(
                    reset_out_dir, self.out_dir
                )
        except OSError as exc:
            logger.exception("/del-out 执行失败：%s", exc)
            await self._reply(message_id, f"清空失败：{exc}", idem_key)
            return

        await self._reply(
            message_id,
            (
                "✅ 已清空 `out_dir` 并重建空 `index.sqlite`：\n"
                f"- 目录：`{report.out_dir}`\n"
                f"- 删除条目数：{report.removed_entries}\n"
                "- 索引：已按当前 schema 重建（records / seen_messages / meta / "
                "analysis_jobs / demands / card_deliveries）。\n\n"
                "提示：请同时确认 collector 与 bot-agent 状态，必要时重启它们。"
            ),
            idem_key,
        )


def _normalize_message_event(raw_event: dict) -> dict | None:
    event_obj = raw_event.get("event")
    if isinstance(event_obj, dict):
        message = event_obj.get("message")
        sender = event_obj.get("sender")
        if not isinstance(message, dict):
            return None
        return {
            "chat_type": str(message.get("chat_type") or ""),
            "chat_id": str(message.get("chat_id") or ""),
            "sender_id": _extract_sender_open_id(sender),
            "message_type": str(message.get("message_type") or ""),
            "message_id": str(
                message.get("message_id") or message.get("open_message_id") or ""
            ),
            "parent_id": str(message.get("parent_id") or ""),
            "root_id": str(message.get("root_id") or ""),
            "thread_id": str(message.get("thread_id") or ""),
            "upper_message_id": str(message.get("upper_message_id") or ""),
            "content": _extract_command_text(
                message.get("content"),
                message_type=str(message.get("message_type") or ""),
            ),
        }

    if not isinstance(raw_event, dict):
        return None
    return {
        "chat_type": str(raw_event.get("chat_type") or ""),
        "chat_id": str(raw_event.get("chat_id") or ""),
        "sender_id": str(raw_event.get("sender_id") or ""),
        "message_type": str(raw_event.get("message_type") or ""),
        "message_id": str(
            raw_event.get("message_id") or raw_event.get("open_message_id") or ""
        ),
        "parent_id": str(raw_event.get("parent_id") or ""),
        "root_id": str(raw_event.get("root_id") or ""),
        "thread_id": str(raw_event.get("thread_id") or ""),
        "upper_message_id": str(raw_event.get("upper_message_id") or ""),
        "content": _extract_command_text(
            raw_event.get("content"),
            message_type=str(raw_event.get("message_type") or ""),
        ),
    }


def _extract_sender_open_id(sender: object) -> str:
    if not isinstance(sender, dict):
        return ""
    sender_id = sender.get("sender_id")
    if isinstance(sender_id, dict):
        return str(sender_id.get("open_id") or sender_id.get("user_id") or "")
    return str(sender.get("open_id") or sender.get("user_id") or "")


def _reply_target_message_id(event: dict, command_message_id: str) -> str:
    for key in ("parent_id", "upper_message_id", "root_id"):
        value = str(event.get(key) or "")
        if not value:
            continue
        if key == "root_id" and value == command_message_id:
            continue
        return value
    return ""


def _message_type(message: dict) -> str:
    return str(message.get("msg_type") or message.get("message_type") or "").lower()


def _message_content(message: dict, message_type: str) -> str:
    content: object = message.get("content")
    if content in (None, ""):
        body = message.get("body")
        if isinstance(body, dict):
            content = body.get("content")
    if not isinstance(content, str):
        return ""
    return _extract_command_text(content, message_type=message_type)


def _is_card_message(message: dict) -> bool:
    message_type = _message_type(message)
    if message_type in {"interactive", "card"}:
        return True
    content = _message_content(message, message_type)
    return content.lstrip().startswith("<card")


def _extract_command_text(content: object, *, message_type: str) -> str:
    if not isinstance(content, str):
        return ""
    if content.startswith("/"):
        return content
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return content
    if not isinstance(payload, dict):
        return content
    if message_type == "text":
        return str(payload.get("text") or "")
    if message_type == "post":
        texts: list[str] = []
        _collect_post_text(payload, texts)
        return "".join(texts)
    return content


def _collect_post_text(node: object, out: list[str]) -> None:
    if isinstance(node, dict):
        tag = node.get("tag")
        if tag == "text":
            text = node.get("text")
            if text:
                out.append(str(text))
        for child in node.values():
            _collect_post_text(child, out)
        return
    if isinstance(node, list):
        for child in node:
            _collect_post_text(child, out)
