"""集中维护提交给开发任务执行器的 prompt 模板。"""
from __future__ import annotations


API_PROMPT_TEMPLATE = (
    "严格按照以下步骤完成开发任务：\n"
    "1. 基于基础分支 {base_branch} 创建新分支 {work_branch_hint}。\n"
    "2. 在新分支上完成下列需求：\n"
    "   - 标题：{requirement_summary}\n"
    "   - 详情：{requirement_detail}\n"
    "3. 完成后提交代码并发起 MR，目标分支为 {base_branch}。\n"
    "4. 最终响应必须严格返回一个合法 JSON 对象，不要包含 Markdown、说明文字或额外字段；\n"
    "成功时仅返回：{{\"repo\":\"example/repo\",\"work_branch\":\"feature/example\",\"mr_url\":\"https://example.com/path/to/mr\"}}；\n"
    "失败时仅返回：{{\"error_msg\":\"具体错误原因\"}}。\n"
)


CLI_PROMPT_TEMPLATE = (
    "完成开发任务：\n"
    "{requirement_detail}\n"
)


def build_api_dev_prompt(
    *,
    base_branch: str,
    work_branch_hint: str,
    requirement_summary: str,
    requirement_detail: str,
) -> str:
    return API_PROMPT_TEMPLATE.format(
        base_branch=base_branch or "main",
        work_branch_hint=work_branch_hint or "feature/auto-dev",
        requirement_summary=requirement_summary or "(无标题)",
        requirement_detail=requirement_detail or "(无详细描述)",
    )


def build_cli_dev_prompt(
    *,
    base_branch: str,
    work_branch_hint: str,
    requirement_summary: str,
    requirement_detail: str,
) -> str:
    return CLI_PROMPT_TEMPLATE.format(
        base_branch=base_branch or "main",
        work_branch_hint=work_branch_hint or "feature/auto-dev",
        requirement_summary=requirement_summary or "(无标题)",
        requirement_detail=requirement_detail or "(无详细描述)",
    )


def build_dev_prompt(
    *,
    base_branch: str,
    work_branch_hint: str,
    requirement_summary: str,
    requirement_detail: str,
) -> str:
    """兼容旧调用方；默认保持 API 执行器语义。"""
    return build_api_dev_prompt(
        base_branch=base_branch,
        work_branch_hint=work_branch_hint,
        requirement_summary=requirement_summary,
        requirement_detail=requirement_detail,
    )
