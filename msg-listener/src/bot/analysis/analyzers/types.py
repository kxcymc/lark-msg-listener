"""分析器对外协议与异常类。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

CATEGORY_REQUIREMENT = "requirement"
CATEGORY_QUESTION = "question"
CATEGORY_CHAT = "chat"
CATEGORY_NOISE = "noise"
CATEGORY_UNKNOWN = "unknown"

VALID_CATEGORIES = {
    CATEGORY_REQUIREMENT,
    CATEGORY_QUESTION,
    CATEGORY_CHAT,
    CATEGORY_NOISE,
    CATEGORY_UNKNOWN,
}


@dataclass
class AnalyzerInput:
    """送入 analyzer 的标准输入。"""

    record_id: str
    record_path: str
    chat_type: str
    anchor: dict
    messages: list[dict] = field(default_factory=list)
    media: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "record_path": self.record_path,
            "chat_type": self.chat_type,
            "anchor": self.anchor,
            "messages": self.messages,
            "media": self.media,
        }


@dataclass
class AnalyzerResult:
    """analyzer 归一化输出。"""

    category: str
    prompt: str
    summary: str
    evidence: list[str]
    reason: str
    raw: dict = field(default_factory=dict)


class AnalyzerError(RuntimeError):
    """分析器运行失败。"""

    def __init__(
        self,
        message: str,
        *,
        retriable: bool = False,
        raw_stdout: str = "",
        raw_stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.retriable = retriable
        self.raw_stdout = raw_stdout
        self.raw_stderr = raw_stderr


class AnalyzerRateLimited(AnalyzerError):
    """限流/服务繁忙；调用方需要退避。"""

    def __init__(self, message: str, *, raw_stdout: str = "", raw_stderr: str = "") -> None:
        super().__init__(
            message,
            retriable=True,
            raw_stdout=raw_stdout,
            raw_stderr=raw_stderr,
        )


def normalize_record_to_input(record: dict, record_path: str) -> AnalyzerInput:
    """把 record.json 拍平成 AnalyzerInput。"""
    record_id = str(record.get("record_id") or "")
    chat_type = str(record.get("chat_type") or "")
    record_root = Path(record_path).resolve()
    anchor = record.get("anchor") or {}
    anchor_view = {
        "sender_name": anchor.get("sender_name") or "",
        "create_time": anchor.get("create_time") or "",
        "type": anchor.get("type") or "",
        "text": anchor.get("text") or "",
        "links": [str(link) for link in anchor.get("links") or [] if link],
        "local_path": anchor.get("local_path") or "",
        "media": _normalize_message_media(anchor, record_root),
    }

    messages: list[dict] = []
    media: list[dict] = []
    _append_message_media(media, anchor, record_root)
    for msg in record.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        position = msg.get("position") or "before"
        msg_type = msg.get("type") or ""
        item = {
            "position": position,
            "sender_name": msg.get("sender_name") or "",
            "create_time": msg.get("create_time") or "",
            "type": msg_type,
            "text": msg.get("text") or "",
            "links": [str(link) for link in msg.get("links") or [] if link],
            "media": _normalize_message_media(msg, record_root),
        }
        messages.append(item)
        _append_message_media(media, msg, record_root)
    return AnalyzerInput(
        record_id=record_id,
        record_path=record_path,
        chat_type=chat_type,
        anchor=anchor_view,
        messages=messages,
        media=media,
    )


def _append_message_media(media: list[dict], msg: dict, record_root: Path) -> None:
    explicit = msg.get("media")
    if isinstance(explicit, list):
        for item in explicit:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "")
            local_path = str(item.get("local_path") or "")
            if kind and local_path:
                _append_media(media, kind=kind, local_path=local_path, record_root=record_root)
        return
    msg_type = str(msg.get("type") or "")
    kind = _kind_from_message_type(msg_type)
    if kind:
        _append_media(
            media,
            kind=kind,
            local_path=str(msg.get("local_path") or ""),
            record_root=record_root,
        )


def _normalize_message_media(msg: dict, record_root: Path) -> list[dict]:
    normalized: list[dict] = []
    _append_message_media(normalized, msg, record_root)
    return normalized


def _append_media(
    media: list[dict],
    *,
    kind: str,
    local_path: str,
    record_root: Path,
) -> None:
    if not local_path:
        return
    readable_path = _resolve_record_media_path(record_root, local_path)
    entry = {
        "kind": kind,
        "local_path": readable_path,
        "record_relative_path": local_path,
    }
    if entry not in media:
        media.append(entry)


def _kind_from_message_type(msg_type: str) -> str:
    if msg_type == "image":
        return "image"
    if msg_type in {"video", "media"}:
        return "video"
    return ""


def _resolve_record_media_path(record_root: Path, local_path: str) -> str:
    if not local_path:
        return ""
    path = Path(local_path)
    if path.is_absolute():
        return str(path)
    return str((record_root / path).resolve())
