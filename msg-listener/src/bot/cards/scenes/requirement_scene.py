"""Requirement card scene backed by local git inventory."""
from __future__ import annotations

import json
import logging
from typing import Any

from ...analysis.card_builder import CardContext, RepoOption, build_card
from ...analysis.record_repository import (
    DEMAND_CANCELLED,
    DEMAND_DEV_CONFIRMED,
    RecordRepository,
)
from ...dev_tasks.orchestrator import DevTaskOrchestrator
from ...dev_tasks.orchestrator import DevTaskConcurrencyLimitError, DevTaskRepoBusyError
from ...dev_tasks.repository import DevTaskRepository
from ...git_inventory import GitInventoryService, RepoInfo
from ...notifications.bubble_notifier import BubbleNotifier
from ..base import (
    CARD_OP_CANCEL,
    CARD_OP_IGNORE,
    CARD_OP_SUCCESS,
    CARD_STATUS_ACTIVE,
    CARD_STATUS_CANCELLED,
    CARD_STATUS_CLOSED,
    CardActionContext,
    CardActionResult,
    CardInstance,
    CardOpenContext,
)

logger = logging.getLogger(__name__)


class RequirementCardScene:
    scene_key = "requirement"

    def __init__(
        self,
        *,
        inventory_service: GitInventoryService,
        record_repository: RecordRepository,
        dev_task_repository: DevTaskRepository,
        dev_task_orchestrator: DevTaskOrchestrator,
        bubble_notifier: BubbleNotifier,
        default_branch: str,
        requires_branch: bool = True,
    ) -> None:
        self.inventory_service = inventory_service
        self.record_repository = record_repository
        self.dev_task_repository = dev_task_repository
        self.dev_task_orchestrator = dev_task_orchestrator
        self.bubble_notifier = bubble_notifier
        self.default_branch = default_branch
        self.requires_branch = requires_branch

    async def build_open_card(
        self,
        ctx: CardOpenContext,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        demand_row = ctx.extra.get("demand_row")
        record = ctx.extra.get("record")
        if not isinstance(demand_row, dict) or not isinstance(record, dict):
            raise ValueError("RequirementCardScene 缺少 demand_row / record")
        repos = await self.inventory_service.get_inventory()
        card_context = _build_context(
            demand_row=demand_row,
            record=record,
            repo_options=_to_options(repos),
            default_branch=self.default_branch,
            requires_branch=self.requires_branch,
        )
        return build_card(card_context), _dump_context(card_context)

    async def handle_action(self, ctx: CardActionContext) -> CardActionResult:
        logger.info(
            "处理需求卡片 action=%s instance=%s form_data=%s",
            ctx.action_name,
            ctx.instance.instance_id,
            ctx.form_data,
        )
        if ctx.action_name in {"repo", "repo_changed"}:
            return self._handle_repo_changed(ctx)
        if ctx.action_name in {"branch", "branch_changed"}:
            return self._handle_branch_changed(ctx)
        if ctx.action_name == "manual_changed":
            return self._handle_manual_changed(ctx)
        if ctx.action_name == "dev_confirm":
            return await self._handle_confirm(ctx)
        if ctx.action_name == "cancel":
            return await self._handle_cancel(ctx)
        return CardActionResult(
            op=CARD_OP_IGNORE,
            next_status=CARD_STATUS_ACTIVE,
            error_text=f"未知操作：{ctx.action_name}",
        )

    async def build_expired_card(self, instance: CardInstance) -> dict[str, Any]:
        del instance  # 协议要求保留入参；当前实现不依赖具体实例信息。
        return _terminal(
            "需求卡片已失效",
            "grey",
            "该需求卡片已失效（被新版本卡片替代）。",
        )

    def _handle_repo_changed(self, ctx: CardActionContext) -> CardActionResult:
        base = _load_context(ctx.instance)
        selected_repo = _extract_selected_value(ctx, "repo") or base.selected_repo
        branches = _branches_for(base.repo_options, selected_repo)
        if base.requires_branch:
            selected_branch = (
                base.selected_branch
                if not branches or base.selected_branch in branches
                else _resolve_branch(branches, self.default_branch)
            )
        else:
            selected_branch = base.selected_branch or _resolve_branch(branches, self.default_branch)
        logger.info(
            "需求卡片仓库切换 instance=%s selected_repo=%s branches=%s selected_branch=%s",
            ctx.instance.instance_id,
            selected_repo,
            branches,
            selected_branch,
        )
        refreshed = _replace_selection(
            base,
            selected_repo=selected_repo,
            selected_branch=selected_branch,
        )
        return CardActionResult(
            op=CARD_OP_SUCCESS,
            next_status=CARD_STATUS_ACTIVE,
            card=build_card(refreshed),
            context_json=json.dumps(_dump_context(refreshed), ensure_ascii=False),
        )

    def _handle_branch_changed(self, ctx: CardActionContext) -> CardActionResult:
        base = _load_context(ctx.instance)
        # branch select 回调里 option 往往就是 branch 值，不应再反推 repo。
        selected_repo = ctx.form_data.get("repo") or base.selected_repo
        branches = _branches_for(base.repo_options, selected_repo)
        selected_branch = _extract_selected_value(ctx, "branch") or base.selected_branch
        if not base.requires_branch:
            selected_branch = selected_branch or _resolve_branch(branches, self.default_branch)
        elif branches and selected_branch not in branches:
            selected_branch = _resolve_branch(branches, self.default_branch)
        logger.debug(
            "需求卡片分支切换 instance=%s selected_repo=%s selected_branch=%s",
            ctx.instance.instance_id,
            selected_repo,
            selected_branch,
        )
        refreshed = _replace_selection(
            base,
            selected_repo=selected_repo,
            selected_branch=selected_branch,
        )
        return CardActionResult(
            op=CARD_OP_SUCCESS,
            next_status=CARD_STATUS_ACTIVE,
            card=build_card(refreshed),
            context_json=json.dumps(_dump_context(refreshed), ensure_ascii=False),
        )

    def _handle_manual_changed(self, ctx: CardActionContext) -> CardActionResult:
        base = _load_context(ctx.instance)
        selected_repo = ctx.form_data.get("repo", base.selected_repo)
        branches = _branches_for(base.repo_options, selected_repo)
        selected_branch = ctx.form_data.get("branch", base.selected_branch)
        if not selected_branch:
            selected_branch = _resolve_branch(branches, self.default_branch)
        logger.debug(
            "需求卡片手填变更 instance=%s selected_repo=%s selected_branch=%s form_data=%s",
            ctx.instance.instance_id,
            selected_repo,
            selected_branch,
            ctx.form_data,
        )
        refreshed = _replace_selection(
            base,
            selected_repo=selected_repo,
            selected_branch=selected_branch,
        )
        return CardActionResult(
            op=CARD_OP_SUCCESS,
            next_status=CARD_STATUS_ACTIVE,
            card=build_card(refreshed),
            context_json=json.dumps(_dump_context(refreshed), ensure_ascii=False),
        )

    async def _handle_confirm(self, ctx: CardActionContext) -> CardActionResult:
        base = _load_context(ctx.instance)
        selected_repo = _extract_selected_value(ctx, "repo") or base.selected_repo
        branches = _branches_for(base.repo_options, selected_repo)
        selected_branch = _extract_selected_value(ctx, "branch") or base.selected_branch
        if not base.requires_branch:
            selected_branch = selected_branch or _resolve_branch(branches, self.default_branch)
        if not selected_repo.strip() or (base.requires_branch and not selected_branch.strip()):
            logger.debug(
                "需求卡片确认缺少仓库或分支 instance=%s repo=%s branch=%s form_data=%s",
                ctx.instance.instance_id,
                selected_repo,
                selected_branch,
                ctx.form_data,
            )
            return CardActionResult(
                op=CARD_OP_IGNORE,
                next_status=CARD_STATUS_ACTIVE,
                error_text="您未填写仓库/分支" if base.requires_branch else "您未填写仓库",
            )
        edited_prompt = _extract_prompt(ctx, base.prompt)
        selected_media = _extract_selected_media(ctx, base.media_files)
        logger.info(
            "需求卡片确认 instance=%s repo=%s branch=%s media=%d form_data=%s",
            ctx.instance.instance_id,
            selected_repo,
            selected_branch,
            len(selected_media),
            ctx.form_data,
        )
        # 派发前先持久化编辑后的 prompt 与勾选的媒体，确保执行器读取到的是用户确认的内容。
        self.record_repository.update_demand_inputs(
            demand_id=base.demand_id,
            prompt=edited_prompt,
            media=selected_media,
        )
        try:
            task_id = await self.dev_task_orchestrator.dispatch(
                demand_id=base.demand_id,
                repo_id=selected_repo,
                base_branch=selected_branch,
                instance_id=ctx.instance.instance_id,
            )
        except DevTaskConcurrencyLimitError as exc:
            logger.debug(
                "开发任务并发已达上限 demand=%s instance=%s: %s",
                base.demand_id,
                ctx.instance.instance_id,
                exc,
            )
            return CardActionResult(
                op=CARD_OP_IGNORE,
                next_status=CARD_STATUS_ACTIVE,
                error_text="当前任务并发数已达上限，请稍后重试",
            )
        except DevTaskRepoBusyError as exc:
            logger.debug(
                "同仓库已有活跃开发任务 demand=%s instance=%s: %s",
                base.demand_id,
                ctx.instance.instance_id,
                exc,
            )
            return CardActionResult(
                op=CARD_OP_IGNORE,
                next_status=CARD_STATUS_ACTIVE,
                error_text="同仓库下其他需求正在开发，请稍等",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "DevTaskOrchestrator.dispatch 失败 demand=%s: %s",
                base.demand_id,
                exc,
            )
            if str(exc) == "kxcymc_busy":
                return CardActionResult(
                    op=CARD_OP_IGNORE,
                    next_status=CARD_STATUS_ACTIVE,
                    error_text="OpenAPI 未确认开始工作，请稍后重试",
                )
            return CardActionResult(
                op=CARD_OP_IGNORE,
                next_status=CARD_STATUS_ACTIVE,
                error_text="执行器未确认开始工作，已截断本次请求，请稍后重试。",
            )
        self.record_repository.update_demand_selection(
            demand_id=base.demand_id,
            repo=selected_repo,
            branch=selected_branch,
            status=DEMAND_DEV_CONFIRMED,
        )
        logger.info(
            "需求卡片关闭并派发任务 instance=%s demand=%s task=%s",
            ctx.instance.instance_id,
            base.demand_id,
            task_id,
        )
        return CardActionResult(
            op=CARD_OP_SUCCESS,
            next_status=CARD_STATUS_CLOSED,
            card=_terminal(
                "已打开Trae侧聊" if self.dev_task_orchestrator.is_local_executor else "已派发开发任务",
                "green",
                "手动开发你的需求吧！"
                if self.dev_task_orchestrator.is_local_executor
                else "该需求已进入后台处理，后续进展将通过气泡通知。",
            ),
        )

    async def _handle_cancel(self, ctx: CardActionContext) -> CardActionResult:
        base = _load_context(ctx.instance)
        self.dev_task_repository.update_dispatch_status(
            demand_id=base.demand_id,
            dispatch_status="not-resolved",
        )
        self.record_repository.update_demand_status(base.demand_id, DEMAND_CANCELLED)
        demand_row = self.record_repository.get_demand(base.demand_id) or {
            "demand_id": base.demand_id,
            "summary": base.summary,
            "repo": base.selected_repo,
            "branch": base.selected_branch,
        }
        try:
            await self.bubble_notifier.notify_cancelled(demand_row=demand_row)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "取消气泡发送失败 demand=%s: %s",
                base.demand_id,
                exc,
            )
        return CardActionResult(
            op=CARD_OP_CANCEL,
            next_status=CARD_STATUS_CANCELLED,
            card=_terminal("需求已取消", "grey", "已取消该需求卡片。"),
        )


def _to_options(repos: list[RepoInfo]) -> list[RepoOption]:
    return [
        RepoOption(label=repo.label, value=repo.repo_id, branches=repo.branches)
        for repo in repos
    ]


def _build_context(
    *,
    demand_row: dict,
    record: dict,
    repo_options: list[RepoOption],
    default_branch: str,
    requires_branch: bool,
) -> CardContext:
    anchor = record.get("anchor") or {}
    selected_repo = demand_row.get("repo") or ""
    branches = _branches_for(repo_options, selected_repo)
    selected_branch = demand_row.get("branch") or ""
    if selected_branch and branches and selected_branch not in branches:
        selected_branch = ""
    return CardContext(
        demand_id=demand_row["demand_id"],
        record_id=demand_row["record_id"],
        record_date=demand_row.get("record_date") or "",
        prompt=demand_row.get("prompt") or "",
        summary=demand_row.get("summary") or "",
        chat_type=record.get("chat_type") or "",
        sender_name=anchor.get("sender_name") or "",
        create_time=anchor.get("create_time") or "",
        anchor_text=anchor.get("text") or "",
        before_excerpt=[],
        after_excerpt=[],
        media_files=_loads_list(demand_row.get("media_json") or "[]"),
        repo_options=repo_options,
        default_branch=default_branch,
        selected_repo=selected_repo,
        selected_branch=selected_branch,
        requires_branch=requires_branch,
    )


def _dump_context(ctx: CardContext) -> dict[str, Any]:
    return {
        "demand_id": ctx.demand_id,
        "record_id": ctx.record_id,
        "record_date": ctx.record_date,
        "prompt": ctx.prompt,
        "summary": ctx.summary,
        "chat_type": ctx.chat_type,
        "sender_name": ctx.sender_name,
        "create_time": ctx.create_time,
        "anchor_text": ctx.anchor_text,
        "before_excerpt": ctx.before_excerpt,
        "after_excerpt": ctx.after_excerpt,
        "media_files": ctx.media_files,
        "repo_options": [
            {"label": opt.label, "value": opt.value, "branches": opt.branches}
            for opt in ctx.repo_options
        ],
        "default_branch": ctx.default_branch,
        "selected_repo": ctx.selected_repo,
        "selected_branch": ctx.selected_branch,
        "requires_branch": ctx.requires_branch,
    }


def _load_context(instance: CardInstance) -> CardContext:
    try:
        data = json.loads(instance.context_json)
    except json.JSONDecodeError:
        data = {}
    repo_options = [
        RepoOption(
            label=str(item.get("label") or ""),
            value=str(item.get("value") or ""),
            branches=[str(branch) for branch in item.get("branches") or [] if branch],
        )
        for item in data.get("repo_options") or []
        if isinstance(item, dict)
    ]
    return CardContext(
        demand_id=str(data.get("demand_id") or ""),
        record_id=str(data.get("record_id") or ""),
        record_date=str(data.get("record_date") or ""),
        prompt=str(data.get("prompt") or ""),
        summary=str(data.get("summary") or ""),
        chat_type=str(data.get("chat_type") or ""),
        sender_name=str(data.get("sender_name") or ""),
        create_time=str(data.get("create_time") or ""),
        anchor_text=str(data.get("anchor_text") or ""),
        before_excerpt=[str(item) for item in data.get("before_excerpt") or []],
        after_excerpt=[str(item) for item in data.get("after_excerpt") or []],
        media_files=[item for item in data.get("media_files") or [] if isinstance(item, dict)],
        repo_options=repo_options,
        default_branch=str(data.get("default_branch") or ""),
        selected_repo=str(data.get("selected_repo") or ""),
        selected_branch=str(data.get("selected_branch") or ""),
        requires_branch=bool(data.get("requires_branch", True)),
    )


def _replace_selection(
    ctx: CardContext,
    *,
    selected_repo: str,
    selected_branch: str,
) -> CardContext:
    return CardContext(
        demand_id=ctx.demand_id,
        record_id=ctx.record_id,
        record_date=ctx.record_date,
        prompt=ctx.prompt,
        summary=ctx.summary,
        chat_type=ctx.chat_type,
        sender_name=ctx.sender_name,
        create_time=ctx.create_time,
        anchor_text=ctx.anchor_text,
        before_excerpt=ctx.before_excerpt,
        after_excerpt=ctx.after_excerpt,
        media_files=ctx.media_files,
        repo_options=ctx.repo_options,
        default_branch=ctx.default_branch,
        selected_repo=selected_repo,
        selected_branch=selected_branch,
        requires_branch=ctx.requires_branch,
    )


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


def _extract_prompt(ctx: CardActionContext, fallback: str) -> str:
    raw = _raw_form_value(ctx.raw_event, "prompt")
    if isinstance(raw, str) and raw.strip():
        return raw
    text = ctx.form_data.get("prompt")
    if isinstance(text, str) and text.strip():
        return text
    return fallback


def _extract_selected_media(
    ctx: CardActionContext,
    media_files: list[dict],
) -> list[dict]:
    raw = _raw_form_value(ctx.raw_event, "media")
    values: list[str] = []
    if isinstance(raw, list):
        values = [str(v) for v in raw]
    elif isinstance(raw, str) and raw:
        values = [raw]
    selected: list[dict] = []
    for value in values:
        try:
            idx = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(media_files):
            selected.append(media_files[idx])
    return selected


def _raw_form_value(event: dict[str, Any], field: str) -> Any:
    """从原始回调事件中读取 form 提交值，保留多选数组等原始类型。"""
    action = _extract_action(event)
    for source in (
        action.get("form_value"),
        action.get("form_data"),
        event.get("form_value"),
        event.get("form_data"),
    ):
        if isinstance(source, dict) and field in source:
            return source[field]
    return None


def _extract_action(event: dict[str, Any]) -> dict[str, Any]:
    action = event.get("action")
    if isinstance(action, dict):
        return action
    event_obj = event.get("event")
    if isinstance(event_obj, dict) and isinstance(event_obj.get("action"), dict):
        return event_obj["action"]
    return {}


def _branches_for(repo_options: list[RepoOption], repo_id: str) -> list[str]:
    for option in repo_options:
        if option.value == repo_id:
            return option.branches
    return []


def _resolve_branch(branches: list[str], default_branch: str) -> str:
    if default_branch and default_branch in branches:
        return default_branch
    return branches[0] if branches else default_branch


def _loads_list(text: str) -> list[dict]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _terminal(title: str, template: str, content: str) -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": template,
        },
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": content}}],
    }
