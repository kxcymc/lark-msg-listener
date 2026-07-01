"""Sink：创建 record 目录、下载媒体、写 record.json、写索引。"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from . import media as media_mod
from .context import CHAT_NAME_KEY, SENDER_OPEN_ID_KEY
from ..common import paths as paths_mod
from ..common.index import IndexDB

logger = logging.getLogger(__name__)


class RecordSink:
    def __init__(self, out_dir: Path, index_db: IndexDB) -> None:
        self.out_dir = out_dir
        self.index_db = index_db
        # owner open_id 由 supervisor/collector 注入到环境变量（见 src/main.py
        # 与 src/collector/main.py）；用它和每条消息的发送人 open_id 比较得出 is_self。
        # 读不到（未注入）时为空串，此时 is_self 一律 False。
        self.owner_open_id = os.environ.get("MSG_LISTENER_OWNER_OPEN_ID") or ""

    async def write_record(
        self,
        *,
        anchor_event: dict,
        anchor: dict,
        before: list[dict],
        after: list[dict],
    ) -> Path:
        chat_id = anchor_event.get("chat_id") or ""
        chat_type = anchor_event.get("chat_type") or ""
        anchor_message_id = anchor_event.get("message_id") or ""
        anchor_create_time_ms = anchor_event.get("create_time") or ""

        record_dir = paths_mod.record_dir(
            self.out_dir, anchor_create_time_ms, chat_type, chat_id, anchor_message_id
        )
        image_dir = paths_mod.media_image_dir(record_dir)
        video_dir = paths_mod.media_video_dir(record_dir)
        record_dir.mkdir(parents=True, exist_ok=True)
        image_dir.mkdir(parents=True, exist_ok=True)
        video_dir.mkdir(parents=True, exist_ok=True)

        await media_mod.download_for_message(
            anchor,
            record_dir=record_dir,
            image_dir=image_dir,
            video_dir=video_dir,
        )
        # 顺序合并 before + after，按位置标签处理
        for msg in before + after:
            await media_mod.download_for_message(
                msg,
                record_dir=record_dir,
                image_dir=image_dir,
                video_dir=video_dir,
            )

        # 合并 before + after 为有序消息序列；before 在前、after 在后，
        # 数组下标即回放定位用的 index。anchor 单独成字段，不进数组、不需要 index。
        ordered = before + after
        # anchor_index = 锚点在 before 序列之后的插入位置 = before 中条数。
        # 当前实现里 messages 已是 before 在前，故等于 messages 中 position=="before" 的条数。
        anchor_index = sum(1 for msg in ordered if (msg.get("position") or "") == "before")

        # 清洗每条消息：补 is_self/index，移除内部 ID（message_id 与临时 sender open_id）。
        cleaned: list[dict] = []
        for idx, msg in enumerate(ordered):
            m = self._clean_node(msg)
            m["index"] = idx  # 数组顺序下标，回放定位与引用以此为准
            cleaned.append(m)
        cleaned_anchor = self._clean_node(anchor)  # anchor 不需要 index

        record = {
            "record_id": anchor_message_id,
            "chat_type": chat_type,
            "chat_id": chat_id,
            # 优先用 poller 事件里的会话名；mget 若补到了会话信息，再作为兜底。
            "chat_name": anchor_event.get("chat_name") or anchor.get(CHAT_NAME_KEY) or "",
            "source": "collector",  # record 来源标记，当前固定 collector
            "anchor_index": anchor_index,
            "anchor": cleaned_anchor,
            "messages": cleaned,
        }
        record_json_path = paths_mod.record_json_path(record_dir)
        record_json_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 写索引
        self.index_db.upsert(
            record_id=anchor_message_id,
            chat_type=chat_type,
            chat_id=chat_id,
            sender_name=anchor.get("sender_name") or "",
            anchor_time=anchor.get("create_time") or "",
            record_path=str(record_dir),
        )
        seen_messages = [(anchor_message_id, "anchor")]
        seen_messages.extend(
            (msg.get("message_id") or "", msg.get("position") or "")
            for msg in before + after
        )
        self.index_db.mark_messages_seen(
            record_id=anchor_message_id,
            messages=seen_messages,
        )

        # 控制台摘要
        all_media_sources = [cleaned_anchor, *cleaned]
        image_count = sum(_count_media(m, kind="image") for m in all_media_sources)
        video_count = sum(_count_media(m, kind="video") for m in all_media_sources)
        link_count = sum(len(m.get("links") or []) for m in cleaned) + len(
            anchor.get("links") or []
        )
        logger.info(
            "上下文聚合完成：前 %d + 后 %d，%d 张图 + %d 个视频 + %d 个链接",
            len(before),
            len(after),
            image_count,
            video_count,
            link_count,
        )
        logger.debug("已写入 %s", record_json_path)
        logger.debug("已更新 %s（record_id=%s）", paths_mod.index_db_path(self.out_dir), anchor_message_id)

        return record_dir

    def _clean_node(self, node: dict) -> dict:
        """清洗单个 anchor / message 节点用于落盘。

        - 计算 is_self：发送人 open_id 与 owner 比较（owner 缺失时一律 False）；
        - 移除内部 ID：message_id 与临时承载 open_id 的 SENDER_OPEN_ID_KEY，
          按「事实数据不暴露内部 ID」的既有约定，record.json 仅保留 is_self。
        其余字段（sender_name/create_time/type/text/links/media/position）原样保留。
        """
        m = dict(node)
        sender_open_id = m.pop(SENDER_OPEN_ID_KEY, "") or ""
        m.pop(CHAT_NAME_KEY, None)
        m.pop("message_id", None)
        # owner 读不到（未注入）或发送人 open_id 为空时，一律按对方消息（is_self=False）。
        m["is_self"] = bool(
            self.owner_open_id and sender_open_id and sender_open_id == self.owner_open_id
        )
        return m


def _count_media(msg: dict, *, kind: str) -> int:
    explicit = msg.get("media")
    if isinstance(explicit, list):
        return sum(
            1
            for item in explicit
            if isinstance(item, dict) and item.get("kind") == kind and item.get("local_path")
        )
    msg_type = msg.get("type") or ""
    if kind == "image" and msg_type == "image" and msg.get("local_path"):
        return 1
    if kind == "video" and msg_type in {"video", "media"} and msg.get("local_path"):
        return 1
    return 0
