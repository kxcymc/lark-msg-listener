"""概览聚合与 xlsx 导出端点（sub-agent B 实现）。

数据来源：对 index.sqlite 的 records / demands / dev_tasks / analysis_jobs 即时聚合，
以及 out/mywork 周报产物（导出的 WeeklyReports sheet）。

实现要求：
  - overview：核心工作量指标（record/需求/任务/MR/周报计数、成功率/失败率），支持 query 日期范围
  - timeseries：按日趋势（每日 record / 需求 / 任务 / MR 数量）
  - exports：POST，body 携带日期范围与报表类型；用 stdlib zipfile 在内存生成 xlsx 字节流，
    Response.binary 返回，附 Content-Disposition: attachment; filename=...
    （xlsx 即 zip + 固定 OOXML 部件，stdlib 即可生成，禁止新增第三方依赖）
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..router import Request, Response
from .. import aggregations as agg
from .. import repositories as repo
from ..config import MYWORK_DIR
from ..exporter import build_xlsx

# 日期形如 YYYY-MM-DD，用于校验 query/body 里的范围参数，避免把脏值带进 SQL
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _clean_date(value: Any) -> str | None:
    """把任意输入规整为合法日期字符串；非法或缺省返回 None（= 不过滤该侧）。"""
    if not value:
        return None
    text = str(value).strip()
    return text if _DATE_RE.match(text) else None


def _range_from_query(request: Request) -> tuple[str | None, str | None]:
    return _clean_date(request.query.get("start")), _clean_date(request.query.get("end"))


def get_overview(request: Request) -> Response:
    """GET /api/overview：返回核心工作量指标与成功率/失败率。"""
    start, end = _range_from_query(request)
    return Response.json(agg.collect_overview(start, end))


def get_timeseries(request: Request) -> Response:
    """GET /api/timeseries：返回按日聚合的趋势序列。"""
    start, end = _range_from_query(request)
    return Response.json({"series": agg.collect_timeseries(start, end)})


# ---------- xlsx 导出 ----------

# 各明细 sheet 的列定义：(表头中文/英文, 取值列名)。
# 用显式列清单而非 SELECT *，保证导出列顺序稳定、且只暴露需要的字段。
_RECORD_COLUMNS = [
    ("record_id", "record_id"),
    ("chat_type", "chat_type"),
    ("chat_id", "chat_id"),
    ("sender_name", "sender_name"),
    ("anchor_time", "anchor_time"),
    ("created_at", "created_at"),
]
_DEMAND_COLUMNS = [
    ("demand_id", "demand_id"),
    ("record_id", "record_id"),
    ("record_date", "record_date"),
    ("summary", "summary"),
    ("repo", "repo"),
    ("branch", "branch"),
    ("dispatch_status", "dispatch_status"),
    ("mr_url", "mr_url"),
    ("status", "status"),
    ("created_at", "created_at"),
    ("updated_at", "updated_at"),
]
_DEV_TASK_COLUMNS = [
    ("task_id", "task_id"),
    ("demand_id", "demand_id"),
    ("repo_id", "repo_id"),
    ("base_branch", "base_branch"),
    ("work_branch", "work_branch"),
    ("mr_url", "mr_url"),
    ("commit_sha", "commit_sha"),
    ("status", "status"),
    ("attempts", "attempts"),
    ("error", "error"),
    ("created_at", "created_at"),
    ("updated_at", "updated_at"),
]
_REPO_COLUMNS = [
    ("repo_id", "repo_id"),
    ("label", "label"),
    ("local_path", "local_path"),
    ("remotes_json", "remotes_json"),
    ("branches_json", "branches_json"),
    ("scanned_at", "scanned_at"),
]


def _table_rows(
    table: str,
    columns: list[tuple[str, str]],
    *,
    date_col: str | None,
    start: str | None,
    end: str | None,
    order_by: str | None = None,
) -> list[list[Any]]:
    """读取一张表的明细，返回「表头 + 数据行」二维列表；表不存在仅返回表头。

    date_col 给定时按范围过滤（值已规整为 YYYY-MM-DD 的表达式）。
    """
    header = [label for label, _col in columns]
    if not repo.table_exists(table):
        return [header]

    select_cols = ", ".join(col for _label, col in columns)
    where, params = "", []
    if date_col:
        clauses = []
        if start:
            clauses.append(f"{date_col} >= ?")
            params.append(start)
        if end:
            clauses.append(f"{date_col} <= ?")
            params.append(end)
        if clauses:
            where = " WHERE " + " AND ".join(clauses)
    order = f" ORDER BY {order_by}" if order_by else ""
    sql = f"SELECT {select_cols} FROM {table}{where}{order}"
    rows = repo.query_all(sql, tuple(params))

    data = [header]
    for r in rows:
        data.append([r.get(col) for _label, col in columns])
    return data


def _summary_rows(start: str | None, end: str | None) -> list[list[Any]]:
    """Summary sheet：把 overview 指标平铺成「指标/数值」两列。"""
    ov = agg.collect_overview(start, end)
    succ = ov["dev_task_success"]
    rows: list[list[Any]] = [["指标", "数值"]]
    rows.append(["日期范围 起", start or "（全部）"])
    rows.append(["日期范围 止", end or "（全部）"])
    rows.append(["Record 总数", ov["records_total"]])
    rows.append(["需求总数", ov["demands_total"]])
    rows.append(["开发任务总数", ov["dev_tasks_total"]])
    rows.append(["分析任务总数", ov["analysis_jobs_total"]])
    rows.append(["MR 总数", ov["mr_total"]])
    rows.append(["MR（需求侧）", ov["mr_breakdown"]["demands"]])
    rows.append(["MR（开发任务侧）", ov["mr_breakdown"]["dev_tasks"]])
    rows.append(["开发任务 成功数", succ["succeeded"]])
    rows.append(["开发任务 失败数", succ["failed"]])
    rows.append(["开发任务 成功率", succ["success_rate"]])
    rows.append(["开发任务 失败率", succ["failure_rate"]])
    # 分析任务各状态计数逐项展开，便于在表格中查看
    for status, n in ov["analysis_status_counts"].items():
        rows.append([f"分析任务[{status}]", n])
    return rows


def _daily_metric_rows(start: str | None, end: str | None) -> list[list[Any]]:
    """DailyMetrics sheet：复用 timeseries 聚合逻辑。"""
    header = ["date", "records", "demands", "dev_tasks", "mrs"]
    rows: list[list[Any]] = [header]
    for point in agg.collect_timeseries(start, end):
        rows.append(
            [
                point["date"],
                point["records"],
                point["demands"],
                point["dev_tasks"],
                point["mrs"],
            ]
        )
    return rows


def _weekly_report_rows() -> list[list[Any]]:
    """WeeklyReports sheet：扫描 out/mywork/*_mywork_doc.json；目录/文件缺失返回仅表头。"""
    header = [
        "file",
        "created_at",
        "document_title",
        "document_url",
        "document_token",
        "provider_name",
        "returncode",
    ]
    rows: list[list[Any]] = [header]
    if not MYWORK_DIR.exists():
        return rows
    for path in sorted(MYWORK_DIR.glob("*_mywork_doc.json")):
        data = _load_json(path)
        rows.append(
            [
                path.name,
                data.get("created_at", ""),
                data.get("document_title", ""),
                data.get("document_url", ""),
                data.get("document_token", ""),
                data.get("provider_name", ""),
                data.get("returncode", ""),
            ]
        )
    return rows


def _load_json(path: Path) -> dict[str, Any]:
    """安全读取 JSON；解析失败/非对象返回空 dict，避免单个坏文件中断导出。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def post_export_xlsx(request: Request) -> Response:
    """POST /api/exports/xlsx：按 body 条件即时生成 xlsx 字节流并回传，不落盘、不落库。"""
    body = request.json_body()
    start = _clean_date(body.get("start"))
    end = _clean_date(body.get("end"))

    # 允许前端通过 body.sheets 选择导出哪些表；缺省导出全部。
    requested = body.get("sheets")
    selected: set[str] | None = None
    if isinstance(requested, list) and requested:
        selected = {str(s).strip().lower() for s in requested if str(s).strip()}

    # (sheet 名, 构造器)；构造器延迟执行，未选中的表不必查询
    builders: list[tuple[str, Any]] = [
        ("Summary", lambda: _summary_rows(start, end)),
        ("DailyMetrics", lambda: _daily_metric_rows(start, end)),
        (
            "Records",
            lambda: _table_rows(
                "records", _RECORD_COLUMNS,
                date_col="substr(anchor_time, 1, 10)", start=start, end=end,
                order_by="anchor_time DESC",
            ),
        ),
        (
            "Demands",
            lambda: _table_rows(
                "demands", _DEMAND_COLUMNS,
                date_col="record_date", start=start, end=end,
                order_by="record_date DESC",
            ),
        ),
        (
            "DevTasks",
            lambda: _table_rows(
                "dev_tasks", _DEV_TASK_COLUMNS,
                date_col="substr(created_at, 1, 10)", start=start, end=end,
                order_by="created_at DESC",
            ),
        ),
        (
            "Repos",
            lambda: _table_rows(
                "repo_inventory", _REPO_COLUMNS,
                date_col=None, start=None, end=None,
                order_by="label",
            ),
        ),
        ("WeeklyReports", lambda: _weekly_report_rows()),
    ]

    sheets = [
        (name, build())
        for name, build in builders
        if selected is None or name.lower() in selected
    ]

    raw = build_xlsx(sheets)
    return Response.binary(
        raw=raw,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename=msg-listener-report.xlsx"
        },
    )
