"""`/config` implementation backed by the generic card infrastructure."""
from __future__ import annotations

from copy import deepcopy
import json
import logging
import time
from pathlib import Path
from typing import Any

from ..analysis_cli_registry import build_analysis_cli_card_support
from ..dev_task_executor_support import build_dev_task_executor_card_support
from ...mywork.conclude_cli_registry import build_conclude_cli_card_support
from ..cards.base import (
    CARD_OP_CANCEL,
    CARD_OP_SUCCESS,
    CARD_OP_VALIDATION_ERROR,
    CARD_STATUS_CANCELLED,
    CARD_STATUS_SUBMITTED,
    CARD_STATUS_VALIDATION_ERROR,
    CardActionContext,
    CardActionResult,
    CardInstance,
    CardOpenContext,
)
from .config_schema import (
    ConfigFieldSchema,
    build_config_schemas,
    current_raw_values,
    static_option_labels,
)
from .config_store import (
    SENSITIVE_KEYS,
    ConfigError,
    apply_changes,
    load_config,
)

logger = logging.getLogger(__name__)


class ConfigCardScene:
    scene_key = "config"

    def __init__(
        self,
        *,
        config_path: Path,
        project_root: Path,
    ) -> None:
        self.config_path = config_path
        self.project_root = project_root

    async def build_open_card(
        self,
        ctx: CardOpenContext,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        del ctx
        cfg = load_config(self.config_path)
        schema_context = await _build_schema_context(cfg)
        values = current_raw_values(cfg)
        card = _build_form_card(
            values=values,
            schemas=schema_context["schemas"],
            errors=[],
            title="配置修改",
            template="blue",
            note="修改后点击提交；任一字段校验失败时不会写回 config.toml。",
            current_option_values=schema_context["current_option_values"],
            option_labels=schema_context["option_labels"],
        )
        return card, {"values": values}

    async def handle_action(self, ctx: CardActionContext) -> CardActionResult:
        logger.info(
            "处理配置卡片 action=%s instance=%s form_data=%s",
            ctx.action_name,
            ctx.instance.instance_id,
            ctx.form_data,
        )
        if ctx.action_name == "config_cancel":
            return CardActionResult(
                op=CARD_OP_CANCEL,
                next_status=CARD_STATUS_CANCELLED,
                card=_build_terminal_card(
                    title="配置修改已取消",
                    template="grey",
                    content="本次没有写回任何配置。",
                ),
            )
        if ctx.action_name != "config_submit":
            cfg = load_config(self.config_path)
            schema_context = await _build_schema_context(cfg)
            return CardActionResult(
                op=CARD_OP_VALIDATION_ERROR,
                next_status=CARD_STATUS_VALIDATION_ERROR,
                card=_build_form_card(
                    values={**_load_context_values(ctx.instance), **ctx.form_data},
                    schemas=schema_context["schemas"],
                    errors=[f"未知操作：{ctx.action_name}"],
                    title="配置校验失败",
                    template="red",
                    note="请修正错误后再次提交；当前没有写回 config.toml。",
                    current_option_values=schema_context["current_option_values"],
                    option_labels=schema_context["option_labels"],
                ),
            )

        base_values = _load_context_values(ctx.instance)
        merged = {**base_values, **ctx.form_data}
        logger.info(
            "配置提交合并字段 instance=%s changed_keys=%s merged=%s",
            ctx.instance.instance_id,
            sorted(ctx.form_data.keys()),
            _mask_values(merged),
        )
        current_cfg = load_config(self.config_path)
        schema_context = await _build_schema_context(
            _merge_cfg_with_flat_values(current_cfg, merged)
        )
        errors = _validate_form_values(
            merged,
            schema_context["schemas"],
            schema_context["unavailable_option_values"],
        )
        if errors:
            logger.warning(
                "配置提交校验失败 instance=%s errors=%s",
                ctx.instance.instance_id,
                errors,
            )
            return CardActionResult(
                op=CARD_OP_VALIDATION_ERROR,
                next_status=CARD_STATUS_VALIDATION_ERROR,
                card=_build_form_card(
                    values=merged,
                    schemas=schema_context["schemas"],
                    errors=errors,
                    title="配置校验失败",
                    template="red",
                    note="请修正错误后再次提交；当前没有写回 config.toml。",
                    current_option_values=schema_context["current_option_values"],
                    option_labels=schema_context["option_labels"],
                ),
            )

        try:
            results = apply_changes(
                config_path=self.config_path,
                changes=merged,
            )
        except (ConfigError, OSError) as exc:
            logger.exception(
                "配置写回失败 instance=%s merged=%s",
                ctx.instance.instance_id,
                _mask_values(merged),
            )
            return CardActionResult(
                op=CARD_OP_VALIDATION_ERROR,
                next_status=CARD_STATUS_VALIDATION_ERROR,
                card=_build_form_card(
                    values=merged,
                    schemas=schema_context["schemas"],
                    errors=[str(exc)],
                    title="配置写回失败",
                    template="red",
                    note="请修正错误后再次提交；当前没有写回 config.toml。",
                    current_option_values=schema_context["current_option_values"],
                    option_labels=schema_context["option_labels"],
                ),
            )

        logger.info(
            "配置写回成功 instance=%s results=%s",
            ctx.instance.instance_id,
            [
                {
                    "key": item.key,
                    "old": _mask_value(item.key, item.old_value),
                    "new": _mask_value(item.key, item.new_value),
                    "inserted": item.inserted,
                }
                for item in results
            ],
        )
        _trigger_restart(self.project_root)
        return CardActionResult(
            op=CARD_OP_SUCCESS,
            next_status=CARD_STATUS_SUBMITTED,
            card=_build_success_card(),
        )

    async def build_expired_card(self, instance: CardInstance) -> dict[str, Any]:
        del instance
        return _build_terminal_card(
            title="配置卡片已失效",
            template="grey",
            content="已打开新的 `/config` 卡片，请在最新卡片中继续操作。",
        )


def _load_context_values(instance: CardInstance) -> dict[str, str]:
    try:
        data = json.loads(instance.context_json)
    except json.JSONDecodeError:
        return {}
    values = data.get("values") if isinstance(data, dict) else None
    if not isinstance(values, dict):
        return {}
    return {str(k): "" if v is None else str(v) for k, v in values.items()}


async def _build_schema_context(cfg: dict[str, Any]) -> dict[str, Any]:
    cli_support = await build_analysis_cli_card_support(cfg)
    dev_support = await build_dev_task_executor_card_support(cfg)
    mywork_support = await build_conclude_cli_card_support(cfg)
    dynamic_select_options = {
        **cli_support.dynamic_select_options,
        **dev_support.dynamic_select_options,
        **mywork_support.dynamic_select_options,
    }
    current_option_values = {
        **cli_support.current_option_values,
        **dev_support.current_option_values,
        **mywork_support.current_option_values,
    }
    option_labels = {
        **static_option_labels(),
        **deepcopy(cli_support.option_labels),
        **deepcopy(dev_support.option_labels),
        **deepcopy(mywork_support.option_labels),
    }
    unavailable_option_values = {
        **cli_support.unavailable_option_values,
        **dev_support.unavailable_option_values,
        **mywork_support.unavailable_option_values,
    }
    return {
        "schemas": build_config_schemas(
            dynamic_select_options=dynamic_select_options,
        ),
        "current_option_values": current_option_values,
        "option_labels": option_labels,
        "unavailable_option_values": unavailable_option_values,
    }


def _merge_cfg_with_flat_values(
    cfg: dict[str, Any],
    values: dict[str, str],
) -> dict[str, Any]:
    merged = deepcopy(cfg)
    for dotted_key, raw_value in values.items():
        cursor: dict[str, Any] = merged
        parts = dotted_key.split(".")
        for part in parts[:-1]:
            child = cursor.get(part)
            if not isinstance(child, dict):
                child = {}
                cursor[part] = child
            cursor = child
        cursor[parts[-1]] = raw_value
    return merged


def _validate_form_values(
    values: dict[str, str],
    schemas: list[ConfigFieldSchema],
    unavailable_option_values: dict[str, tuple[str, ...]] | None = None,
) -> list[str]:
    errors: list[str] = []
    unavailable_option_values = unavailable_option_values or {}
    for schema in schemas:
        value = values.get(schema.key, "").strip()
        if not value and not schema.allow_empty:
            errors.append(f"`{schema.key}` 不能为空")
            continue
        if schema.widget == "text" and len(value) > schema.max_length:
            errors.append(f"`{schema.key}` 不能超过 {schema.max_length} 字")
        if schema.widget == "number" and value:
            try:
                number = float(value)
            except ValueError:
                errors.append(f"`{schema.key}` 必须是数字")
                continue
            if schema.min_value is not None and number < schema.min_value:
                errors.append(f"`{schema.key}` 不能小于 {schema.min_value:g}")
            if schema.max_value is not None and number > schema.max_value:
                errors.append(f"`{schema.key}` 不能大于 {schema.max_value:g}")
        if schema.widget == "select" and value and value not in schema.options:
            errors.append(f"`{schema.key}` 不是合法候选值：{value}")
        if (
            schema.widget == "select"
            and value
            and value in unavailable_option_values.get(schema.key, ())
        ):
            errors.append(f"`{schema.key}` 当前暂不可用")
    return errors


def _build_form_card(
    *,
    values: dict[str, str],
    schemas: list[ConfigFieldSchema],
    errors: list[str],
    title: str,
    template: str,
    note: str,
    current_option_values: dict[str, str] | None = None,
    option_labels: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    elements: list[dict[str, Any]] = []
    if errors:
        elements.append(_div("**错误**\n\n" + "\n".join(f"- {e}" for e in errors)))
    elements.append(_note(note))
    form_elements: list[dict[str, Any]] = []
    for schema in schemas:
        form_elements.append(
            _field_element(
                schema,
                values.get(schema.key, ""),
                current_option_values=current_option_values,
                option_labels=option_labels,
            )
        )
    form_elements.append(_submit_button())
    elements.append({"tag": "form", "name": "config_form", "elements": form_elements})
    elements.append({"tag": "action", "actions": [_button("取消", "default", "config_cancel")]})
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": template,
        },
        "elements": elements,
    }


def _field_element(
    schema: ConfigFieldSchema,
    value: str,
    *,
    current_option_values: dict[str, str] | None = None,
    option_labels: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "tag": "column_set",
        "columns": [
            {
                "tag": "column",
                "elements": [_div(f"{schema.description}：")],
            },
            {
                "tag": "column",
                "elements": [
                    _field_control(
                        schema,
                        value,
                        current_option_values=current_option_values,
                        option_labels=option_labels,
                    )
                ],
            },
        ],
    }


def _field_control(
    schema: ConfigFieldSchema,
    value: str,
    *,
    current_option_values: dict[str, str] | None = None,
    option_labels: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    if schema.widget == "select":
        options = []
        current_option_values = current_option_values or {}
        option_labels = option_labels or {}
        field_option_labels = option_labels.get(schema.key, {})
        current_option_value = current_option_values.get(schema.key, "")
        option_values = list(schema.options)
        if current_option_value and current_option_value in option_values:
            option_values.remove(current_option_value)
            option_values.insert(0, current_option_value)
        for item in option_values:
            label = field_option_labels.get(item, item)
            if item == current_option_value:
                label = f"{item}（当前）"
            if item == current_option_value and item in field_option_labels:
                label = f"{field_option_labels[item]}（当前）"
            options.append(
                {"text": {"tag": "plain_text", "content": label}, "value": item}
            )
        select: dict[str, Any] = {
            "tag": "select_static",
            "placeholder": {"tag": "plain_text", "content": "请选择"},
            "name": schema.key,
            "required": not schema.allow_empty,
            "options": options,
        }
        initial_value = value
        if initial_value and initial_value not in option_values:
            initial_value = ""
        if not initial_value:
            if current_option_value and current_option_value in option_values:
                initial_value = current_option_value
            elif option_values:
                initial_value = option_values[0]
        if initial_value:
            select["initial_option"] = initial_value
        return select

    placeholder = schema.description
    if schema.widget == "number":
        placeholder = f"{schema.description}（{schema.min_value:g} - {schema.max_value:g}）"
    input_box: dict[str, Any] = {
        "tag": "input",
        "name": schema.key,
        "required": not schema.allow_empty,
        "placeholder": {"tag": "plain_text", "content": placeholder},
        "max_length": schema.max_length,
    }
    if value:
        input_box["default_value"] = value
    if schema.widget == "number":
        input_box["input_type"] = "text"
    return input_box


def _submit_button() -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": "提交修改"},
        "type": "primary",
        "name": "config_submit",
        "action_type": "form_submit",
        "value": {"action": "config_submit"},
    }


def _build_success_card() -> dict[str, Any]:
    return _build_terminal_card(
        title="已成功写回配置",
        template="green",
        content="正在为您重启服务，请稍候...",
    )


def _trigger_restart(project_root: Path) -> None:
    """通过写入 `cache/restart.request` 文件请求 supervisor (`src/main.py`) 重启服务。

    跨平台：仅依赖 pathlib 文件操作；不发送信号、不起子进程。
    supervisor 主循环每秒轮询该文件，发现存在即消费（删除）并执行重启流程。
    """
    request_path = project_root / "cache" / "restart.request"
    try:
        request_path.parent.mkdir(parents=True, exist_ok=True)
        request_path.write_text(str(int(time.time())), encoding="utf-8")
    except OSError as exc:
        logger.error("写入 restart.request 失败：%s", exc)
        return
    logger.info("已请求服务重启 path=%s", request_path)


def _build_terminal_card(*, title: str, template: str, content: str) -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": template,
        },
        "elements": [_div(content)],
    }


def _div(content: str) -> dict[str, Any]:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _note(content: str) -> dict[str, Any]:
    return {"tag": "note", "elements": [{"tag": "lark_md", "content": content}]}


def _mask_value(key: str, value: Any) -> Any:
    if key not in SENSITIVE_KEYS:
        return value
    return "***" if str(value or "") else ""


def _mask_values(values: dict[str, Any]) -> dict[str, Any]:
    return {key: _mask_value(key, value) for key, value in values.items()}


def _button(text: str, btn_type: str, action_name: str) -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": btn_type,
        "name": action_name,
        "value": {"action": action_name},
    }
