"""Config card field metadata and validation helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..analysis_cli_registry import list_analysis_cli_options
from ..dev_task_executor_support import list_executor_options
from ...mywork.conclude_cli_registry import list_conclude_cli_options
from .config_store import WHITELIST, WhitelistField, get_dotted


@dataclass(frozen=True)
class ConfigFieldSchema:
    key: str
    label: str
    widget: str  # number | text | select
    description: str
    min_value: float | None = None
    max_value: float | None = None
    options: tuple[str, ...] = ()
    allow_empty: bool = False
    max_length: int = 1000


_NUMERIC_RANGES: dict[str, tuple[float, float]] = {
    "context_before": (0, 1000),
    "context_after": (0, 1000),
    "context_after_max_wait_seconds": (0, 86400),
    "poll_interval_seconds": (1, 86400),
    "phase2.analysis_interval_seconds": (1, 86400),
    "phase2.analysis_batch_size": (1, 500),
    "phase2.analysis_max_concurrency": (1, 32),
    "phase2.analysis_max_attempts": (1, 20),
    "phase3.dev_task.max_concurrency": (1, 32),
    "mywork.lookback_days": (1, 31),
    "mywork.context_window": (0, 1000),
    "mywork.chat_history_buffer_hours": (0, 24 * 31),
    "mywork.page_size": (1, 50),
    "mywork.max_pages_per_chat": (1, 1000),
    "mywork.conclude_cli_providers.kxcymc_cli.timeout_seconds": (1, 86400),
}

_STATIC_OPTIONS: dict[str, tuple[str, ...]] = {
    "log_level": ("DEBUG", "INFO", "WARNING", "ERROR"),
    "poll_p2p_enabled": ("true", "false"),
    "phase2.analysis_cli": list_analysis_cli_options(),
    "phase2.process_current_date_only": ("true", "false"),
    "phase2.card.meego_prompt_delivery": ("bubble", "trae_side_chat"),
    "phase3.dev_task.executor": list_executor_options(),
    "phase3.dev_task.claude_code_cli.output_format": ("json", "text", "stream-json"),
    "mywork.enabled": ("true", "false"),
    "mywork.conclude_cli": list_conclude_cli_options(),
    "ccr.enabled": ("true", "false"),
    "ccr.auto_start": ("true", "false"),
}
# 静态选项的中文显示标签（value -> 标签）。
_STATIC_OPTION_LABELS: dict[str, dict[str, str]] = {
    "phase2.card.meego_prompt_delivery": {
        "bubble": "智能体私聊",
        "trae_side_chat": "打开 Trae 侧聊",
    },
}
_DYNAMIC_SELECT_KEYS: set[str] = {
    "phase2.analysis_cli_model_name",
    "mywork.conclude_cli_model_name",
}

# 文本类字段的自定义最大长度。
# 注意：飞书卡片 input 组件的 max_length 平台硬上限为 1000，超过会导致整张卡片渲染失败
# （ErrCode 11310: max_length exceed the default maximum 1000），故卡片可编辑字段不得超过 1000。
_TEXT_MAX_LENGTHS: dict[str, int] = {
    "phase2.card.meego_prompt_content": 1000,
    "mywork.conclude_cli_providers.kxcymc_cli.prompt_template": 1000,
}


def static_option_labels() -> dict[str, dict[str, str]]:
    return {key: dict(labels) for key, labels in _STATIC_OPTION_LABELS.items()}


def build_config_schemas(
    *,
    dynamic_select_options: dict[str, tuple[str, ...]] | None = None,
) -> list[ConfigFieldSchema]:
    dynamic_select_options = dynamic_select_options or {}
    schemas: list[ConfigFieldSchema] = []
    for field in WHITELIST:
        schema = _schema_for_field(field, dynamic_select_options)
        if schema is not None:
            schemas.append(schema)
    return schemas


def current_raw_values(cfg: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for field in WHITELIST:
        value = get_dotted(cfg, field.key, "")
        if isinstance(value, bool):
            values[field.key] = "true" if value else "false"
        else:
            values[field.key] = "" if value is None else str(value)
    return values


def _schema_for_field(
    field: WhitelistField,
    dynamic_select_options: dict[str, tuple[str, ...]],
) -> ConfigFieldSchema | None:
    if field.key in _NUMERIC_RANGES:
        min_value, max_value = _NUMERIC_RANGES[field.key]
        return ConfigFieldSchema(
            key=field.key,
            label=field.key,
            widget="number",
            description=field.desc,
            min_value=min_value,
            max_value=max_value,
        )
    if field.key in dynamic_select_options:
        return ConfigFieldSchema(
            key=field.key,
            label=field.key,
            widget="select",
            description=field.desc,
            options=dynamic_select_options[field.key],
        )
    if field.key in _DYNAMIC_SELECT_KEYS:
        return None
    if field.key in _STATIC_OPTIONS:
        return ConfigFieldSchema(
            key=field.key,
            label=field.key,
            widget="select",
            description=field.desc,
            options=_STATIC_OPTIONS[field.key],
        )
    return ConfigFieldSchema(
        key=field.key,
        label=field.key,
        widget="text",
        description=field.desc,
        allow_empty=True,
        max_length=_TEXT_MAX_LENGTHS.get(field.key, 1000),
    )
