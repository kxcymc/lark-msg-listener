"""监控 / 日志 / 健康 / 配置端点（sub-agent C 实现）。

数据来源：
  - monitor/processes -> 对 main、bot、collector 进程表即时探活（参考 src/main.py 的跨平台枚举/探活）
  - monitor/queues    -> 对 analysis_jobs / dev_tasks 的状态即时计数（repositories）
  - monitor/system    -> CPU、内存、磁盘、out/logs 目录大小即时采样（优先 stdlib：os/shutil；禁止新增依赖）
  - logs              -> 读取 logs/<日期>/ 各服务日志尾部（query: service, lines）
  - health            -> cache/bot.ready 与各数据源路径可读性检查
  - config            -> config.toml 摘要

实现要求:
  - 全部为只读、即时采样；跨平台（Windows/macOS/Linux）行为一致
  - 采集逻辑下沉到同包的 system_monitor.py（探活/采样）与 logs.py（日志尾部），
    本文件只做编排与参数解析。
"""
from __future__ import annotations

import os
from typing import Any

from .. import repositories as repo
from ..config import CACHE_DIR, LOGS_DIR, DashboardConfig, index_db_path, load_raw_config
from ..config import OUT_DIR
from ..logs import KNOWN_SERVICES, log_path, tail_lines, today_dir_name
from ..router import Request, Response
from ..system_monitor import detect_services, sample_system

# bot 就绪标志文件，与 src/main.py 的 BOT_READY_STAMP_PATH 同一路径。
_BOT_READY_PATH = CACHE_DIR / "bot.ready"


def get_monitor_processes(request: Request) -> Response:
    """GET /api/monitor/processes：即时探活 main / bot / collector。"""
    return Response.json({"services": detect_services()})


def _status_counts(table: str) -> dict[str, int]:
    """对某表按 status 分组计数；表不存在返回空 dict。"""
    if not repo.table_exists(table):
        return {}
    rows = repo.query_all(
        f"SELECT status, COUNT(*) AS n FROM {table} GROUP BY status"
    )
    # status 理论上非空，做一次兜底转字符串，避免 None 作键破坏 JSON 序列化。
    return {str(r.get("status")): int(r.get("n") or 0) for r in rows}


def get_monitor_queues(request: Request) -> Response:
    """GET /api/monitor/queues：分析任务 / 开发任务按状态的即时队列计数。"""
    return Response.json(
        {
            "analysis_jobs": _status_counts("analysis_jobs"),
            "dev_tasks": _status_counts("dev_tasks"),
        }
    )


def get_monitor_system(request: Request) -> Response:
    """GET /api/monitor/system：CPU / 内存 / 磁盘 / out / logs 即时采样。"""
    return Response.json(sample_system())


def get_logs(request: Request) -> Response:
    """GET /api/logs：读取指定服务当日日志尾部 N 行后返回。

    query：
      - service：缺省遍历 KNOWN_SERVICES 中第一个存在当日日志的服务；
      - lines：缺省取 DashboardConfig.tail_log_lines。
    """
    cfg = DashboardConfig.load()
    date = today_dir_name()
    requested = (request.query.get("service") or "").strip()
    lines = request.query_int("lines", cfg.tail_log_lines)
    if lines < 0:
        lines = cfg.tail_log_lines

    service = _resolve_service(requested, date)
    raw_lines = tail_lines(log_path(service, date), lines)
    return Response.json(
        {
            "service": service,
            "date": date,
            "lines": raw_lines,
            "known_services": list(KNOWN_SERVICES),
        }
    )


def _resolve_service(requested: str, date: str) -> str:
    """确定要读取的服务名。

    - 显式传入 service 时按原值读取（即使当日尚无日志，让 lines 返回空数组）；
    - 缺省时选 KNOWN_SERVICES 中首个当日已有日志文件的服务，便于前端默认展示；
    - 都没有日志则回退到首个已知服务名。
    """
    if requested:
        return requested
    for name in KNOWN_SERVICES:
        if log_path(name, date).is_file():
            return name
    return KNOWN_SERVICES[0]


def _path_readable(path) -> bool:
    """路径存在且当前进程可读；任何 OSError 视为不可读。"""
    try:
        return path.exists() and os.access(path, os.R_OK)
    except OSError:
        return False


def get_health(request: Request) -> Response:
    """GET /api/health：汇总就绪标志与各数据源可读性。

    任一检查失败仅体现在 checks 中并把 status 置为 degraded，绝不抛 500。
    """
    checks = {
        "bot_ready": _BOT_READY_PATH.exists(),
        "index_db_readable": _path_readable(index_db_path()),
        "out_dir_readable": _path_readable(OUT_DIR),
        "logs_dir_readable": _path_readable(LOGS_DIR),
    }
    status = "ok" if all(checks.values()) else "degraded"
    return Response.json({"status": status, "checks": checks})


def get_config(request: Request) -> Response:
    """GET /api/config：返回 config.toml 摘要。"""
    raw: dict[str, Any] = load_raw_config()
    return Response.json({"config": raw})
