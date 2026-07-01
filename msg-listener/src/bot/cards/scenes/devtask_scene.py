"""DevTask card scene：由 /devtask 命令触发的独立开发任务卡片。

与需求卡片功能等价（选仓库/分支、可编辑 prompt、确定开发派发任务），
但数据与需求卡片解耦：不消费分析链路 demand，而是在确认时创建一条
合成 demand 用于驱动 DevTaskOrchestrator。
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from ...analysis.card_builder import CardContext, RepoOption, build_card
from ...analysis.record_repository import (
    DEMAND_CARD_SENT,
    DEMAND_DEV_CONFIRMED,
    Demand,
    RecordRepository,
)
from ...dev_tasks.orchestrator import (
    DevTaskConcurrencyLimitError,
    DevTaskOrchestrator,
    DevTaskRepoBusyError,
)
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

_INTRO_TEXT = "填写仓库、分支与需求 prompt 后点击「确定开发」。"
_HEADER_TITLE = "新建开发任务"
_HEADER_TEMPLATE = "blue"


class DevTaskCardScene:
    scene_key = "devtask"

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
        prompt = ctx.extra.get("prompt")
        if not isinstance(prompt, str):
            prompt = ""
        repos = await self.inventory_service.get_inventory()
        card_context = self._build_context(
            repo_options=_to_options(repos),
            demand_id="",
            prompt=prompt,
            selected_repo="",
            selected_branch="",
        )
        return self._render(card_context), _dump_context(card_context)

    async def handle_action(self, ctx: CardActionContext) -> CardActionResult:
        logger.info(
            "处理开发任务卡片 action=%s instance=%s form_data=%s",
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
            return self._handle_cancel(ctx)
        return CardActionResult(
            op=CARD_OP_IGNORE,
            next_status=CARD_STATUS_ACTIVE,
            error_text=f"未知操作：{ctx.action_name}",
        )

    async def build_expired_card(self, instance: CardInstance) -> dict[str, Any]:
        del instance
        return _terminal(
            "开发任务卡片已失效",
            "grey",
            "该开发任务卡片已失效。",
        )

    # ---------- 表单刷新 ----------

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
        refreshed = _replace_selection(
            base,
            selected_repo=selected_repo,
            selected_branch=selected_branch,
            prompt=_extract_prompt(ctx, base.prompt),
        )
        return self._refresh_result(refreshed)

    def _handle_branch_changed(self, ctx: CardActionContext) -> CardActionResult:
        base = _load_context(ctx.instance)
        selected_repo = ctx.form_data.get("repo") or base.selected_repo
        branches = _branches_for(base.repo_options, selected_repo)
        selected_branch = _extract_selected_value(ctx, "branch") or base.selected_branch
        if not base.requires_branch:
            selected_branch = selected_branch or _resolve_branch(branches, self.default_branch)
        elif branches and selected_branch not in branches:
            selected_branch = _resolve_branch(branches, self.default_branch)
        refreshed = _replace_selection(
            base,
            selected_repo=selected_repo,
            selected_branch=selected_branch,
            prompt=_extract_prompt(ctx, base.prompt),
        )
        return self._refresh_result(refreshed)

    def _handle_manual_changed(self, ctx: CardActionContext) -> CardActionResult:
        base = _load_context(ctx.instance)
        selected_repo = ctx.form_data.get("repo", base.selected_repo)
        branches = _branches_for(base.repo_options, selected_repo)
        selected_branch = ctx.form_data.get("branch", base.selected_branch)
        if not selected_branch:
            selected_branch = _resolve_branch(branches, self.default_branch)
        refreshed = _replace_selection(
            base,
            selected_repo=selected_repo,
            selected_branch=selected_branch,
            prompt=_extract_prompt(ctx, base.prompt),
        )
        return self._refresh_result(refreshed)

    def _refresh_result(self, refreshed: CardContext) -> CardActionResult:
        return CardActionResult(
            op=CARD_OP_SUCCESS,
            next_status=CARD_STATUS_ACTIVE,
            card=self._render(refreshed),
            context_json=json.dumps(_dump_context(refreshed), ensure_ascii=False),
        )

    # ---------- 确认 / 取消 ----------

    async def _handle_confirm(self, ctx: CardActionContext) -> CardActionResult:
        base = _load_context(ctx.instance)
        selected_repo = _extract_selected_value(ctx, "repo") or base.selected_repo
        branches = _branches_for(base.repo_options, selected_repo)
        selected_branch = _extract_selected_value(ctx, "branch") or base.selected_branch
        if not base.requires_branch:
            selected_branch = selected_branch or _resolve_branch(branches, self.default_branch)
        if not selected_repo.strip() or (base.requires_branch and not selected_branch.strip()):
            return CardActionResult(
                op=CARD_OP_IGNORE,
                next_status=CARD_STATUS_ACTIVE,
                error_text="您未填写仓库/分支" if base.requires_branch else "您未填写仓库",
            )
        edited_prompt = _extract_prompt(ctx, base.prompt)
        if not edited_prompt.strip():
            return CardActionResult(
                op=CARD_OP_IGNORE,
                next_status=CARD_STATUS_ACTIVE,
                error_text="请填写需求 prompt",
            )

        demand_id = base.demand_id
        if not demand_id:
            today = datetime.now().astimezone().strftime("%Y-%m-%d")
            demand = self.record_repository.upsert_demand(
                Demand(
                    demand_id="",
                    record_id=f"devtask_{uuid.uuid4().hex[:16]}",
                    record_date=today,
                    prompt=edited_prompt,
                    summary=edited_prompt.splitlines()[0] if edited_prompt.strip() else "",
                    repo=selected_repo,
                    branch=selected_branch,
                    status=DEMAND_CARD_SENT,
                )
            )
            demand_id = demand.demand_id
        else:
            # 失败重试复用已有合成 demand：刷新用户最新输入。
            self.record_repository.update_demand_inputs(
                demand_id=demand_id,
                prompt=edited_prompt,
                media=[],
            )

        # 回填 demand_id 的 context，供失败后重试复用，避免合成 demand 堆积。
        confirmed = _replace_selection(
            base,
            selected_repo=selected_repo,
            selected_branch=selected_branch,
            prompt=edited_prompt,
            demand_id=demand_id,
        )
        persisted_context = json.dumps(_dump_context(confirmed), ensure_ascii=False)

        logger.info(
            "开发任务卡片确认 instance=%s demand=%s repo=%s branch=%s",
            ctx.instance.instance_id,
            demand_id,
            selected_repo,
            selected_branch,
        )
        try:
            task_id = await self.dev_task_orchestrator.dispatch(
                demand_id=demand_id,
                repo_id=selected_repo,
                base_branch=selected_branch,
                instance_id=ctx.instance.instance_id,
            )
        except DevTaskConcurrencyLimitError as exc:
            logger.debug("开发任务并发已达上限 demand=%s: %s", demand_id, exc)
            return CardActionResult(
                op=CARD_OP_IGNORE,
                next_status=CARD_STATUS_ACTIVE,
                error_text="当前任务并发数已达上限，请稍后重试",
                context_json=persisted_context,
            )
        except DevTaskRepoBusyError as exc:
            logger.debug("同仓库已有活跃开发任务 demand=%s: %s", demand_id, exc)
            return CardActionResult(
                op=CARD_OP_IGNORE,
                next_status=CARD_STATUS_ACTIVE,
                error_text="同仓库下其他需求正在开发，请稍等",
                context_json=persisted_context,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("DevTaskOrchestrator.dispatch 失败 demand=%s: %s", demand_id, exc)
            if str(exc) == "kxcymc_busy":
                return CardActionResult(
                    op=CARD_OP_IGNORE,
                    next_status=CARD_STATUS_ACTIVE,
                    error_text="OpenAPI 未确认开始工作，请稍后重试",
                    context_json=persisted_context,
                )
            return CardActionResult(
                op=CARD_OP_IGNORE,
                next_status=CARD_STATUS_ACTIVE,
                error_text="执行器未确认开始工作，已截断本次请求，请稍后重试。",
                context_json=persisted_context,
            )
        self.record_repository.update_demand_selection(
            demand_id=demand_id,
            repo=selected_repo,
            branch=selected_branch,
            status=DEMAND_DEV_CONFIRMED,
        )
        logger.info(
            "开发任务卡片关闭并派发任务 instance=%s demand=%s task=%s",
            ctx.instance.instance_id,
            demand_id,
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

    def _handle_cancel(self, ctx: CardActionContext) -> CardActionResult:
        del ctx
        return CardActionResult(
            op=CARD_OP_CANCEL,
            next_status=CARD_STATUS_CANCELLED,
            card=_terminal("开发任务已取消", "grey", "已取消该开发任务卡片。"),
        )

    # ---------- 渲染 / 上下文构造 ----------

    def _render(self, ctx: CardContext) -> dict[str, Any]:
        return build_card(
            ctx,
            show_context_sections=False,
            header_title=_HEADER_TITLE,
            header_template=_HEADER_TEMPLATE,
            intro_text=_INTRO_TEXT,
        )

    def _build_context(
        self,
        *,
        repo_options: list[RepoOption],
        demand_id: str,
        prompt: str,
        selected_repo: str,
        selected_branch: str,
    ) -> CardContext:
        return CardContext(
            demand_id=demand_id,
            record_id="",
            record_date="",
            prompt=prompt,
            summary="",
            chat_type="",
            sender_name="",
            create_time="",
            anchor_text="",
            before_excerpt=[],
            after_excerpt=[],
            media_files=[],
            repo_options=repo_options,
            default_branch=self.default_branch,
            selected_repo=selected_repo,
            selected_branch=selected_branch,
            requires_branch=self.requires_branch,
        )


def _to_options(repos: list[RepoInfo]) -> list[RepoOption]:
    return [
        RepoOption(label=repo.label, value=repo.repo_id, branches=repo.branches)
        for repo in repos
    ]


def _dump_context(ctx: CardContext) -> dict[str, Any]:
    return {
        "demand_id": ctx.demand_id,
        "prompt": ctx.prompt,
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
        record_id="",
        record_date="",
        prompt=str(data.get("prompt") or ""),
        summary="",
        chat_type="",
        sender_name="",
        create_time="",
        anchor_text="",
        before_excerpt=[],
        after_excerpt=[],
        media_files=[],
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
    prompt: str | None = None,
    demand_id: str | None = None,
) -> CardContext:
    return CardContext(
        demand_id=ctx.demand_id if demand_id is None else demand_id,
        record_id="",
        record_date="",
        prompt=ctx.prompt if prompt is None else prompt,
        summary="",
        chat_type="",
        sender_name="",
        create_time="",
        anchor_text="",
        before_excerpt=[],
        after_excerpt=[],
        media_files=[],
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


def _raw_form_value(event: dict[str, Any], field: str) -> Any:
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


def _terminal(title: str, template: str, content: str) -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": template,
        },
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": content}}],
    }
