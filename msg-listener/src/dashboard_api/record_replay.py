"""record.json 回放投影与媒体子路径受限解析。

把磁盘上的 record.json 投影为前端 IM 回放所需的稳定结构，并对媒体子路径做
受限解析，从根上挡住越权读取 record 目录之外文件的路径穿越。

之所以在这里做大量「防御式默认值」：record.json 的回放字段
（chat_name / source / anchor_index / is_self / index / position 以及 media[]）
是后续才增补的，磁盘上仍存在大量旧 record 缺这些字段；投影必须对新旧两种结构
都能稳定产出，否则旧记录会渲染失败或丢失媒体。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..common import paths as paths_mod


def build_replay(record_id: str, record_dir: Path) -> dict[str, Any] | None:
    """读取 record.json 并投影为回放结构；文件缺失或不可解析时返回 None（交由上层 404）。"""
    record_json = paths_mod.record_json_path(record_dir)
    if not record_json.is_file():
        return None
    try:
        raw = json.loads(record_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        # 文件损坏或编码异常时按「不存在」降级，避免把 500 抛给前端。
        return None
    if not isinstance(raw, dict):
        return None

    raw_messages = raw.get("messages")
    if not isinstance(raw_messages, list):
        raw_messages = []

    # anchor_index 优先取已写入的字段；旧 record 缺失时按 before 条数推断
    #（锚点恰好插在所有 before 之后），再退化不出来则回退 0（锚点排最前）。
    anchor_index = raw.get("anchor_index")
    if not isinstance(anchor_index, int):
        anchor_index = sum(
            1
            for m in raw_messages
            if isinstance(m, dict) and m.get("position") == "before"
        )

    messages = [
        _project_message(record_id, m, idx, anchor_index)
        for idx, m in enumerate(raw_messages)
        if isinstance(m, dict)
    ]

    anchor = raw.get("anchor")
    projected_anchor = _project_node(record_id, anchor) if isinstance(anchor, dict) else {}

    return {
        # record.json 自身字段优先，缺失才回退到索引里的 record_id。
        "record_id": raw.get("record_id") or record_id,
        "chat_type": raw.get("chat_type") or "",
        "chat_id": raw.get("chat_id") or "",
        "chat_name": raw.get("chat_name") or "",
        "source": raw.get("source") or "",
        "anchor_index": anchor_index,
        "anchor": projected_anchor,
        "messages": messages,
    }


def _project_message(
    record_id: str, msg: dict[str, Any], idx: int, anchor_index: int
) -> dict[str, Any]:
    """投影单条消息，补齐回放定位所需的 index / position。"""
    node = _project_node(record_id, msg)
    # index 缺失用数组下标兜底，保证回放定位与引用稳定。
    index = msg.get("index")
    node["index"] = index if isinstance(index, int) else idx
    # position 缺失时按 index 与 anchor_index 的关系推断 before/after。
    position = msg.get("position")
    if position not in ("before", "after"):
        position = "before" if node["index"] < anchor_index else "after"
    node["position"] = position
    return node


def _project_node(record_id: str, node: dict[str, Any]) -> dict[str, Any]:
    """投影 anchor / message 公共字段，统一缺失默认值。"""
    return {
        "sender_name": node.get("sender_name") or "",
        # is_self 缺失默认 False，回放时旧记录一律按对方气泡（左侧）渲染。
        "is_self": bool(node.get("is_self", False)),
        "create_time": node.get("create_time") or "",
        "type": node.get("type") or "",
        "text": node.get("text") or "",
        "links": _project_links(node.get("links")),
        "media": _project_media(record_id, node),
    }


def _project_links(links: Any) -> list[dict[str, str]]:
    if not isinstance(links, list):
        return []
    out: list[dict[str, str]] = []
    for link in links:
        if isinstance(link, dict):
            out.append({"url": link.get("url") or "", "text": link.get("text") or ""})
    return out


def _project_media(record_id: str, node: dict[str, Any]) -> list[dict[str, str]]:
    """把 media[].local_path 转成受限媒体端点 URL。

    新 record 使用 media[] 数组；旧 record 没有 media[] 但消息体上挂了顶层
    local_path，这里合成一条媒体，否则旧记录的图片/视频在回放里会全部丢失。
    """
    items = node.get("media")
    projected: list[dict[str, str]] = []
    if isinstance(items, list) and items:
        for item in items:
            if not isinstance(item, dict):
                continue
            local_path = item.get("local_path") or ""
            if not local_path:
                continue
            projected.append(
                _media_entry(record_id, item.get("kind"), item.get("name"), local_path)
            )
        return projected

    # 兼容旧结构：顶层 local_path 合成单条媒体。
    local_path = node.get("local_path")
    if isinstance(local_path, str) and local_path:
        kind = _infer_kind(node.get("type"), local_path)
        projected.append(_media_entry(record_id, kind, None, local_path))
    return projected


def _media_entry(
    record_id: str, kind: Any, name: Any, local_path: str
) -> dict[str, str]:
    rel = _normalize_rel(local_path)
    return {
        "kind": (kind if isinstance(kind, str) and kind else _infer_kind(None, local_path)),
        "name": (name if isinstance(name, str) and name else Path(rel).name),
        # local_path 是相对 record 目录的路径，转成受限媒体端点 URL，
        # 前端据此加载，不直接触碰文件系统。
        "url": f"/api/records/{record_id}/media/{rel}",
    }


def _normalize_rel(local_path: str) -> str:
    """把 record.json 里的相对路径（形如 ./media/image/x.jpg）规整成纯相对子路径。"""
    rel = local_path.replace("\\", "/")
    if rel.startswith("./"):
        rel = rel[2:]
    return rel.lstrip("/")


def _infer_kind(msg_type: Any, local_path: str) -> str:
    """媒体类型缺省推断：优先看落盘目录，再看消息类型，兜底按图片。"""
    lp = (local_path or "").lower()
    if "/video/" in lp or msg_type in ("video", "media"):
        return "video"
    return "image"


def resolve_within(base: Path, rel: str) -> Path | None:
    """把相对子路径在 base 目录内安全解析；越界（../ 或绝对路径）返回 None。

    先 resolve() 再用 is_relative_to 校验真实路径仍位于 record 目录之内，
    这样无论传入 ../、绝对路径还是符号链接拼接，都无法逃出 record 目录。
    """
    base_resolved = base.resolve()
    target = (base_resolved / rel).resolve()
    if not target.is_relative_to(base_resolved):
        return None
    return target
