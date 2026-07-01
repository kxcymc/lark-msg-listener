"""config.toml 读写 + 白名单字段管理。

写回采用行级替换：定位到 `key = ...` 行（含 dotted path），保留注释/空行/顺序。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib  # py311+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

logger = logging.getLogger(__name__)


# 白名单字段：(dotted_key, 类型, 描述)
@dataclass
class WhitelistField:
    key: str  # dotted: "phase2.analysis_interval_seconds" / 顶层 "log_level"
    py_type: str  # int | float | bool | str
    desc: str


WHITELIST: list[WhitelistField] = [
    WhitelistField("context_before", "int", "命中消息前补充的同会话上下文条数"),
    WhitelistField("context_after", "int", "命中消息后补充的同会话上下文条数"),
    WhitelistField("context_after_max_wait_seconds", "int", "命中后等待拉取后文的最大秒数"),
    WhitelistField("log_level", "str", "日志级别"),
    WhitelistField("poll_p2p_enabled", "bool", "是否启用私聊消息轮询"),
    WhitelistField("poll_interval_seconds", "int", "消息轮询间隔秒"),
    WhitelistField("phase2.analysis_interval_seconds", "float", "分析轮询间隔秒"),
    WhitelistField("phase2.analysis_batch_size", "int", "单轮占坑批量"),
    WhitelistField("phase2.analysis_max_concurrency", "int", "分析并发上限"),
    WhitelistField("phase2.analysis_cli", "str", "需求判断 CLI"),
    WhitelistField("phase2.analysis_cli_model_name", "str", "需求判断 CLI 使用的模型名"),
    WhitelistField("phase2.analysis_max_attempts", "int", "单条 record 最大分析尝试次数"),
    WhitelistField("phase2.process_current_date_only", "bool", "是否仅扫描本地当日 records"),
    WhitelistField("phase2.card.meego_requirement_host", "str", "meego需求域名"),
    WhitelistField("phase2.card.meego_prompt_delivery", "str", "meego prompt 推送方式"),
    WhitelistField("phase2.card.meego_prompt_content", "str", "meego 的 prompt 内容"),
    WhitelistField("phase3.dev_task.executor", "str", "开发任务执行器"),
    WhitelistField("phase3.dev_task.max_concurrency", "int", "开发任务并发上限"),
    WhitelistField("phase3.dev_task.kxcymc_openapi.pat_token", "str", "kxcymc OpenAPI PAT（code_pat_...）"),
    WhitelistField("mywork.enabled", "bool", "是否启用 mywork 一次性收集与总结"),
    WhitelistField("mywork.lookback_days", "int", "mywork 搜索锚点消息的回看天数"),
    WhitelistField("mywork.context_window", "int", "mywork 锚点消息前后保留的上下文条数"),
    WhitelistField("mywork.chat_history_buffer_hours", "int", "mywork 拉取聊天历史的外扩缓冲小时数"),
    WhitelistField("mywork.page_size", "int", "mywork 单次列消息分页大小"),
    WhitelistField("mywork.max_pages_per_chat", "int", "mywork 单聊天最大翻页数"),
    WhitelistField("mywork.conclude_cli", "str", "mywork 总结 CLI"),
    WhitelistField("mywork.conclude_cli_model_name", "str", "mywork 总结 CLI 使用的模型名"),
    WhitelistField("mywork.conclude_cli_providers.kxcymc_cli.command", "str", "mywork kxcymc CLI 命令"),
    WhitelistField("mywork.conclude_cli_providers.kxcymc_cli.timeout_seconds", "int", "mywork kxcymc CLI 超时秒数"),
    WhitelistField("mywork.conclude_cli_providers.kxcymc_cli.doc_title_text", "str", "mywork 云文档标题文字"),
    WhitelistField("mywork.conclude_cli_providers.kxcymc_cli.prompt_template", "str", "mywork 总结内容清单"),
    WhitelistField("ccr.enabled", "bool", "是否启用 CCR 路由"),
    WhitelistField("ccr.auto_start", "bool", "网关未运行时是否自动 ccr start"),
    WhitelistField("ccr.base_url", "str", "CCR 网关地址(ANTHROPIC_BASE_URL)"),
    # WhitelistField("phase3.dev_task.claude_code_cli.command", "str", "Claude Code CLI 命令"),
    # WhitelistField("phase3.dev_task.claude_code_cli.model", "str", "Claude Code CLI 模型名"),
    # WhitelistField("phase3.dev_task.claude_code_cli.output_format", "str", "Claude Code CLI 输出格式"),
    # WhitelistField("phase3.dev_task.claude_code_cli.state_dir", "str", "Claude Code CLI 任务状态目录"),
]


SENSITIVE_KEYS: set[str] = {"phase3.dev_task.kxcymc_openapi.pat_token"}


class ConfigError(ValueError):
    pass


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def get_dotted(cfg: dict, key: str, default: Any = None) -> Any:
    cur: Any = cfg
    for part in key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def coerce_value(field: WhitelistField, raw: str) -> Any:
    if raw is None:
        raise ConfigError(f"`{field.key}` 缺少值")
    text = str(raw).strip()
    if field.py_type == "int":
        try:
            return int(text)
        except ValueError as exc:
            raise ConfigError(f"`{field.key}` 应为整数，得到 {raw!r}") from exc
    if field.py_type == "float":
        try:
            return float(text)
        except ValueError as exc:
            raise ConfigError(f"`{field.key}` 应为浮点数，得到 {raw!r}") from exc
    if field.py_type == "bool":
        low = text.lower()
        if low in {"true", "1", "yes", "on"}:
            return True
        if low in {"false", "0", "no", "off"}:
            return False
        raise ConfigError(f"`{field.key}` 应为 bool，得到 {raw!r}")
    # str
    return _strip_quotes(text)


def _strip_quotes(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    return text


def render_value_toml(field: WhitelistField, value: Any) -> str:
    if field.py_type in {"int", "float"}:
        return str(value)
    if field.py_type == "bool":
        return "true" if value else "false"
    # str
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def find_whitelist(key: str) -> WhitelistField | None:
    for f in WHITELIST:
        if f.key == key:
            return f
    return None


@dataclass
class ApplyResult:
    key: str
    old_value: Any
    new_value: Any
    inserted: bool


def apply_change(
    *,
    config_path: Path,
    key: str,
    raw_value: str,
) -> ApplyResult:
    """白名单内的字段做行级替换；非白名单或非法值抛 ConfigError。"""
    return apply_changes(
        config_path=config_path,
        changes={key: raw_value},
    )[0]


def apply_changes(
    *,
    config_path: Path,
    changes: dict[str, str],
) -> list[ApplyResult]:
    """批量校验后一次性写回；任一字段失败则不修改文件。"""
    if not changes:
        raise ConfigError("没有可写回的配置项")
    logger.debug("准备批量写回配置 changes=%s", _mask_mapping(changes))
    cfg = load_config(config_path)
    validated: list[tuple[WhitelistField, Any, Any]] = []
    for key, raw_value in changes.items():
        field, value = _validate_change(
            key=key,
            raw_value=raw_value,
        )
        old_value = get_dotted(cfg, key, None)
        logger.debug(
            "配置字段校验通过 key=%s raw=%r old=%r new=%r",
            key,
            _mask_value(key, raw_value),
            _mask_value(key, old_value),
            _mask_value(key, value),
        )
        validated.append((field, value, old_value))

    text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    results: list[ApplyResult] = []
    new_text = text
    for field, value, old_value in validated:
        new_text, inserted = _replace_or_insert_line(new_text, field, value)
        results.append(
            ApplyResult(
                key=field.key,
                old_value=old_value,
                new_value=value,
                inserted=inserted,
            )
        )

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(new_text, encoding="utf-8")
    logger.info("config.toml 批量更新字段：%s", ", ".join(changes))
    return results


def _mask_value(key: str, value: Any) -> Any:
    if key not in SENSITIVE_KEYS:
        return value
    return "***" if str(value or "") else ""


def _mask_mapping(values: dict[str, Any]) -> dict[str, Any]:
    return {key: _mask_value(key, value) for key, value in values.items()}


def _validate_change(
    *,
    key: str,
    raw_value: str,
) -> tuple[WhitelistField, Any]:
    field = find_whitelist(key)
    if field is None:
        raise ConfigError(
            f"`{key}` 不在白名单中。可改字段：\n"
            + "\n".join(f"  - {f.key}（{f.py_type}）：{f.desc}" for f in WHITELIST)
        )
    value = coerce_value(field, raw_value)
    return field, value


def _replace_or_insert_line(
    text: str,
    field: WhitelistField,
    value: Any,
) -> tuple[str, bool]:
    """对 dotted key 做行级替换。

    - 顶层（无点）：在没有任何 [section] 之前的区段内查找/写入。
    - 嵌套：找到对应 [section] 的下一个 section 之间，查找最后一段 key。
    """
    rendered = render_value_toml(field, value)
    parts = field.key.split(".")
    if len(parts) == 1:
        return _replace_or_insert_top_level(text, parts[0], rendered)
    section = ".".join(parts[:-1])
    leaf = parts[-1]
    return _replace_or_insert_in_section(text, section, leaf, rendered)


def _replace_or_insert_top_level(
    text: str, key: str, rendered: str
) -> tuple[str, bool]:
    new_line = f"{key} = {rendered}"
    lines = text.splitlines()
    in_top = True
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_top = False
            insert_at = idx
            break
        if in_top and re.match(rf"^{re.escape(key)}\s*=", stripped):
            indent = line[: len(line) - len(line.lstrip())]
            lines[idx] = f"{indent}{new_line}"
            return "\n".join(lines) + ("\n" if text.endswith("\n") else ""), False
        insert_at = idx + 1
    if in_top:
        # 整个文件都属于顶层
        if text and not text.endswith("\n"):
            text += "\n"
        return text + new_line + "\n", True
    # 在第一个 section 之前插入
    lines.insert(insert_at, new_line)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else ""), True


def _replace_or_insert_in_section(
    text: str, section: str, leaf: str, rendered: str
) -> tuple[str, bool]:
    new_line = f"{leaf} = {rendered}"
    lines = text.splitlines()
    section_header_re = re.compile(rf"^\s*\[\s*{re.escape(section)}\s*\]\s*$")
    any_header_re = re.compile(r"^\s*\[[^\]]+\]\s*$")
    leaf_re = re.compile(rf"^\s*{re.escape(leaf)}\s*=")

    section_start = -1
    for idx, line in enumerate(lines):
        if section_header_re.match(line):
            section_start = idx
            break
    if section_start == -1:
        # section 缺失：append 一段
        if lines and lines[-1].strip() != "":
            lines.append("")
        lines.append(f"[{section}]")
        lines.append(new_line)
        out = "\n".join(lines) + "\n"
        return out, True

    # section 范围
    end = len(lines)
    for idx in range(section_start + 1, len(lines)):
        if any_header_re.match(lines[idx]):
            end = idx
            break

    for idx in range(section_start + 1, end):
        if leaf_re.match(lines[idx]):
            indent = lines[idx][: len(lines[idx]) - len(lines[idx].lstrip())]
            lines[idx] = f"{indent}{new_line}"
            return "\n".join(lines) + ("\n" if text.endswith("\n") else ""), False

    # leaf 不存在：插在 section 末尾（end 之前）
    insert_at = end
    while insert_at > section_start + 1 and lines[insert_at - 1].strip() == "":
        insert_at -= 1
    lines.insert(insert_at, new_line)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else ""), True


def render_show(cfg: dict) -> str:
    """`/config` 不带参数时的展示文本。"""
    rows: list[str] = []
    for f in WHITELIST:
        if f.key in SENSITIVE_KEYS:
            continue
        cur = get_dotted(cfg, f.key, "(未配置)")
        rows.append(f"- {f.key} = {cur}  // {f.desc}")
    rows.append("")
    rows.append("用法：发送 /config 打开交互卡片后统一修改。")
    return "\n".join(rows)
