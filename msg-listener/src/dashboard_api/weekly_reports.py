"""扫描 out/mywork/*_mywork_doc.json 组装周报链接只读列表。

之所以把扫描/解析逻辑单独拆到本模块：handler 只负责调度返回，
而周报文件结构（由 src/mywork/concluder.py 写出）与日期解析的细节集中在此，
便于和 mywork 产物结构保持单点对齐。

mywork doc JSON 的实际字段以 concluder.py 写出的为准：
  created_at / provider_name / document_title / returncode /
  document_url / document_token / stdout / stderr / json_path
其中 document_title 形如 "20250101-20250107_上周工作总结"，内嵌日期段；
文件名形如 "20250107_mywork_doc.json"，前缀是该周生成日（= 日期段末端）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import MYWORK_DIR

# document_title 内嵌的日期段，形如 20250101-20250107；用于还原起止日期。
_RANGE_RE = re.compile(r"(\d{8})-(\d{8})")


def collect_weekly_reports() -> list[dict[str, Any]]:
    """扫描 mywork 目录下的周报文档结果，返回按日期倒序的只读列表。

    目录不存在或无文件时返回空列表；单个文件读取/解析失败时跳过该文件并继续，
    避免一个坏文件导致整个端点 500。
    """
    if not MYWORK_DIR.exists():
        return []

    # 文件名前缀即生成日（YYYYMMDD），按其倒序排列即「按日期倒序」；
    # 前缀同为 8 位数字时字典序与时间序一致，文件名兜底保证排序稳定。
    paths = sorted(
        MYWORK_DIR.glob("*_mywork_doc.json"),
        key=lambda p: (_filename_date(p), p.name),
        reverse=True,
    )

    items: list[dict[str, Any]] = []
    for path in paths:
        data = _load_json(path)
        if data is None:
            # 坏文件已在 _load_json 内吞掉异常，这里跳过即可，不影响其余周报。
            continue
        items.append(_build_item(path, data))
    return items


def _build_item(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    """把单个 mywork doc 投影成周报列表项；字段以 concluder.py 实际写出的为准。"""
    title = str(data.get("document_title") or "")
    filename_date = _filename_date(path)

    # 优先从标题内嵌的「起-止」日期段还原；缺失时退化到文件名日期作为末端。
    match = _RANGE_RE.search(title)
    if match:
        start_raw, end_raw = match.group(1), match.group(2)
    else:
        start_raw, end_raw = "", filename_date

    date_start = _fmt_date(start_raw)
    date_end = _fmt_date(end_raw)
    if date_start and date_end:
        date_range = f"{date_start} ~ {date_end}"
    else:
        date_range = date_end

    returncode = data.get("returncode")
    return {
        "file": path.name,
        "date_start": date_start,
        "date_end": date_end,
        "date_range": date_range,
        "title": title,
        "document_url": data.get("document_url") or "",
        "document_token": data.get("document_token") or "",
        "provider_name": data.get("provider_name") or "",
        "returncode": returncode,
        # 生成结果状态：returncode==0 视为成功，其他整数视为失败，缺失则未知。
        "status": _status_of(returncode),
        "created_at": data.get("created_at") or "",
    }


def _status_of(returncode: Any) -> str:
    if returncode == 0:
        return "success"
    if isinstance(returncode, int):
        return "failed"
    return "unknown"


def _filename_date(path: Path) -> str:
    """从 "{YYYYMMDD}_mywork_doc.json" 取前缀日期；不符合则返回空串。"""
    head = path.name.split("_", 1)[0]
    return head if len(head) == 8 and head.isdigit() else ""


def _fmt_date(yyyymmdd: str) -> str:
    """YYYYMMDD -> YYYY-MM-DD；非法输入返回空串，交由前端兜底。"""
    if len(yyyymmdd) == 8 and yyyymmdd.isdigit():
        return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"
    return ""


def _load_json(path: Path) -> dict[str, Any] | None:
    """安全读取单个周报 JSON；解析失败或非对象返回 None 以便上层跳过。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None
