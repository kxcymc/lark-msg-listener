"""Meego requirement collection card scene."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from dataclasses import dataclass
from typing import Any

from ...analysis.card_builder import RepoOption
from ...git_inventory import GitInventoryService, RepoInfo
from ...meego import CollectResult, collect_from_lark_group, collect_from_meego_url
from ...notifier import BotNotifier, NotifierError
from ...trae_side_chat import copy_to_local_clipboard, open_trae_side_chat
from ..base import (
    CARD_OP_CANCEL,
    CARD_OP_IGNORE,
    CARD_OP_SUCCESS,
    CARD_OP_VALIDATION_ERROR,
    CARD_STATUS_ACTIVE,
    CARD_STATUS_CANCELLED,
    CARD_STATUS_CLOSED,
    CARD_STATUS_VALIDATION_ERROR,
    CardActionContext,
    CardActionResult,
    CardInstance,
    CardOpenContext,
)

logger = logging.getLogger(__name__)

MEEGO_MODE_CHAT = "chat"
MEEGO_MODE_URL = "url"

MEEGO_DELIVERY_BUBBLE = "bubble"
MEEGO_DELIVERY_TRAE_SIDE_CHAT = "trae_side_chat"
CLIPBOARD_FALLBACK_TEXT = "需求 prompt 已复制到剪贴板，如打开失败，可自行粘贴。"


@dataclass(frozen=True)
class MeegoCardContext:
    mode: str
    repo_options: list[RepoOption]
    selected_repo: str = ""
    source_value: str = ""
    errors: list[str] | None = None


class MeegoCardScene:
    scene_key = "meego"

    def __init__(
        self,
        *,
        inventory_service: GitInventoryService,
        bot_notifier: BotNotifier,
        requirement_host: str = "",
        prompt_delivery: str = MEEGO_DELIVERY_BUBBLE,
        prompt_content: str = "",
    ) -> None:
        self.inventory_service = inventory_service
        self.bot_notifier = bot_notifier
        self.requirement_host = requirement_host or ""
        self.prompt_delivery = prompt_delivery or MEEGO_DELIVERY_BUBBLE
        self.prompt_content = prompt_content or ""

    async def build_open_card(
        self,
        ctx: CardOpenContext,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        mode = str(ctx.extra.get("mode") or "")
        if mode not in {MEEGO_MODE_CHAT, MEEGO_MODE_URL}:
            raise ValueError("MeegoCardScene 缺少合法 mode")
        repos = await self.inventory_service.get_inventory()
        card_context = MeegoCardContext(
            mode=mode,
            repo_options=_to_options(repos),
        )
        return _build_card(
            card_context,
            require_repo=_requires_repo(self.prompt_delivery),
        ), _dump_context(card_context)

    async def handle_action(self, ctx: CardActionContext) -> CardActionResult:
        logger.info(
            "处理 Meego 卡片 action=%s instance=%s form_data=%s",
            ctx.action_name,
            ctx.instance.instance_id,
            ctx.form_data,
        )
        if ctx.action_name in {"meego_source_changed", "meego_repo_changed"}:
            return self._handle_changed(ctx)
        if ctx.action_name == "meego_confirm":
            return await self._handle_confirm(ctx)
        if ctx.action_name == "meego_cancel":
            return CardActionResult(
                op=CARD_OP_CANCEL,
                next_status=CARD_STATUS_CANCELLED,
                card=_terminal("Meego 收集已取消", "grey", "已取消本次 Meego 需求收集。"),
            )
        return CardActionResult(
            op=CARD_OP_IGNORE,
            next_status=CARD_STATUS_ACTIVE,
            error_text=f"未知操作：{ctx.action_name}",
        )

    async def build_expired_card(self, instance: CardInstance) -> dict[str, Any]:
        del instance
        return _terminal(
            "Meego 卡片已失效",
            "grey",
            "已打开新的 Meego 卡片，请在最新卡片中继续操作。",
        )

    def _handle_changed(self, ctx: CardActionContext) -> CardActionResult:
        base = _load_context(ctx.instance)
        refreshed = _merge_form_values(base, ctx)
        return CardActionResult(
            op=CARD_OP_SUCCESS,
            next_status=CARD_STATUS_ACTIVE,
            card=_build_card(
                refreshed,
                require_repo=_requires_repo(self.prompt_delivery),
            ),
            context_json=json.dumps(_dump_context(refreshed), ensure_ascii=False),
        )

    async def _handle_confirm(self, ctx: CardActionContext) -> CardActionResult:
        base = _load_context(ctx.instance)
        merged = _merge_form_values(base, ctx)
        require_repo = _requires_repo(self.prompt_delivery)
        errors = _validate(merged, require_repo=require_repo)
        if errors:
            failed = _replace_errors(merged, errors)
            return CardActionResult(
                op=CARD_OP_VALIDATION_ERROR,
                next_status=CARD_STATUS_VALIDATION_ERROR,
                card=_build_card(failed, require_repo=require_repo),
                context_json=json.dumps(_dump_context(failed), ensure_ascii=False),
            )

        try:
            if merged.mode == MEEGO_MODE_CHAT:
                collected = await collect_from_lark_group(
                    merged.source_value.strip(),
                    requirement_host=self.requirement_host,
                    prompt_content=self.prompt_content,
                )
            else:
                collected = await collect_from_meego_url(
                    merged.source_value.strip(),
                    requirement_host=self.requirement_host,
                    prompt_content=self.prompt_content,
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Meego 需求收集失败 mode=%s: %s", merged.mode, exc)
            failed = _replace_errors(merged, [f"需求收集失败：{exc}"])
            return CardActionResult(
                op=CARD_OP_VALIDATION_ERROR,
                next_status=CARD_STATUS_VALIDATION_ERROR,
                card=_build_card(failed, require_repo=require_repo),
                context_json=json.dumps(_dump_context(failed), ensure_ascii=False),
            )

        prompt = collected.prompt.strip()
        warnings = [json.dumps(item, ensure_ascii=False) for item in collected.errors]
        if not prompt:
            failed = _replace_errors(
                merged,
                ["未收集到需求 prompt，请检查输入或 lark-cli / meegle 登录状态。"],
            )
            return CardActionResult(
                op=CARD_OP_VALIDATION_ERROR,
                next_status=CARD_STATUS_VALIDATION_ERROR,
                card=_build_card(
                    failed,
                    warnings=warnings,
                    show_clipboard_fallback=_has_clipboard_fallback(self.prompt_delivery),
                    require_repo=require_repo,
                ),
                context_json=json.dumps(_dump_context(failed), ensure_ascii=False),
            )

        try:
            await self._deliver_prompt(ctx=merged, result=collected, prompt=prompt)
        except NotifierError as exc:
            logger.exception("Meego prompt 推送失败：%s", exc)
            failed = _replace_errors(merged, [f"prompt 推送失败：{exc}"])
            return CardActionResult(
                op=CARD_OP_VALIDATION_ERROR,
                next_status=CARD_STATUS_VALIDATION_ERROR,
                card=_build_card(
                    failed,
                    warnings=warnings,
                    require_repo=require_repo,
                ),
                context_json=json.dumps(_dump_context(failed), ensure_ascii=False),
            )

        return CardActionResult(
            op=CARD_OP_SUCCESS,
            next_status=CARD_STATUS_CLOSED,
            card=_build_done_card(
                ctx=merged,
                result=collected,
                warnings=warnings,
                delivery=self.prompt_delivery,
                require_repo=require_repo,
            ),
        )

    async def _deliver_prompt(
        self,
        *,
        ctx: MeegoCardContext,
        result: CollectResult,
        prompt: str,
    ) -> None:
        del result
        delivery = (self.prompt_delivery or MEEGO_DELIVERY_BUBBLE).strip()
        logger.debug(
            "meego 投递 delivery=%s prompt_len=%d prompt_repr=%r",
            delivery,
            len(prompt),
            prompt,
        )
        idempotency_key = f"meego:{delivery}:{ctx.mode}:{abs(hash(ctx.source_value))}"
        # 无论哪种投递方式，都把整理后的 prompt 作为气泡消息发给 owner，方便随时查看/复制。
        await self.bot_notifier.send_text(
            prompt,
            idempotency_key=idempotency_key,
        )
        if delivery == MEEGO_DELIVERY_TRAE_SIDE_CHAT:
            await open_trae_side_chat(prompt, repo_path=_resolve_selected_repo_path(ctx))
            return
        if delivery != MEEGO_DELIVERY_BUBBLE:
            logger.warning("未知 Meego prompt 推送方式 %s，回退 bubble", delivery)
        await copy_to_local_clipboard(prompt)


def _resolve_selected_repo_path(ctx: MeegoCardContext) -> str:
    selected = ctx.selected_repo.strip()
    for option in ctx.repo_options:
        if selected in {option.value, option.label, option.local_path}:
            return option.local_path
    return selected if Path(selected).expanduser().is_dir() else ""


def _to_options(repos: list[RepoInfo]) -> list[RepoOption]:
    return [
        RepoOption(
            label=repo.label,
            value=repo.repo_id,
            branches=repo.branches,
            local_path=repo.local_path,
        )
        for repo in repos
    ]


def _dump_context(ctx: MeegoCardContext) -> dict[str, Any]:
    return {
        "mode": ctx.mode,
        "selected_repo": ctx.selected_repo,
        "source_value": ctx.source_value,
        "errors": ctx.errors or [],
        "repo_options": [
            {
                "label": opt.label,
                "value": opt.value,
                "branches": opt.branches,
                "local_path": opt.local_path,
            }
            for opt in ctx.repo_options
        ],
    }


def _load_context(instance: CardInstance) -> MeegoCardContext:
    try:
        data = json.loads(instance.context_json)
    except json.JSONDecodeError:
        data = {}
    repo_options = [
        RepoOption(
            label=str(item.get("label") or ""),
            value=str(item.get("value") or ""),
            branches=[str(branch) for branch in item.get("branches") or [] if branch],
            local_path=str(item.get("local_path") or ""),
        )
        for item in data.get("repo_options") or []
        if isinstance(item, dict)
    ]
    return MeegoCardContext(
        mode=str(data.get("mode") or ""),
        repo_options=repo_options,
        selected_repo=str(data.get("selected_repo") or ""),
        source_value=str(data.get("source_value") or ""),
        errors=[str(item) for item in data.get("errors") or []],
    )


def _merge_form_values(ctx: MeegoCardContext, action_ctx: CardActionContext) -> MeegoCardContext:
    source_value = (
        action_ctx.form_data.get("source")
        or action_ctx.form_data.get("meego_url")
        or action_ctx.form_data.get("chat_name")
        or ctx.source_value
    )
    selected_repo = (
        _extract_selected_value(action_ctx, "repo")
        or action_ctx.form_data.get("repo")
        or ctx.selected_repo
    )
    return MeegoCardContext(
        mode=ctx.mode,
        repo_options=ctx.repo_options,
        selected_repo=selected_repo,
        source_value=source_value,
        errors=[],
    )


def _replace_errors(ctx: MeegoCardContext, errors: list[str]) -> MeegoCardContext:
    return MeegoCardContext(
        mode=ctx.mode,
        repo_options=ctx.repo_options,
        selected_repo=ctx.selected_repo,
        source_value=ctx.source_value,
        errors=errors,
    )


def _validate(ctx: MeegoCardContext, *, require_repo: bool) -> list[str]:
    errors: list[str] = []
    source_label = _source_label(ctx.mode)
    if not ctx.source_value.strip():
        errors.append(f"`{source_label}` 不能为空")
    if ctx.mode == MEEGO_MODE_URL:
        value = ctx.source_value.strip()
        if value and not value.startswith(("http://", "https://")):
            errors.append("`meego链接` 必须是 http(s) 链接")
    if require_repo:
        if not ctx.selected_repo.strip():
            errors.append("`本地仓库` 不能为空")
        elif ctx.repo_options and ctx.selected_repo not in {opt.value for opt in ctx.repo_options}:
            errors.append("`本地仓库` 不是合法候选值")
    return errors


def _build_card(
    ctx: MeegoCardContext,
    warnings: list[str] | None = None,
    *,
    show_clipboard_fallback: bool = False,
    require_repo: bool = True,
) -> dict[str, Any]:
    elements: list[dict[str, Any]] = []
    if ctx.errors:
        message = "**处理失败**\n\n" + "\n".join(f"- {item}" for item in ctx.errors)
        if show_clipboard_fallback:
            message += f"\n\n{CLIPBOARD_FALLBACK_TEXT}"
        elements.append(
            _div(
                message
                + (
                    "\n\n如果提示 macOS `System Events` 权限违例，请在系统设置中允许当前终端/IDE 控制电脑后重试。"
                    if show_clipboard_fallback
                    else ""
                )
            )
        )
    elements.append(_note(_intro_text(ctx.mode, require_repo=require_repo)))
    if warnings:
        elements.append(
            _note(
                "上一次收集提示：\n"
                + "\n".join(f"- {_truncate(item, 300)}" for item in warnings[:5])
            )
        )
    form_elements: list[dict[str, Any]] = [_field_row(_source_label(ctx.mode), _source_input(ctx))]
    if require_repo:
        form_elements.append(_field_row("本地仓库", _repo_control(ctx)))
    form_elements.append(
        _button_row(
            _submit_button(),
            _button("取消", "default", "meego_cancel"),
        )
    )
    elements.append({"tag": "form", "name": "meego_form", "elements": form_elements})
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": "Meego Prompt 推送失败" if ctx.errors else _title(ctx.mode),
            },
            "template": "red" if ctx.errors else "blue",
        },
        "elements": elements,
    }


def _build_done_card(
    *,
    ctx: MeegoCardContext,
    result: CollectResult,
    warnings: list[str],
    delivery: str,
    require_repo: bool,
) -> dict[str, Any]:
    source_info = [
        f"**来源**：{_source_label(ctx.mode)}：{ctx.source_value}",
    ]
    if require_repo and ctx.selected_repo.strip():
        source_info.append(f"**本地仓库**：{ctx.selected_repo}")
    if result.associated_meego_url:
        source_info.append(f"**关联 Meego**：{result.associated_meego_url}")
    delivery_text = (
        "已打开 Trae 侧聊并尝试带入整理后的需求 Prompt。"
        if (delivery or "").strip() == MEEGO_DELIVERY_TRAE_SIDE_CHAT
        else "已通过智能体私聊（气泡消息）发送整理后的需求 Prompt。"
    )
    elements: list[dict[str, Any]] = [_div(delivery_text)]
    if _has_clipboard_fallback(delivery):
        elements.append(_note(CLIPBOARD_FALLBACK_TEXT))
    elements.append(_div("\n".join(source_info)))
    if warnings:
        elements.append(
            _note(
                "收集过程提示/错误：\n"
                + "\n".join(f"- {_truncate(item, 300)}" for item in warnings[:5])
            )
        )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "Meego 需求 Prompt 已处理"},
            "template": "green",
        },
        "elements": elements,
    }


def _has_clipboard_fallback(delivery: str) -> bool:
    return (delivery or MEEGO_DELIVERY_BUBBLE).strip() in {
        MEEGO_DELIVERY_BUBBLE,
        MEEGO_DELIVERY_TRAE_SIDE_CHAT,
    }


def _requires_repo(delivery: str) -> bool:
    return (delivery or MEEGO_DELIVERY_BUBBLE).strip() == MEEGO_DELIVERY_TRAE_SIDE_CHAT


def _source_input(ctx: MeegoCardContext) -> dict[str, Any]:
    placeholder = (
        "填写关联 Meego 的飞书群聊名称，支持不完整名称"
        if ctx.mode == MEEGO_MODE_CHAT
        else "填写 Meego 工作项链接"
    )
    return {
        "tag": "input",
        "name": "source",
        "required": True,
        "placeholder": {"tag": "plain_text", "content": placeholder},
        "max_length": 500,
        "value": {"action": "meego_source_changed"},
        **({"default_value": ctx.source_value} if ctx.source_value else {}),
    }


def _repo_control(ctx: MeegoCardContext) -> dict[str, Any]:
    if ctx.repo_options:
        option_values = [opt.value for opt in ctx.repo_options]
        control: dict[str, Any] = {
            "tag": "select_static",
            "name": "repo",
            "required": True,
            "placeholder": {"tag": "plain_text", "content": "选择本地仓库"},
            "options": [
                {"text": {"tag": "plain_text", "content": opt.label}, "value": opt.value}
                for opt in ctx.repo_options
            ],
            "value": {"action": "meego_repo_changed"},
        }
        if ctx.selected_repo in option_values:
            control["initial_option"] = ctx.selected_repo
        return control
    return {
        "tag": "input",
        "name": "repo",
        "required": True,
        "placeholder": {"tag": "plain_text", "content": "手动填写仓库名或 repo_id"},
        "max_length": 200,
        "value": {"action": "meego_repo_changed"},
        **({"default_value": ctx.selected_repo} if ctx.selected_repo else {}),
    }


def _extract_selected_value(ctx: CardActionContext, field: str) -> str:
    if ctx.form_data.get(field):
        return ctx.form_data[field]
    action = _extract_action(ctx.raw_event)
    for key in ("option", "selected_option"):
        value = action.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict) and value.get("value"):
            return str(value["value"])
    value = action.get("value")
    if isinstance(value, dict):
        for key in (field, "selected", "option"):
            if value.get(key):
                return str(value[key])
    return ""


def _extract_action(event: dict[str, Any]) -> dict[str, Any]:
    action = event.get("action")
    if isinstance(action, dict):
        return action
    event_obj = event.get("event")
    if isinstance(event_obj, dict) and isinstance(event_obj.get("action"), dict):
        return event_obj["action"]
    return {}


def _field_row(label: str, control: dict[str, Any]) -> dict[str, Any]:
    return {
        "tag": "column_set",
        "columns": [
            {"tag": "column", "width": "auto", "elements": [_div(f"**{label}**")]},
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [control]},
        ],
    }


def _button_row(*buttons: dict[str, Any]) -> dict[str, Any]:
    return {
        "tag": "column_set",
        "columns": [{"tag": "column", "elements": [button]} for button in buttons],
    }


def _submit_button() -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": "确定"},
        "type": "primary",
        "name": "meego_confirm",
        "action_type": "form_submit",
        "value": {"action": "meego_confirm"},
    }


def _button(text: str, btn_type: str, action_name: str) -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": btn_type,
        "name": action_name,
        "value": {"action": action_name},
    }


def _title(mode: str) -> str:
    if mode == MEEGO_MODE_CHAT:
        return "Meego 需求收集：飞书群"
    return "Meego 需求收集：Meego 链接"


def _source_label(mode: str) -> str:
    if mode == MEEGO_MODE_CHAT:
        return "关联 meego 的飞书群聊名称"
    return "meego链接"


def _intro_text(mode: str, *, require_repo: bool) -> str:
    if mode == MEEGO_MODE_CHAT:
        if require_repo:
            return "填写飞书群聊名称和本地仓库后，点击确定会模糊匹配群聊、读取群描述中的 Meego 链接，并整理需求 prompt。"
        return "填写飞书群聊名称后，点击确定会模糊匹配群聊、读取群描述中的 Meego 链接，并整理需求 prompt。"
    if require_repo:
        return "填写 Meego 链接和本地仓库后，点击确定会读取工作项信息，并整理需求 prompt。"
    return "填写 Meego 链接后，点击确定会读取工作项信息，并整理需求 prompt。"


def _terminal(title: str, template: str, content: str) -> dict[str, Any]:
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


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
