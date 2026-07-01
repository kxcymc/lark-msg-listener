"""overview / timeseries 共用的只读聚合逻辑。

为什么单独成模块：概览指标与按日趋势都对同一批表（records / demands /
dev_tasks / analysis_jobs）做 COUNT/GROUP BY，导出 sheet 又要复用这些
结果。集中在此可避免 overview.py 与 exporter 调用处各写一份 SQL。

所有查询经 repositories 的只读短连接；表不存在时 repositories 返回空，
配合本模块的 table_exists 守卫，缺表项一律计 0，不抛错。

日期维度约定（与各表实际写入一致）：
  - records   : anchor_time 是 ISO8601 本地时间字符串，取前 10 位即 YYYY-MM-DD
  - demands / analysis_jobs : 各有 record_date 列，已是 YYYY-MM-DD
  - dev_tasks : created_at 是 ISO8601 本地时间字符串，取前 10 位
"""
from __future__ import annotations

from typing import Any

from . import repositories as repo


def _date_clause(column: str, start: str | None, end: str | None) -> tuple[str, list[str]]:
    """构造对「日期字符串列」的范围过滤片段。

    column 传入的应是已规整为 YYYY-MM-DD 的表达式（如 substr(anchor_time,1,10)
    或 record_date）。start/end 为闭区间边界，缺省则不加该侧约束。
    """
    clauses: list[str] = []
    params: list[str] = []
    if start:
        clauses.append(f"{column} >= ?")
        params.append(start)
    if end:
        clauses.append(f"{column} <= ?")
        params.append(end)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _count(table: str, date_col: str, start: str | None, end: str | None,
           extra: str = "") -> int:
    """对单表做范围内计数；表不存在返回 0。extra 追加额外条件（已含 AND/无 WHERE）。"""
    if not repo.table_exists(table):
        return 0
    where, params = _date_clause(date_col, start, end)
    if extra:
        where = (where + " AND " + extra) if where else " WHERE " + extra
    sql = f"SELECT COUNT(*) FROM {table}{where}"
    value = repo.query_scalar(sql, tuple(params))
    return int(value or 0)


def _status_counts(table: str, date_col: str, start: str | None,
                   end: str | None) -> dict[str, int]:
    """按 status 分组计数；表不存在返回空 dict。"""
    if not repo.table_exists(table):
        return {}
    where, params = _date_clause(date_col, start, end)
    sql = f"SELECT status, COUNT(*) AS n FROM {table}{where} GROUP BY status"
    rows = repo.query_all(sql, tuple(params))
    return {str(r.get("status") or ""): int(r.get("n") or 0) for r in rows}


def collect_overview(start: str | None, end: str | None) -> dict[str, Any]:
    """汇总核心工作量指标与成功率/失败率。"""
    # records：用 anchor_time 前 10 位作为日期维度
    records_total = _count("records", "substr(anchor_time, 1, 10)", start, end)

    # demands：有独立 record_date；MR 数取 mr_url 非空者
    demands_total = _count("demands", "record_date", start, end)
    demands_mr_total = _count(
        "demands", "record_date", start, end, extra="mr_url IS NOT NULL AND mr_url != ''"
    )
    demand_status = _status_counts("demands", "record_date", start, end)

    # dev_tasks：用 created_at 前 10 位作为日期维度
    dev_tasks_total = _count("dev_tasks", "substr(created_at, 1, 10)", start, end)
    dev_tasks_mr_total = _count(
        "dev_tasks", "substr(created_at, 1, 10)", start, end,
        extra="mr_url IS NOT NULL AND mr_url != ''",
    )
    dev_task_status = _status_counts("dev_tasks", "substr(created_at, 1, 10)", start, end)

    # analysis_jobs：有 record_date
    analysis_total = _count("analysis_jobs", "record_date", start, end)
    analysis_status = _status_counts("analysis_jobs", "record_date", start, end)

    # MR 总数：需求与开发任务两侧带 MR 的数量合计（报告口径：mr_url 取自两表）
    mr_total = demands_mr_total + dev_tasks_mr_total

    # 成功率/失败率：以 dev_tasks 终态为准（succeeded/failed），分母为 0 时返回 0 避免除零
    succeeded = dev_task_status.get("succeeded", 0)
    failed = dev_task_status.get("failed", 0)
    finished = succeeded + failed
    success_rate = round(succeeded / finished, 4) if finished else 0.0
    failure_rate = round(failed / finished, 4) if finished else 0.0

    return {
        "range": {"start": start or "", "end": end or ""},
        "records_total": records_total,
        "demands_total": demands_total,
        "dev_tasks_total": dev_tasks_total,
        "analysis_jobs_total": analysis_total,
        "mr_total": mr_total,
        "mr_breakdown": {
            "demands": demands_mr_total,
            "dev_tasks": dev_tasks_mr_total,
        },
        "analysis_status_counts": analysis_status,
        "demand_status_counts": demand_status,
        "dev_task_status_counts": dev_task_status,
        "dev_task_success": {
            "succeeded": succeeded,
            "failed": failed,
            "finished": finished,
            "success_rate": success_rate,
            "failure_rate": failure_rate,
        },
    }


def _daily_map(table: str, date_expr: str, start: str | None, end: str | None,
               extra: str = "") -> dict[str, int]:
    """按日期表达式分组计数，返回 {date: count}；表不存在返回空。"""
    if not repo.table_exists(table):
        return {}
    where, params = _date_clause(date_expr, start, end)
    if extra:
        where = (where + " AND " + extra) if where else " WHERE " + extra
    sql = (
        f"SELECT {date_expr} AS d, COUNT(*) AS n FROM {table}{where} "
        f"GROUP BY d"
    )
    rows = repo.query_all(sql, tuple(params))
    result: dict[str, int] = {}
    for r in rows:
        day = str(r.get("d") or "").strip()
        if day:
            result[day] = int(r.get("n") or 0)
    return result


def collect_timeseries(start: str | None, end: str | None) -> list[dict[str, Any]]:
    """按日聚合 record / 需求 / 任务 / MR 数，按日期升序返回。

    MR 维度口径与 overview 一致：demands.mr_url 与 dev_tasks.mr_url 非空合计。
    """
    rec_daily = _daily_map("records", "substr(anchor_time, 1, 10)", start, end)
    dem_daily = _daily_map("demands", "record_date", start, end)
    task_daily = _daily_map("dev_tasks", "substr(created_at, 1, 10)", start, end)
    dem_mr_daily = _daily_map(
        "demands", "record_date", start, end, extra="mr_url IS NOT NULL AND mr_url != ''"
    )
    task_mr_daily = _daily_map(
        "dev_tasks", "substr(created_at, 1, 10)", start, end,
        extra="mr_url IS NOT NULL AND mr_url != ''",
    )

    # 合并所有出现过的日期，缺失维度补 0
    all_dates = set(rec_daily) | set(dem_daily) | set(task_daily) | set(dem_mr_daily) | set(task_mr_daily)
    series: list[dict[str, Any]] = []
    for day in sorted(all_dates):
        series.append(
            {
                "date": day,
                "records": rec_daily.get(day, 0),
                "demands": dem_daily.get(day, 0),
                "dev_tasks": task_daily.get(day, 0),
                "mrs": dem_mr_daily.get(day, 0) + task_mr_daily.get(day, 0),
            }
        )
    return series
