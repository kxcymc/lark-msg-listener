"""analyzers 子包：负责把 record 喂给底层模型并产出归一化 AnalyzerResult。"""
from __future__ import annotations

from .types import (
    AnalyzerError,
    AnalyzerInput,
    AnalyzerRateLimited,
    AnalyzerResult,
    CATEGORY_CHAT,
    CATEGORY_NOISE,
    CATEGORY_QUESTION,
    CATEGORY_REQUIREMENT,
    CATEGORY_UNKNOWN,
    VALID_CATEGORIES,
    normalize_record_to_input,
)

__all__ = [
    "AnalyzerError",
    "AnalyzerInput",
    "AnalyzerRateLimited",
    "AnalyzerResult",
    "CATEGORY_CHAT",
    "CATEGORY_NOISE",
    "CATEGORY_QUESTION",
    "CATEGORY_REQUIREMENT",
    "CATEGORY_UNKNOWN",
    "VALID_CATEGORIES",
    "normalize_record_to_input",
]
