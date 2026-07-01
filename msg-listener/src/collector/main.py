"""常驻服务入口：asyncio 管道编排。

流：
  poller (@我消息轮询，持久化游标 + overlap)
    → 跳过 seen 消息
    → filter (命中)
    → 等待到锚点+after_max_wait 的绝对时间，再用 API 拉后文
    → context (前 N 条 + 后 N 条，跳过已 seen)
    → media (下载图片/视频)
    → sink (写 record + 登记 seen + 写 index)
"""
from __future__ import annotations

import asyncio
import os
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

try:
    import tomllib  # py311+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

from .command_filter import is_command_message
from ..common.command_parser import parse_command
from .context import (
    CHAT_NAME_KEY,
    build_anchor,
    collect_after,
    collect_before,
    resolve_chat_name,
)
from ..common.filter import HitFilter, OwnerCache
from ..common.index import IndexDB
from ..common.paths import index_db_path
from .permissions import PermissionCheckError, ensure_permissions
from .poller import AtMePoller, is_bot_sender_event
from .sink import RecordSink
from ..common.process_lifecycle import (
    install_asyncio_shutdown_handlers,
    log_process_started,
    watch_parent_process,
)
from ..common.logging_setup import configure_logging

logger = logging.getLogger("msg-listener")


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.toml"


def load_config() -> dict:
    cfg_path = PROJECT_ROOT / "config.toml"
    if not cfg_path.exists():
        return {}
    with cfg_path.open("rb") as f:
        return tomllib.load(f)


def setup_logging(level: str) -> None:
    configure_logging(service_name="collector", level=level)


def config_bool(cfg: dict, key: str, default: bool) -> bool:
    value = cfg.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def config_str_set(cfg: dict, key: str) -> set[str]:
    value = cfg.get(key) or []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}


def _render_toml_string_array(values: set[str]) -> str:
    return "[" + ", ".join(json.dumps(v, ensure_ascii=False) for v in sorted(values)) + "]"


def add_ignored_p2p_chat_id(config_path: Path, chat_id: str) -> set[str]:
    """把机器人单聊 chat_id 写回 config.toml，保留其它配置内容。"""
    cfg = load_config()
    values = config_str_set(cfg, "ignored_p2p_chat_ids")
    if chat_id in values:
        return values
    values.add(chat_id)
    rendered = _render_toml_string_array(values)
    text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    # 保留原文件行尾（Windows 下通常是 CRLF），避免 git diff 因换行符波动而满屏。
    sep = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    line_re = re.compile(r"^\s*ignored_p2p_chat_ids\s*=")
    insert_at = len(lines)
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            insert_at = idx
            break
        if line_re.match(line):
            indent = line[: len(line) - len(line.lstrip())]
            lines[idx] = f"{indent}ignored_p2p_chat_ids = {rendered}"
            tail = sep if text.endswith(("\r\n", "\n")) else ""
            config_path.write_text(sep.join(lines) + tail, encoding="utf-8")
            return values
    lines.insert(insert_at, f"ignored_p2p_chat_ids = {rendered}")
    config_path.write_text(sep.join(lines) + sep, encoding="utf-8")
    return values


class App:
    def __init__(self, cfg: dict) -> None:
        self.context_before: int = int(cfg.get("context_before", 10))
        self.context_after: int = int(cfg.get("context_after", 10))
        self.context_after_max_wait: float = float(cfg.get("context_after_max_wait_seconds", 600))
        self.out_dir = (PROJECT_ROOT / "out").resolve()
        self.poll_interval = float(cfg.get("poll_interval_seconds", 10))
        self.poll_overlap = float(cfg.get("poll_overlap_seconds", 30))
        self.poll_bootstrap_lookback = float(
            cfg.get("poll_bootstrap_lookback_seconds", cfg.get("poll_lookback_seconds", 60))
        )
        self.poll_page_size = int(cfg.get("poll_page_size", 50))
        self.poll_page_limit = int(cfg.get("poll_page_limit", 2))
        self.poll_group_at_me_enabled = config_bool(cfg, "poll_group_at_me_enabled", True)
        self.poll_p2p_enabled = config_bool(cfg, "poll_p2p_enabled", True)
        self.ignored_p2p_chat_ids = config_str_set(cfg, "ignored_p2p_chat_ids")
        self.filter_sticker = config_bool(cfg, "filter_sticker", True)
        self.filter_emoji_only = config_bool(cfg, "filter_emoji_only", True)

        commands_cfg = cfg.get("commands") or {}
        self.commands_prefix = str(commands_cfg.get("prefix", "/")) or "/"
        self.commands_ignore_in_collector = bool(
            commands_cfg.get("ignore_in_collector", True)
        )

        self.cache_dir = PROJECT_ROOT / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)

        self.owner = OwnerCache(self.cache_dir)
        self.hit_filter = HitFilter(
            self.owner,
            filter_sticker=self.filter_sticker,
            filter_emoji_only=self.filter_emoji_only,
        )
        self.index_db = IndexDB(index_db_path(self.out_dir))
        self.sink = RecordSink(self.out_dir, self.index_db)
        self.poller: AtMePoller | None = None
        self.owner_open_id = ""

        self.seen_message_ids: set[str] = set()
        self._lock = asyncio.Lock()
        self._chat_locks: dict[str, asyncio.Lock] = {}

    def _chat_lock(self, chat_id: str) -> asyncio.Lock:
        lock = self._chat_locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._chat_locks[chat_id] = lock
        return lock

    async def run(self) -> None:
        try:
            await ensure_permissions()
        except PermissionCheckError as exc:
            logger.error("权限预检失败：%s", exc)
            sys.exit(1)

        # 提前缓存 owner，避免首次命中时阻塞
        try:
            owner_id = await self.owner.load_or_fetch()
            self.owner_open_id = owner_id
            if not os.environ.get("MSG_LISTENER_OWNER_OPEN_ID"):
                logger.info("owner_open_id = %s", owner_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("获取 owner open_id 失败: %s", exc)
            sys.exit(1)

        self.poller = AtMePoller(
            owner_open_id=owner_id,
            index_db=self.index_db,
            interval_seconds=self.poll_interval,
            overlap_seconds=self.poll_overlap,
            bootstrap_lookback_seconds=self.poll_bootstrap_lookback,
            page_size=self.poll_page_size,
            page_limit=self.poll_page_limit,
            group_at_me_enabled=self.poll_group_at_me_enabled,
            p2p_enabled=self.poll_p2p_enabled,
            ignored_p2p_chat_ids=self.ignored_p2p_chat_ids,
            discover_owner_p2p_commands=self.commands_ignore_in_collector,
            command_prefix=self.commands_prefix,
        )
        async for event in self.poller.stream():
            await self._dispatch(event)

    async def _dispatch(self, event: dict) -> None:
        chat_id = event.get("chat_id") or ""
        message_id = event.get("message_id") or ""
        chat_type = event.get("chat_type") or ""
        message_type = event.get("message_type") or ""

        if chat_type == "p2p" and chat_id in self.ignored_p2p_chat_ids:
            logger.info(
                "跳过已配置忽略的机器人单聊：chat_id=%s message_id=%s",
                chat_id,
                message_id or "-",
            )
            return

        if chat_type == "p2p" and is_bot_sender_event(event):
            try:
                self.ignored_p2p_chat_ids = add_ignored_p2p_chat_id(CONFIG_PATH, chat_id)
                if self.poller is not None:
                    self.poller.ignored_p2p_chat_ids = self.ignored_p2p_chat_ids
                logger.info(
                    "发现机器人单聊推送，已写入忽略会话：chat_id=%s sender=%s message_id=%s",
                    chat_id,
                    event.get("sender_id") or "-",
                    message_id or "-",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("写入机器人单聊 ignored_p2p_chat_ids 失败：%s", exc)
            return

        if chat_type == "p2p" and (event.get("chat_partner_open_id") or "") == self.owner_open_id and self.owner_open_id:
            try:
                self.ignored_p2p_chat_ids = add_ignored_p2p_chat_id(CONFIG_PATH, chat_id)
                if self.poller is not None:
                    self.poller.ignored_p2p_chat_ids = self.ignored_p2p_chat_ids
                logger.info(
                    "发现 owner 自己与自己的单聊，已写入忽略会话：chat_id=%s message_id=%s",
                    chat_id,
                    message_id or "-",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("写入 owner self-chat ignored_p2p_chat_ids 失败：%s", exc)
            return

        if self._is_owner_p2p_command(event):
            try:
                self.ignored_p2p_chat_ids = add_ignored_p2p_chat_id(CONFIG_PATH, chat_id)
                if self.poller is not None:
                    self.poller.ignored_p2p_chat_ids = self.ignored_p2p_chat_ids
                logger.info(
                    "发现 owner 与机器人单聊命令，已写入忽略会话：chat_id=%s message_id=%s",
                    chat_id,
                    message_id or "-",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("写入 ignored_p2p_chat_ids 失败：%s", exc)
            return

        # 机器人单聊命令短路，避免命令文本被收集为 record。
        # 不写 seen_messages 也不调用 hit filter，仅丢弃事件。
        if (
            self.commands_ignore_in_collector
            and is_command_message(
                event,
                prefix=self.commands_prefix,
            )
        ):
            return

        if message_id:
            async with self._lock:
                if message_id in self.seen_message_ids:
                    logger.info("消息已处理，跳过重复：message_id=%s", message_id)
                    return
                self.seen_message_ids.add(message_id)
            if self.index_db.is_message_seen(message_id):
                logger.info("消息已收集过，跳过重复：message_id=%s", message_id)
                return
        logger.info(
            "收到消息事件：%s/%s type=%s message_id=%s",
            chat_type or "-",
            chat_id or "-",
            message_type or "-",
            message_id or "-",
        )

        try:
            hit = await self.hit_filter.is_hit(event)
        except Exception as exc:  # noqa: BLE001
            logger.exception("命中判断异常: %s", exc)
            return
        if not hit:
            logger.info("消息未命中/已过滤：message_id=%s", message_id or "-")
            return

        logger.info(
            "@我命中：%s/%s message_id=%s",
            chat_type,
            chat_id,
            message_id,
        )

        try:
            await self._handle_hit(event)
        except Exception as exc:  # noqa: BLE001
            logger.exception("处理命中失败: %s", exc)

    def _is_owner_p2p_command(self, event: dict) -> bool:
        if not self.commands_ignore_in_collector:
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
        return parse_command(text, prefix=self.commands_prefix) is not None

    async def _handle_hit(self, event: dict) -> None:
        message_id = event.get("message_id") or ""
        chat_id = event.get("chat_id") or ""
        try:
            anchor_create_time_ms = int(event.get("create_time") or 0)
        except (TypeError, ValueError):
            anchor_create_time_ms = 0

        # 按锚点绝对时间等待 after 窗口结束：
        # 旧消息（已经超过 after_max_wait）不再额外等待；新消息严格等满。
        now_ms = int(time.time() * 1000)
        deadline_ms = anchor_create_time_ms + int(self.context_after_max_wait * 1000)
        wait_seconds = max(0.0, (deadline_ms - now_ms) / 1000.0)

        logger.info(
            "开始聚合上下文：前 %d 条 + 等待 %.1fs（直到锚点+%.0fs）后拉取后 %d 条",
            self.context_before,
            wait_seconds,
            self.context_after_max_wait,
            self.context_after,
        )

        # 同一 chat 串行处理 anchor，避免两个并发 anchor 抢同一段 after。
        chat_lock = self._chat_lock(chat_id)
        async with chat_lock:
            anchor_task = asyncio.create_task(build_anchor(message_id, event))
            before_task = asyncio.create_task(
                collect_before(
                    chat_id,
                    anchor_create_time_ms,
                    self.context_before,
                    exclude_message_ids={message_id},
                    is_message_seen=self.index_db.is_message_seen,
                    filter_sticker=self.filter_sticker,
                    filter_emoji_only=self.filter_emoji_only,
                )
            )

            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
            logger.info("等待结束，开始通过 API 拉取后续消息")

            anchor = await anchor_task
            chat_name = (
                event.get("chat_name")
                or anchor.get(CHAT_NAME_KEY)
                or await resolve_chat_name(event)
            )
            if chat_name:
                event = {**event, "chat_name": chat_name}
            else:
                logger.warning(
                    "未能解析会话名，record.json.chat_name 将为空：chat_type=%s chat_id=%s message_id=%s",
                    event.get("chat_type") or "-",
                    chat_id or "-",
                    message_id or "-",
                )
            before = await before_task
            exclude_after = {message_id}
            exclude_after.update(
                msg.get("message_id") or "" for msg in before if msg.get("message_id")
            )
            after = await collect_after(
                chat_id,
                anchor_create_time_ms,
                self.context_after,
                message_id,
                exclude_message_ids=exclude_after,
                is_message_seen=self.index_db.is_message_seen,
                filter_sticker=self.filter_sticker,
                filter_emoji_only=self.filter_emoji_only,
            )

            await self.sink.write_record(
                anchor_event=event,
                anchor=anchor,
                before=before,
                after=after,
            )


async def amain() -> None:
    startup_parent_pid = os.getppid()
    cfg = load_config()
    setup_logging(str(cfg.get("log_level", "INFO")))
    log_process_started(logger, "collector")
    shutdown_event = asyncio.Event()
    install_asyncio_shutdown_handlers(
        logger=logger,
        service_name="collector",
        shutdown_event=shutdown_event,
    )
    app = App(cfg)
    app_task = asyncio.create_task(app.run(), name="collector-app")
    shutdown_task = asyncio.create_task(shutdown_event.wait(), name="collector-shutdown")
    parent_task = asyncio.create_task(
        watch_parent_process(
            logger=logger,
            service_name="collector",
            initial_parent_pid=startup_parent_pid,
            shutdown_event=shutdown_event,
        ),
        name="collector-parent-watch",
    )
    try:
        done, _ = await asyncio.wait(
            {app_task, shutdown_task, parent_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if app_task in done and app_task.exception() is not None:
            raise app_task.exception()
    finally:
        shutdown_event.set()
        if not app_task.done():
            app_task.cancel()
        for task in (shutdown_task, parent_task):
            if not task.done():
                task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    app_task,
                    shutdown_task,
                    parent_task,
                    return_exceptions=True,
                ),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            logger.error("collector 退出超时，强制终止进程 pid=%s", os.getpid())
            os._exit(1)


def main() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
