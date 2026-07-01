"""周报端点（sub-agent D 实现）。

数据来源：扫描 out/mywork/*_mywork_doc.json，组装周报链接只读列表。

实现要求：
  - weekly-reports：返回每个 mywork doc 的日期段、标题、云文档链接、生成结果等
  - 支持模块文件 weekly_reports.py
"""
from __future__ import annotations

from ..router import Request, Response
from ..weekly_reports import collect_weekly_reports


def list_weekly_reports(request: Request) -> Response:
    """GET /api/weekly-reports：mywork 周报链接只读列表。

    扫描/解析逻辑委托给 weekly_reports 模块。
    目录不存在或无文件时返回 {"items": [], "total": 0}，不报错。
    """
    items = collect_weekly_reports()
    payload = {"items": items, "total": len(items)}
    return Response.json(payload)
