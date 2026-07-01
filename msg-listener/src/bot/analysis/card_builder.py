"""飞书互动卡片 JSON 构造。

输入：Demand 视图 + Record 摘要 + repo/branch 候选 + 媒体附加摘要。
输出：飞书 interactive card JSON 字符串（可直接作为 lark-cli `--content` 的参数）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

CARD_VERSION = "1.0"
DEFAULT_TEMPLATE = "blue"


@dataclass
class RepoOption:
    label: str
    value: str
    branches: list[str] = field(default_factory=list)
    local_path: str = ""


@dataclass
class CardContext:
    """构造卡片所需的全部上下文。"""

    demand_id: str
    record_id: str
    record_date: str
    prompt: str
    summary: str
    chat_type: str
    sender_name: str
    create_time: str
    anchor_text: str
    before_excerpt: list[str]
    after_excerpt: list[str]
    media_files: list[dict]
    repo_options: list[RepoOption]
    default_branch: str
    record_path: str = ""
    selected_repo: str = ""
    selected_branch: str = ""
    requires_branch: bool = True


def _truncate(text: str, limit: int = 240) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _build_div(text: str) -> dict:
    return {
        "tag": "div",
        "text": {"tag": "lark_md", "content": text},
    }


def _build_hr() -> dict:
    return {"tag": "hr"}


def _build_note(text: str) -> dict:
    return {
        "tag": "note",
        "elements": [{"tag": "lark_md", "content": text}],
    }


def _build_select(
    *,
    placeholder: str,
    name: str,
    options: list[dict],
    initial_value: str = "",
    value: dict | None = None,
    action_type: str = "",
    disabled: bool = False,
) -> dict:
    select: dict[str, Any] = {
        "tag": "select_static",
        "placeholder": {"tag": "plain_text", "content": placeholder},
        "options": options,
        "name": name,
    }
    if initial_value:
        select["initial_option"] = initial_value
    if value is not None:
        select["value"] = value
    if action_type:
        select["action_type"] = action_type
    if disabled:
        select["disabled"] = True
    return select


# 飞书卡片 input 组件的 max_length 平台硬上限为 1000，超过会导致整张卡片渲染失败
# （ErrCode 11310: max_length exceed the default maximum 1000），故卡片可编辑字段不得超过 1000。
_INPUT_MAX_LENGTH_LIMIT = 1000


def _build_input(
    *,
    placeholder: str,
    name: str,
    default_value: str = "",
    max_length: int = 200,
    action_name: str = "",
    required: bool = False,
    input_type: str = "",
    rows: int = 0,
    disabled: bool = False,
) -> dict[str, Any]:
    input_box: dict[str, Any] = {
        "tag": "input",
        "name": name,
        "placeholder": {"tag": "plain_text", "content": placeholder},
        "max_length": min(max_length, _INPUT_MAX_LENGTH_LIMIT),
    }
    if default_value:
        input_box["default_value"] = default_value
    if action_name:
        input_box["value"] = {"action": action_name}
    if required:
        input_box["required"] = True
    if input_type:
        input_box["input_type"] = input_type
    if rows > 0:
        input_box["rows"] = rows
    if disabled:
        input_box["disabled"] = True
    return input_box


def _build_multi_select(
    *,
    placeholder: str,
    name: str,
    options: list[dict],
    selected_values: list[str] | None = None,
) -> dict[str, Any]:
    component: dict[str, Any] = {
        "tag": "multi_select_static",
        "name": name,
        # 媒体多选为可选项（默认不选）；不显式置 false 时平台默认 required=true，
        # 会在未勾选时拦截表单提交并提示“有必填项未填写”。
        "required": False,
        "placeholder": {"tag": "plain_text", "content": placeholder},
        "options": options,
    }
    if selected_values:
        component["selected_values"] = selected_values
    return component


def _media_options(media_files: list[dict]) -> list[dict]:
    """仅图片可被选中并随任务发送；编号顺序与随后推送的单独消息一致。

    option.value 为图片在 media_files 中的绝对索引（字符串），便于回调侧反查。
    """
    options: list[dict] = []
    image_no = 0
    for idx, m in enumerate(media_files):
        if (m.get("kind") or "") != "image":
            continue
        image_no += 1
        options.append(
            {
                "text": {"tag": "plain_text", "content": f"图片{image_no}"},
                "value": str(idx),
            }
        )
    return options


def _build_button(
    *,
    text: str,
    btn_type: str,
    value: dict,
    name: str,
    action_type: str = "",
    disabled: bool = False,
) -> dict:
    button = {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": btn_type,
        "value": value,
        "name": name,
    }
    if action_type:
        button["action_type"] = action_type
    if disabled:
        button["disabled"] = True
    return button


def _build_submit_button(text: str, action_name: str, *, disabled: bool = False) -> dict[str, Any]:
    button = {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": "primary",
        "name": action_name,
        "action_type": "form_submit",
        "value": {"action": action_name},
    }
    if disabled:
        button["disabled"] = True
    return button


def _build_button_row(*buttons: dict[str, Any]) -> dict[str, Any]:
    return {
        "tag": "column_set",
        "columns": [
            {
                "tag": "column",
                "elements": [button],
            }
            for button in buttons
        ],
    }


def _build_field_row(label: str, control: dict[str, Any]) -> dict[str, Any]:
    return {
        "tag": "column_set",
        "columns": [
            {
                "tag": "column",
                "width": "auto",
                "vertical_align": "center",
                "elements": [_build_div(label)],
            },
            {
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "vertical_align": "center",
                "elements": [control],
            },
        ],
    }


def _build_manual_fields_row(*, repo_input: dict[str, Any], branch_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "tag": "column_set",
        "columns": [
            {
                "tag": "column",
                "width": "auto",
                "vertical_align": "center",
                "elements": [_build_div("**仓库**")],
            },
            {
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "vertical_align": "center",
                "elements": [repo_input],
            },
            {
                "tag": "column",
                "width": "auto",
                "vertical_align": "center",
                "elements": [_build_div("**分支**")],
            },
            {
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "vertical_align": "center",
                "elements": [branch_input],
            },
        ],
    }


def _summary_section(ctx: CardContext) -> list[dict]:
    summary_md = (
        f"**来源**：{ctx.chat_type or '-'} / {ctx.sender_name or '-'}　"
        f"**时间**：{ctx.create_time or '-'}\n"
        f"**record_id**：{ctx.record_id}"
    )
    elements: list[dict] = [_build_div(summary_md)]
    if ctx.summary and ctx.summary != ctx.prompt:
        elements.append(_build_note(f"概要：{_truncate(ctx.summary, 300)}"))
    return elements


def _evidence_section(ctx: CardContext) -> list[dict]:
    elements: list[dict] = [_build_hr()]
    if ctx.anchor_text:
        elements.append(
            _build_div(f"**锚点消息**\n\n{_truncate(ctx.anchor_text, 600)}")
        )
    if ctx.before_excerpt:
        joined = "\n".join(f"- {_truncate(t, 200)}" for t in ctx.before_excerpt[:5])
        elements.append(_build_div(f"**前文摘要**\n\n{joined}"))
    if ctx.after_excerpt:
        joined = "\n".join(f"- {_truncate(t, 200)}" for t in ctx.after_excerpt[:5])
        elements.append(_build_div(f"**后文摘要**\n\n{joined}"))
    return elements


def _prompt_input(ctx: CardContext) -> dict[str, Any]:
    return _build_input(
        placeholder="可编辑：发送给执行器的需求 prompt",
        name="prompt",
        default_value=ctx.prompt,
        max_length=1000,
        input_type="multiline_text",
        rows=4,
    )


def _media_block(ctx: CardContext) -> list[dict[str, Any]]:
    options = _media_options(ctx.media_files)
    if not options:
        return []
    return [
        _build_field_row(
            "**媒体附件**",
            _build_multi_select(
                placeholder="选择需要随任务发送的图片，编号与随后单独消息一致",
                name="media",
                options=options,
                selected_values=[],
            ),
        ),
    ]


def _form_section(ctx: CardContext) -> list[dict]:
    button_value = {
        "demand_id": ctx.demand_id,
        "record_id": ctx.record_id,
    }
    if ctx.repo_options:
        repo_options = [
            {
                "text": {"tag": "plain_text", "content": opt.label},
                "value": opt.value,
            }
            for opt in ctx.repo_options
        ]
        selected_repo = next(
            (opt for opt in ctx.repo_options if opt.value == ctx.selected_repo),
            None,
        )
        branch_pool: list[str] = []
        if selected_repo is not None:
            for br in selected_repo.branches:
                if br and br not in branch_pool:
                    branch_pool.append(br)
        else:
            for opt in ctx.repo_options:
                for br in opt.branches:
                    if br and br not in branch_pool:
                        branch_pool.append(br)
        if selected_repo is not None and not branch_pool and ctx.default_branch:
            branch_pool = [ctx.default_branch]
        selected_branch = ctx.selected_branch
        if selected_branch and selected_branch not in branch_pool:
            selected_branch = ""
        branch_options = [
            {"text": {"tag": "plain_text", "content": b}, "value": b}
            for b in branch_pool
        ]
        actions = [
            _build_select(
                placeholder="选择仓库",
                name="repo",
                options=repo_options,
                initial_value=selected_repo.value if selected_repo is not None else "",
                value={"action": "repo_changed"},
            ),
        ]
        if ctx.requires_branch:
            actions.append(
                _build_select(
                    placeholder="选择分支",
                    name="branch",
                    options=branch_options,
                    initial_value=selected_branch,
                    value={"action": "branch_changed"},
                    disabled=not ctx.selected_repo,
                )
            )
        # 仓库/分支保留在表单外，选择即时刷新分支；下方表单承载可编辑 prompt、图片多选与提交。
        confirm_disabled = not ctx.selected_repo or (
            ctx.requires_branch and not ctx.selected_branch
        )
        form_elements: list[dict[str, Any]] = [
            _build_field_row("**需求 prompt**", _prompt_input(ctx)),
            *_media_block(ctx),
            _build_button_row(
                _build_submit_button("确定开发", "dev_confirm", disabled=confirm_disabled),
                _build_button(
                    text="取消",
                    btn_type="default",
                    value={**button_value, "action": "cancel"},
                    name="cancel",
                ),
            ),
        ]
        return [
            _build_hr(),
            {
                "tag": "action",
                "actions": actions,
            },
            {
                "tag": "form",
                "name": "requirement_form",
                "elements": form_elements,
            },
        ]
    selected_branch = ctx.selected_branch or ctx.default_branch
    repo_input = _build_input(
        placeholder="手动填写仓库名或 repo_id",
        name="repo",
        default_value=ctx.selected_repo,
        required=True,
    )
    fields = repo_input
    if ctx.requires_branch:
        fields = _build_manual_fields_row(
            repo_input=repo_input,
            branch_input=_build_input(
                placeholder="手动填写基础分支",
                name="branch",
                default_value=selected_branch,
                required=True,
            ),
        )
    form_elements = [
        fields,
        _build_field_row("**需求 prompt**", _prompt_input(ctx)),
        *_media_block(ctx),
        _build_button_row(
            _build_submit_button("确定开发", "dev_confirm"),
            _build_button(
                text="取消",
                btn_type="default",
                value={**button_value, "action": "cancel"},
                name="cancel",
            ),
        ),
    ]
    return [
        _build_hr(),
        _build_note(
            "未获取到可选仓库，请手动填写 repo。"
            if not ctx.requires_branch
            else "未获取到可选仓库，请手动填写 repo 与 branch。"
        ),
        {"tag": "form", "name": "requirement_manual_form", "elements": form_elements},
    ]


def build_card(
    ctx: CardContext,
    *,
    show_context_sections: bool = True,
    header_title: str = "发现一个需求",
    header_template: str = DEFAULT_TEMPLATE,
    intro_text: str = "",
) -> dict:
    """构造飞书 interactive card 结构（dict）。

    - show_context_sections=False 时跳过来源/锚点区块，仅渲染表单（可选 intro note）。
    - header_title/header_template 控制卡片标题与配色。
    - intro_text 非空时在表单前插入一条说明 note。
    """
    elements: list[dict] = []
    if show_context_sections:
        elements.extend(_summary_section(ctx))
        elements.extend(_evidence_section(ctx))
    elif intro_text:
        elements.append(_build_note(intro_text))
    elements.extend(_form_section(ctx))

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": header_title},
            "template": header_template,
        },
        "elements": elements,
    }
    return card


def build_card_json(ctx: CardContext) -> str:
    return json.dumps(build_card(ctx), ensure_ascii=False)
