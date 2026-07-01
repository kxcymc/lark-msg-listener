"""分析 CLI 的通用元数据。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AnalysisCliCardSupport:
    """配置卡片所需的 CLI 扩展信息。"""

    available: bool = True
    dynamic_select_options: dict[str, tuple[str, ...]] = field(default_factory=dict)
    current_option_values: dict[str, str] = field(default_factory=dict)
    option_labels: dict[str, dict[str, str]] = field(default_factory=dict)
    unavailable_option_values: dict[str, tuple[str, ...]] = field(default_factory=dict)
