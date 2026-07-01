"""业务数据与回放端点（sub-agent A 实现）。

数据来源：
  - records / analysis-jobs / demands / dev-tasks / repos -> repositories 只读查询 index.sqlite
  - replay -> record_replay.py 读取 out/**/record.json 投影为回放结构
  - media  -> 受限读取 out/**/media/** 回传

实现要求：
  - 列表端点支持分页（query: page, page_size），返回 {"items": [...], "total": N, "page": ..., "page_size": ...}
  - 详情/replay 未找到时返回 Response.error(404, ...)
  - media 必须做路径穿越防护：解析后的真实路径必须位于对应 record 目录之内
"""
from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any

from .. import repositories as repo
from ..record_replay import build_replay, resolve_within
from ..router import Request, Response

# 分页 page_size 上限，避免一次性拉取过多行拖垮只读连接。
_MAX_PAGE_SIZE = 200

# 详情返回时需要把存了 JSON 字符串的列还原成对象，按表分别登记列名。
_JSON_COLUMNS = {
    "demands": ("evidence_json", "media_json"),
    "dev_tasks": ("raw_snapshot_json",),
    "repo_inventory": ("remotes_json", "branches_json"),
    "analysis_jobs": ("raw_output_json",),
}


def _page_params(request: Request) -> tuple[int, int]:
    """解析并夹紧分页参数：page>=1，1<=page_size<=上限。"""
    page = request.query_int("page", 1)
    page_size = request.query_int("page_size", 20)
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 20
    if page_size > _MAX_PAGE_SIZE:
        page_size = _MAX_PAGE_SIZE
    return page, page_size


def _expand_json_columns(row: dict[str, Any], table: str) -> dict[str, Any]:
    """把该表登记的 JSON 字符串列尽量 json.loads 成对象；解析失败原样保留字符串。"""
    for col in _JSON_COLUMNS.get(table, ()):
        value = row.get(col)
        if isinstance(value, str) and value:
            try:
                row[col] = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                # 列里可能存的并非合法 JSON（如空串或裸文本），保留原值即可。
                pass
    return row


def _read_record_json(record_path: Any) -> dict[str, Any]:
    if not isinstance(record_path, str) or not record_path:
        return {}
    try:
        raw = json.loads((Path(record_path) / "record.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _expand_record_row(row: dict[str, Any]) -> dict[str, Any]:
    """records 索引表不存 chat_name，列表/详情接口从 record.json 投影补齐。"""
    raw = _read_record_json(row.get("record_path"))
    if not raw:
        return row

    value = raw.get("chat_name") or raw.get("chatName")
    if isinstance(value, str) and value:
        row["chat_name"] = value
    return row


def _empty_page(page: int, page_size: int) -> Response:
    """表不存在时的统一空分页响应，保证前端拿到稳定结构而非报错。"""
    return Response.json({"items": [], "total": 0, "page": page, "page_size": page_size})


def _list(
    request: Request,
    *,
    table: str,
    order_by: str,
    where_sql: str = "",
    where_params: tuple = (),
) -> Response:
    """列表分页的通用实现：COUNT 求总数 + LIMIT/OFFSET 取页。

    where_sql / where_params 用参数化方式拼接过滤条件，值一律走占位符，
    绝不把外部输入拼进 SQL 字符串，杜绝注入。
    """
    page, page_size = _page_params(request)
    if not repo.table_exists(table):
        return _empty_page(page, page_size)

    total = repo.query_scalar(
        f"SELECT COUNT(*) FROM {table}{where_sql}", where_params
    ) or 0
    offset = (page - 1) * page_size
    rows = repo.query_all(
        f"SELECT * FROM {table}{where_sql} ORDER BY {order_by} LIMIT ? OFFSET ?",
        (*where_params, page_size, offset),
    )
    items = [_expand_json_columns(row, table) for row in rows]
    if table == "records":
        items = [_expand_record_row(row) for row in items]
    return Response.json(
        {
            "items": items,
            "total": int(total),
            "page": page,
            "page_size": page_size,
        }
    )


def _detail(table: str, key_col: str, key_value: str) -> Response:
    """按主键取单行详情；未找到返回 404，JSON 列还原为对象后返回。"""
    if not repo.table_exists(table):
        return Response.error(404, "未找到")
    row = repo.query_one(
        f"SELECT * FROM {table} WHERE {key_col} = ?", (key_value,)
    )
    if row is None:
        return Response.error(404, "未找到")
    item = _expand_json_columns(row, table)
    if table == "records":
        item = _expand_record_row(item)
    return Response.json(item)


def list_records(request: Request) -> Response:
    # records 支持按 chat_type / chat_id 过滤；均用参数化条件，禁止字符串拼接值。
    clauses: list[str] = []
    params: list[str] = []
    chat_type = request.query.get("chat_type")
    if chat_type:
        clauses.append("chat_type = ?")
        params.append(chat_type)
    chat_id = request.query.get("chat_id")
    if chat_id:
        clauses.append("chat_id = ?")
        params.append(chat_id)
    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    # records 按锚点时间倒序，最新命中的会话排在最前。
    return _list(
        request,
        table="records",
        order_by="anchor_time DESC",
        where_sql=where_sql,
        where_params=tuple(params),
    )


def get_record(request: Request) -> Response:
    return _detail("records", "record_id", request.params["id"])


def get_record_replay(request: Request) -> Response:
    record_id = request.params["id"]
    # 先经索引拿到 record 落盘目录；未登记的 record_id 直接 404。
    row = repo.query_one(
        "SELECT record_path FROM records WHERE record_id = ?", (record_id,)
    )
    if row is None or not row.get("record_path"):
        return Response.error(404, "未找到")
    replay = build_replay(record_id, Path(row["record_path"]))
    if replay is None:
        # record.json 不存在或不可解析，按未找到处理。
        return Response.error(404, "未找到")
    return Response.json(replay)


def get_record_media(request: Request) -> Response:
    record_id = request.params["id"]
    wildcard = request.params.get("wildcard", "")
    row = repo.query_one(
        "SELECT record_path FROM records WHERE record_id = ?", (record_id,)
    )
    if row is None or not row.get("record_path"):
        return Response.error(404, "未找到")

    base = Path(row["record_path"])
    # 路径穿越防护：解析后的真实路径必须仍位于 record 目录之内。
    target = resolve_within(base, wildcard)
    if target is None:
        return Response.error(403, "禁止访问")
    if not target.is_file():
        return Response.error(404, "未找到")

    try:
        data = target.read_bytes()
    except OSError:
        return Response.error(404, "未找到")
    # 按扩展名推断 content_type，无法识别时兜底二进制流。
    content_type, _ = mimetypes.guess_type(str(target))
    return Response.binary(data, content_type or "application/octet-stream")


def list_analysis_jobs(request: Request) -> Response:
    return _list(request, table="analysis_jobs", order_by="created_at DESC")


def list_demands(request: Request) -> Response:
    return _list(request, table="demands", order_by="created_at DESC")


def get_demand(request: Request) -> Response:
    return _detail("demands", "demand_id", request.params["id"])


def list_dev_tasks(request: Request) -> Response:
    return _list(request, table="dev_tasks", order_by="created_at DESC")


def get_dev_task(request: Request) -> Response:
    return _detail("dev_tasks", "task_id", request.params["id"])


def list_repos(request: Request) -> Response:
    # repo_inventory 无 created_at，按扫描时间倒序，最近扫描的仓库排在前。
    return _list(request, table="repo_inventory", order_by="scanned_at DESC")
