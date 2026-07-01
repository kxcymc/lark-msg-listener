"""Meego 需求收集核心编排逻辑。

仅通过 :mod:`.cli` 调用 `lark-cli` / `meegle` 固定指令获取必要信息，
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import tomllib

from .cli import run_json_command
from .figma_finder import find_figma_link_in_doc

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = PROJECT_ROOT / "config.toml"


@dataclass
class CollectResult:
    """需求收集结果。"""

    ok: bool = False
    source: str = ""
    title: str = ""
    prompt: str = ""
    prd_doc_url: str | None = None
    technical_doc_url: str | None = None
    design_doc_url: str | None = None
    associated_meego_url: str | None = None
    selected_chat: dict[str, Any] | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# prompt 拼装
# ---------------------------------------------------------------------------
# prompt 结尾文案的兜底默认值（当配置项 meego_prompt_content 为空时使用）。
DEFAULT_PROMPT_CONTENT = "根据以上信息，生成开发任务清单。"


def _build_prompt(
    *,
    title: str,
    prd_doc_url: str | None,
    technical_doc_url: str | None,
    design_doc_url: str | None,
    prompt_content: str = "",
) -> str:
    tail = (prompt_content or "").strip() or DEFAULT_PROMPT_CONTENT
    return (
        f"需求：{title}\n"
        f"PRD：{prd_doc_url or ''}\n"
        f"技术方案：{technical_doc_url or ''}\n"
        f"设计稿：{design_doc_url or ''}\n"
        f"\n{tail}"
    )


def _host_from_value(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    return (parsed.hostname or "").strip().lower().rstrip(".")


def _load_config_requirement_host() -> str:
    try:
        with CONFIG_PATH.open("rb") as f:
            cfg = tomllib.load(f)
    except FileNotFoundError:
        return ""
    except tomllib.TOMLDecodeError as exc:
        logger.warning("读取 meego_requirement_host 失败，config.toml 解析错误：%s", exc)
        return ""

    card_cfg = ((cfg.get("phase2") or {}).get("card") or {})
    return str(card_cfg.get("meego_requirement_host", "") or "").strip()


def _resolve_requirement_host(requirement_host: str = "") -> str:
    # 群描述关联链接和 Meegle 登录态必须以 config.toml 中的 Meego 域名为准。
    configured_host = _host_from_value(requirement_host)
    if configured_host:
        return configured_host
    return _host_from_value(_load_config_requirement_host())


# ---------------------------------------------------------------------------
# Meego 工作项字段提取
# ---------------------------------------------------------------------------
def _extract_work_item_fields(detail_data: Any) -> list[dict[str, Any]]:
    if not isinstance(detail_data, dict):
        return []
    fields = detail_data.get("work_item_fields")
    if isinstance(fields, list):
        return [item for item in fields if isinstance(item, dict)]
    return []


def _extract_title(detail_data: Any) -> str:
    if not isinstance(detail_data, dict):
        return ""
    attribute = detail_data.get("work_item_attribute")
    if isinstance(attribute, dict):
        return str(attribute.get("work_item_name") or "")
    return ""


def _value_for_key(fields: list[dict[str, Any]], key: str) -> str | None:
    """从 work_item_fields 里按 field_key 取出 http 链接值（值为人工填写，可能为空）。"""
    if not key:
        return None
    for item in fields:
        if str(item.get("key") or "") != key:
            continue
        value = item.get("value")
        if isinstance(value, str) and value.startswith("http"):
            return value
        return None
    return None


def _select_link_field_key(meta_list: list[dict[str, Any]], *candidate_names: str) -> str | None:
    """从 meta-fields 列表里挑出最匹配的「链接」字段 field_key。

    仅考虑 ``field_type == "link"`` 的字段，按「精确名 → 包含名 → 第一个链接字段」优先级匹配。
    """
    link_fields = [
        item
        for item in meta_list
        if isinstance(item, dict) and str(item.get("field_type") or "") == "link"
    ]
    candidate_names_lower = [name.lower() for name in candidate_names]
    for matcher in (
        lambda name: name in candidate_names_lower,
        lambda name: any(candidate in name for candidate in candidate_names_lower),
    ):
        for item in link_fields:
            name = str(item.get("field_name") or "").lower()
            if matcher(name):
                key = str(item.get("field_key") or "").strip()
                if key:
                    return key
    if link_fields:
        return str(link_fields[0].get("field_key") or "").strip() or None
    return None


async def _resolve_link_field_key(
    project_key: str,
    work_item_type: str,
    env_overrides: dict[str, str] | None,
    field_query: str,
    *candidate_names: str,
) -> str | None:
    """通过 ``meegle workitem meta-fields --field-query`` 模糊匹配，定位链接字段的稳定 field_key。"""
    command = [
        "meegle",
        "workitem",
        "meta-fields",
        "--project-key",
        project_key,
        "--work-item-type",
        work_item_type,
        "--field-query",
        field_query,
        "--page-num",
        "1",
        "--envelope",
        "--format",
        "json",
    ]
    result = await run_json_command(command, env_overrides=env_overrides, timeout_seconds=20)
    payload = result.data if isinstance(result.data, dict) else None
    data = payload.get("data") if isinstance(payload, dict) else None
    meta_list = data.get("list") if isinstance(data, dict) else None
    if not isinstance(meta_list, list):
        return None
    return _select_link_field_key(meta_list, *candidate_names)


async def _fetch_work_item_values(
    work_item_id: str,
    project_key: str,
    env_overrides: dict[str, str] | None,
    field_keys: list[str],
) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]]]:
    """一次性按指定 field_key 拉取工作项标题与对应字段值（单页，无需翻全部字段）。"""
    errors: list[dict[str, Any]] = []
    command = [
        "meegle",
        "workitem",
        "get",
        "--work-item-id",
        work_item_id,
        "--project-key",
        project_key,
    ]
    for key in field_keys:
        if key:
            command.extend(["--fields", key])
    command.extend(["--envelope", "--format", "json"])
    page = await run_json_command(command, env_overrides=env_overrides, timeout_seconds=30)
    payload = page.data if isinstance(page.data, dict) else None
    data = payload.get("data") if isinstance(payload, dict) else None
    error = payload.get("error") if isinstance(payload, dict) else None
    if error:
        errors.append({"step": "workitem_get", "error": error})
        return [], "", errors
    if not page.ok or not isinstance(data, dict):
        errors.append(
            {
                "step": "workitem_get",
                "returncode": page.returncode,
                "stderr": page.stderr,
                "stdout": page.stdout,
            }
        )
        return [], "", errors
    return _extract_work_item_fields(data), _extract_title(data), errors


async def _fallback_design_from_docs(
    *,
    prd_doc_url: str | None,
    technical_doc_url: str | None,
    errors: list[dict[str, Any]],
) -> str | None:
    """设计稿字段为空时，依次在 PRD、技术文档中模糊查找 figma 链接。"""
    for step, doc_url in (("prd", prd_doc_url), ("technical", technical_doc_url)):
        if not doc_url:
            continue
        try:
            link = await find_figma_link_in_doc(doc_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "在 %s 文档中查找 figma 链接异常 doc=%s: %s", step, doc_url, exc
            )
            errors.append(
                {"step": f"figma_fallback_{step}", "message": str(exc)}
            )
            continue
        if link:
            return link
    return None


async def collect_from_meego_url(
    url: str,
    *,
    requirement_host: str = "",
    prompt_content: str = "",
) -> CollectResult:
    """根据 Meego 链接收集需求信息（标题 / PRD / 技术方案 / 设计稿）。"""
    result = CollectResult(source="meego_url", associated_meego_url=url)

    decode = await run_json_command(["meegle", "url", "decode", "--url", url, "--format", "json"])
    if not decode.ok or not isinstance(decode.data, dict):
        result.errors.append(
            {
                "step": "url_decode",
                "returncode": decode.returncode,
                "stderr": decode.stderr,
                "stdout": decode.stdout,
            }
        )
        return result

    decoded = decode.data
    resolved_host = str(decoded.get("host") or "").strip()
    simple_name = str(decoded.get("simple_name") or "")
    work_item_id = str(decoded.get("work_item_id") or "")
    work_item_type = str(decoded.get("work_item_type") or "").strip()
    if not simple_name or not work_item_id:
        result.errors.append(
            {"step": "url_decode", "message": "链接未解析出 simple_name 或 work_item_id"}
        )
        return result

    target_host = _host_from_value(resolved_host) or _resolve_requirement_host(requirement_host)
    if not target_host:
        result.errors.append(
            {
                "step": "resolve_host",
                "message": "未配置 meego_requirement_host，请在 config.toml 的 [phase2.card] 中填写。",
            }
        )
        return result
    env_overrides = {"MEEGLE_HOST": target_host}

    # 1) 通过 meta-fields 模糊匹配，并行定位 PRD / 技术文档 / 设计稿 三个链接字段的稳定 field_key。
    #    field_key 与具体工作项无关，只取决于 (project_key, work_item_type)，故可一次性解析。
    if work_item_type:
        prd_key, tech_key, design_key = await asyncio.gather(
            _resolve_link_field_key(
                simple_name, work_item_type, env_overrides, "PRD", "prd文档", "prd"
            ),
            _resolve_link_field_key(
                simple_name, work_item_type, env_overrides, "技术文档", "技术文档", "技术方案"
            ),
            _resolve_link_field_key(
                simple_name, work_item_type, env_overrides, "设计稿", "ui&ux设计稿", "设计稿"
            ),
        )
    else:
        prd_key = tech_key = design_key = None

    # 2) 一次性按 field_key 拉取标题 + 这几个字段的值（单页请求，无需翻全部字段）。
    wanted_keys = [k for k in (prd_key, tech_key, design_key) if k]
    fields, title, field_errors = await _fetch_work_item_values(
        work_item_id, simple_name, env_overrides, wanted_keys
    )
    result.errors.extend(field_errors)
    if not title and field_errors:
        # 一条都没拿到，通常是未登录或无权限。
        auth = await run_json_command(
            ["meegle", "auth", "status", "--format", "json"], env_overrides=env_overrides
        )
        if isinstance(auth.data, dict) and auth.data.get("authenticated") is False:
            result.errors.append(
                {
                    "step": "auth_status",
                    "message": (
                        f"当前 host={target_host} 未登录或 token 无效，"
                        f"请执行 `meegle auth login --device-code --host {target_host}`"
                    ),
                }
            )
        return result

    result.title = title
    # 值由人工填写，可能为空或字段不存在，均按缺省留空处理。
    result.prd_doc_url = _value_for_key(fields, prd_key) if prd_key else None
    result.technical_doc_url = _value_for_key(fields, tech_key) if tech_key else None
    result.design_doc_url = _value_for_key(fields, design_key) if design_key else None
    if not result.design_doc_url:
        result.design_doc_url = await _fallback_design_from_docs(
            prd_doc_url=result.prd_doc_url,
            technical_doc_url=result.technical_doc_url,
            errors=result.errors,
        )
    result.prompt = _build_prompt(
        title=title,
        prd_doc_url=result.prd_doc_url,
        technical_doc_url=result.technical_doc_url,
        design_doc_url=result.design_doc_url,
        prompt_content=prompt_content,
    )
    result.ok = True
    return result


# ---------------------------------------------------------------------------
# 飞书群收集（用于定位群描述里关联的 Meego 链接）
# ---------------------------------------------------------------------------
def _extract_urls(text: str) -> list[str]:
    return re.findall(r"https?://[^\s]+", text or "")


def _find_associated_meego_url(
    chat: dict[str, Any],
    *,
    requirement_host: str = "",
) -> str | None:
    target_host = _resolve_requirement_host(requirement_host)
    if not target_host:
        return None
    description = str(chat.get("description") or "")
    for url in _extract_urls(description):
        if _host_from_value(url) == target_host:
            return url
    return None


def _normalize_text(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _score_chat(query: str, chat: dict[str, Any]) -> tuple[int, int, int]:
    query_norm = _normalize_text(query)
    name_norm = _normalize_text(str(chat.get("name") or ""))
    if not name_norm:
        return (0, 0, 0)
    exact = 1 if name_norm == query_norm else 0
    contains = 1 if query_norm and query_norm in name_norm else 0
    prefix = 1 if query_norm and name_norm.startswith(query_norm) else 0
    return (exact, contains + prefix, -abs(len(name_norm) - len(query_norm)))


def _extract_list(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    containers = [data, payload]
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in keys:
            value = container.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


async def collect_from_lark_group(
    query: str,
    *,
    page_size: int = 10,
    requirement_host: str = "",
    prompt_content: str = "",
) -> CollectResult:
    """根据飞书群名称模糊匹配，提取群描述里关联的 Meego 链接后复用 Meego 收集。"""
    result = CollectResult(source="lark_group")

    auth = await run_json_command(["lark-cli", "auth", "status", "--verify"])
    if not auth.ok:
        result.errors.append(
            {
                "step": "auth_status",
                "returncode": auth.returncode,
                "stderr": auth.stderr,
                "stdout": auth.stdout,
            }
        )
        return result

    search = await run_json_command(
        [
            "lark-cli",
            "im",
            "+chat-search",
            "--query",
            query,
            "--disable-search-by-user",
            "--page-size",
            str(page_size),
            "--format",
            "json",
        ]
    )
    if not search.ok:
        result.errors.append(
            {
                "step": "chat_search",
                "returncode": search.returncode,
                "stderr": search.stderr,
                "stdout": search.stdout,
            }
        )
        return result

    chats = _extract_list(search.data, "chats")
    if not chats:
        result.errors.append({"step": "chat_search", "message": "未搜索到匹配群聊"})
        return result

    selected_chat = sorted(chats, key=lambda item: _score_chat(query, item), reverse=True)[0]
    result.selected_chat = selected_chat
    associated_meego_url = _find_associated_meego_url(
        selected_chat,
        requirement_host=requirement_host,
    )
    if not associated_meego_url:
        result.errors.append({"step": "associated_meego_url", "message": "群聊没有关联 Meego 链接"})
        return result

    meego_result = await collect_from_meego_url(
        associated_meego_url,
        requirement_host=requirement_host,
        prompt_content=prompt_content,
    )
    meego_result.source = "lark_group"
    meego_result.selected_chat = selected_chat
    meego_result.associated_meego_url = associated_meego_url
    return meego_result
