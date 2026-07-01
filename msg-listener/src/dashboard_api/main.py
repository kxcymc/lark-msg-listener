"""API 进程入口：python -m src.dashboard_api.main。

装配路由表 -> 启动 HTTP 服务。路由表是各 sub-agent 共享的稳定契约：
端点与 handler 的映射在此集中登记，handler 具体实现分散在 handlers/ 各文件中。
"""
from __future__ import annotations

import logging
import os

from .config import DashboardConfig
from .router import Router
from .server import serve
from .handlers import business, monitor, overview, weekly
from ..common.logging_setup import configure_logging

logger = logging.getLogger("dashboard-api")


def build_router() -> Router:
    """登记全部端点契约。新增端点统一在此追加。"""
    r = Router()

    # 基础
    r.add("GET", "/api/health", monitor.get_health)
    r.add("GET", "/api/config", monitor.get_config)

    # 概览
    r.add("GET", "/api/overview", overview.get_overview)
    r.add("GET", "/api/timeseries", overview.get_timeseries)

    # 业务数据
    r.add("GET", "/api/records", business.list_records)
    r.add("GET", "/api/records/:id", business.get_record)
    r.add("GET", "/api/records/:id/replay", business.get_record_replay)
    r.add("GET", "/api/records/:id/media/*", business.get_record_media)
    r.add("GET", "/api/analysis-jobs", business.list_analysis_jobs)
    r.add("GET", "/api/demands", business.list_demands)
    r.add("GET", "/api/demands/:id", business.get_demand)
    r.add("GET", "/api/dev-tasks", business.list_dev_tasks)
    r.add("GET", "/api/dev-tasks/:id", business.get_dev_task)
    r.add("GET", "/api/repos", business.list_repos)

    # 监控
    r.add("GET", "/api/monitor/processes", monitor.get_monitor_processes)
    r.add("GET", "/api/monitor/queues", monitor.get_monitor_queues)
    r.add("GET", "/api/monitor/system", monitor.get_monitor_system)
    r.add("GET", "/api/logs", monitor.get_logs)

    # 周报
    r.add("GET", "/api/weekly-reports", weekly.list_weekly_reports)

    # 导出
    r.add("POST", "/api/exports/xlsx", overview.post_export_xlsx)

    return r


def main() -> None:
    cfg = DashboardConfig.load()
    configure_logging(
        service_name="dashboard-api",
        level=os.environ.get("MSG_LISTENER_LOG_LEVEL", "INFO"),
    )
    if not cfg.enabled:
        logger.error(
            "dashboard_api.enabled = false，未启动；请在 config.toml 的 [dashboard_api] 中开启。"
        )
        raise SystemExit(1)
    serve(build_router(), host=cfg.host, port=cfg.port)


if __name__ == "__main__":
    main()
