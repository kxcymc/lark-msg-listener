"""路径规则（按方案 5.2 / 6.1）。"""
from __future__ import annotations

from pathlib import Path

from .utils import to_local_date


def record_dir(out_root: Path, anchor_create_time_ms: str, chat_type: str, chat_id: str, anchor_message_id: str) -> Path:
    date = to_local_date(anchor_create_time_ms) or "unknown-date"
    safe_chat_type = chat_type if chat_type in {"p2p", "group"} else "other"
    return out_root / date / safe_chat_type / chat_id / anchor_message_id


def record_json_path(record_dir_path: Path) -> Path:
    return record_dir_path / "record.json"


def media_image_dir(record_dir_path: Path) -> Path:
    return record_dir_path / "media" / "image"


def media_video_dir(record_dir_path: Path) -> Path:
    return record_dir_path / "media" / "video"


def index_db_path(out_root: Path) -> Path:
    return out_root / "index.sqlite"
