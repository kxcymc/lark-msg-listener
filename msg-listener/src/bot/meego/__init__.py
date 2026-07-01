"""Meego 需求收集模块。"""
from __future__ import annotations

from .collectors import (
    CollectResult,
    collect_from_lark_group,
    collect_from_meego_url,
)

__all__ = [
    "CollectResult",
    "collect_from_lark_group",
    "collect_from_meego_url",
]
