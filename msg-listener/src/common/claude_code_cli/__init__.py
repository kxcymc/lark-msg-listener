"""Claude Code CLI 公共配置与 argv 构造工具。"""
from __future__ import annotations

import asyncio
import ast
import csv
import json
import os
import platform
import re
import shlex
import shutil
from pathlib import Path
from typing import Iterable

from ..ccr_gateway import CcrGatewayConfig


CLAUDE_CODE_CLI_DEFAULT_COMMAND = "claude"
CLAUDE_CODE_CLI_DEFAULT_OUTPUT_FORMAT = "json"
CLAUDE_CODE_CLI_ANALYSIS_TASK_STATE_DIR = "out/claude_code_cli_analysis_tasks"
CLAUDE_CODE_CLI_DEV_TASK_STATE_DIR = "out/claude_code_cli_dev_tasks"
CLAUDE_CODE_CLI_PERMISSION_MODE = "bypassPermissions"
_ACP_MODEL_DISCOVERY_TIMEOUT_SECONDS = 8.0

# 分析器必须保持只读：即使配置中只填写额外禁用工具，也会固定追加这些写入类工具。
CLAUDE_CODE_CLI_READONLY_DISALLOWED_TOOLS = (
    "Edit",
    "Write",
    "MultiEdit",
    "Bash",
)

# 执行器允许改代码，但仍固定拒绝高风险 Bash 指令：进程终止、强制递归删除、提权，
# 以及常见 Windows 等价命令。业务侧配置只能追加，不能移除这组内置保护。
CLAUDE_CODE_CLI_DEV_TASK_BUILTIN_DISALLOWED_TOOLS = (
    "Bash(kill *)",
    "Bash(pkill *)",
    "Bash(killall *)",
    "Bash(shutdown *)",
    "Bash(reboot *)",
    "Bash(halt *)",
    "Bash(sudo *)",
    "Bash(doas *)",
    "Bash(su *)",
    "Bash(rm -rf *)",
    "Bash(rm -fr *)",
    "Bash(rm -r -f *)",
    "Bash(rm -f -r *)",
    "Bash(taskkill *)",
    "Bash(Stop-Process *)",
    "Bash(stop-process *)",
    "Bash(Stop-Computer *)",
    "Bash(stop-computer *)",
    "Bash(Restart-Computer *)",
    "Bash(restart-computer *)",
    "Bash(Remove-Item -Recurse -Force *)",
    "Bash(Remove-Item -Force -Recurse *)",
    "Bash(remove-item -recurse -force *)",
    "Bash(remove-item -force -recurse *)",
    "Bash(del /f /s /q *)",
    "Bash(erase /f /s /q *)",
    "Bash(rd /s /q *)",
    "Bash(rmdir /s /q *)",
    "Bash(runas *)",
    "Bash(Start-Process * -Verb RunAs*)",
    "Bash(start-process * -verb runas*)",
)


def coerce_tool_list(
    value: object,
    default: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """把 TOML list 或配置卡片文本归一化为去重后的工具列表。"""
    if value in (None, ""):
        return tuple(default or ())
    if isinstance(value, str):
        parsed = _parse_tool_text(value)
    elif isinstance(value, (list, tuple, set)):
        parsed = [str(item).strip() for item in value]
    else:
        parsed = []
    return merge_disallowed_tools(parsed or tuple(default or ()))


def merge_disallowed_tools(*groups: Iterable[str]) -> tuple[str, ...]:
    """合并工具列表，并保留首次出现顺序。"""
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            tool = str(item).strip()
            if not tool or tool in seen:
                continue
            seen.add(tool)
            merged.append(tool)
    return tuple(merged)


def build_claude_print_argv(
    *,
    model: str = "",
    output_format: str = "",
    prompt: str | None = None,
    permission_mode: str = "",
    disallowed_tools: Iterable[str] = (),
) -> list[str]:
    """按统一的输出格式、模型和工具规则构造 `claude -p` argv。

    命令固定为 `claude`；model/output_format/disallowed_tools
    由各调用方（分析器 / 执行器）从自己的配置段读取后显式传入。
    """
    argv = [CLAUDE_CODE_CLI_DEFAULT_COMMAND, "-p"]
    if permission_mode:
        argv += ["--permission-mode", permission_mode]
    if model.strip():
        argv += ["--model", model.strip()]
    if output_format.strip():
        argv += ["--output-format", output_format.strip()]
    for tool in merge_disallowed_tools(disallowed_tools):
        argv += ["--disallowedTools", tool]
    if prompt is not None:
        argv.append(prompt)
    return argv


def resolve_dev_task_state_dir(project_root: Path) -> Path:
    """返回 Claude 开发任务 worker 的固定本地状态目录。"""
    return (project_root / CLAUDE_CODE_CLI_DEV_TASK_STATE_DIR).resolve()


def resolve_analysis_task_state_dir(project_root: Path) -> Path:
    """返回 Claude 分析任务的固定本地状态目录。"""
    return (project_root / CLAUDE_CODE_CLI_ANALYSIS_TASK_STATE_DIR).resolve()


async def discover_claude_model_options(
    *,
    ccr_config: CcrGatewayConfig,
    command: str = CLAUDE_CODE_CLI_DEFAULT_COMMAND,
    cwd: Path | None = None,
) -> tuple[str, ...]:
    """按当前路由模式发现 Claude Code CLI 可用模型。

    CCR 模式直接读 claude-code-router 配置，避免调用网关接口时依赖服务已启动；
    直连模式通过 ACP session/new 返回的 model configOptions 发现候选项。
    """
    if ccr_config.enabled:
        return discover_ccr_model_options()
    return await discover_acp_model_options(command=command, cwd=cwd)


def discover_ccr_model_options() -> tuple[str, ...]:
    """从 claude-code-router 配置文件提取模型名和 provider,model 完整路由名。"""
    for config_path in iter_ccr_config_paths():
        if not config_path.is_file():
            continue
        config = _read_json5_object(config_path)
        if not config:
            continue
        models = _extract_ccr_model_options(config)
        if models:
            return models
    return ()


def iter_ccr_config_paths() -> tuple[Path, ...]:
    """返回三端常见的 claude-code-router 配置文件候选路径。"""
    env_names = (
        "CLAUDE_CODE_ROUTER_CONFIG",
        "CLAUDE_CODE_ROUTER_CONFIG_FILE",
        "CCR_CONFIG",
        "CCR_CONFIG_FILE",
    )
    candidates: list[Path] = []
    for name in env_names:
        raw = os.environ.get(name)
        if raw:
            candidates.append(Path(raw).expanduser())

    home = Path.home()
    system = platform.system().lower()
    if system == "windows":
        appdata = os.environ.get("APPDATA")
        local_appdata = os.environ.get("LOCALAPPDATA")
        if appdata:
            candidates.append(Path(appdata) / "claude-code-router" / "config.json")
        if local_appdata:
            candidates.append(Path(local_appdata) / "claude-code-router" / "config.json")
        candidates.append(home / ".claude-code-router" / "config.json")
    elif system == "darwin":
        candidates.append(
            home / "Library" / "Application Support" / "claude-code-router" / "config.json"
        )
        candidates.append(home / ".claude-code-router" / "config.json")
    else:
        xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config_home:
            candidates.append(Path(xdg_config_home) / "claude-code-router" / "config.json")
        candidates.append(home / ".config" / "claude-code-router" / "config.json")
        candidates.append(home / ".claude-code-router" / "config.json")

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path.expanduser())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path.expanduser())
    return tuple(deduped)


async def discover_acp_model_options(
    *,
    command: str = CLAUDE_CODE_CLI_DEFAULT_COMMAND,
    cwd: Path | None = None,
) -> tuple[str, ...]:
    """通过 ACP session/new 的 configOptions 获取直连模式模型列表。"""
    for argv in _build_acp_command_candidates(command):
        if shutil.which(argv[0]) is None:
            continue
        try:
            models = await _try_discover_acp_models(argv=argv, cwd=cwd)
        except (OSError, asyncio.TimeoutError, ValueError):
            continue
        if models:
            return models
    return ()


def _parse_tool_text(value: str) -> list[str]:
    text = value.strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            parsed = None
        if isinstance(parsed, (list, tuple, set)):
            return [str(item).strip() for item in parsed]
    try:
        return [item.strip() for item in next(csv.reader([text]))]
    except csv.Error:
        return [item.strip() for item in text.split(",")]


def _extract_ccr_model_options(config: dict) -> tuple[str, ...]:
    providers = config.get("Providers") or config.get("providers") or []
    models: list[str] = []
    if isinstance(providers, list):
        for provider in providers:
            if not isinstance(provider, dict):
                continue
            provider_name = str(provider.get("name") or "").strip()
            raw_models = provider.get("models") or []
            if not isinstance(raw_models, list):
                continue
            for raw_model in raw_models:
                model = str(raw_model or "").strip()
                if not model:
                    continue
                models.append(model)
                if provider_name:
                    models.append(f"{provider_name},{model}")
    router = config.get("Router") or config.get("router") or {}
    if isinstance(router, dict):
        for value in router.values():
            if isinstance(value, str):
                model = value.strip()
                if model:
                    models.append(model)
    return merge_disallowed_tools(models)


def _read_json5_object(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        try:
            parsed = json.loads(_json5_to_json(text))
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _json5_to_json(text: str) -> str:
    without_comments = _strip_json5_comments(text)
    without_trailing_commas = re.sub(r",(\s*[}\]])", r"\1", without_comments)
    return re.sub(
        r'([{\[,]\s*)([A-Za-z_$][\w$-]*)\s*:',
        r'\1"\2":',
        without_trailing_commas,
    )


def _strip_json5_comments(text: str) -> str:
    result: list[str] = []
    i = 0
    in_string = False
    string_quote = ""
    escaped = False
    while i < len(text):
        char = text[i]
        next_char = text[i + 1] if i + 1 < len(text) else ""
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == string_quote:
                in_string = False
            i += 1
            continue
        if char in {'"', "'"}:
            in_string = True
            string_quote = char
            result.append(char)
            i += 1
            continue
        if char == "/" and next_char == "/":
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            continue
        if char == "/" and next_char == "*":
            i += 2
            while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        result.append(char)
        i += 1
    return "".join(result)


def _build_acp_command_candidates(command: str) -> tuple[tuple[str, ...], ...]:
    candidates: list[tuple[str, ...]] = []
    env_command = os.environ.get("CLAUDE_CODE_ACP_COMMAND", "").strip()
    if env_command:
        candidates.append(tuple(shlex.split(env_command)))
    candidates.extend(
        (
            ("claude-code-acp",),
            ("cc-acp",),
            (command, "--acp"),
            (command, "acp"),
        )
    )
    deduped: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()
    for argv in candidates:
        if not argv or argv in seen:
            continue
        seen.add(argv)
        deduped.append(argv)
    return tuple(deduped)


async def _try_discover_acp_models(
    *,
    argv: tuple[str, ...],
    cwd: Path | None,
) -> tuple[str, ...]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=str(cwd) if cwd else None,
    )
    try:
        assert proc.stdin is not None
        assert proc.stdout is not None
        await _write_json_line(proc.stdin, _acp_initialize_request())
        initialize_response = await asyncio.wait_for(
            _read_json_response(proc.stdout, request_id=1),
            timeout=_ACP_MODEL_DISCOVERY_TIMEOUT_SECONDS,
        )
        if "error" in initialize_response:
            raise ValueError("ACP initialize failed")
        await _write_json_line(proc.stdin, _acp_session_new_request(cwd))
        session_response = await asyncio.wait_for(
            _read_json_response(proc.stdout, request_id=2),
            timeout=_ACP_MODEL_DISCOVERY_TIMEOUT_SECONDS,
        )
        if "error" in session_response:
            raise ValueError("ACP session/new failed")
        models = _extract_acp_models(session_response.get("result"))
        session_id = _extract_session_id(session_response.get("result"))
        if session_id:
            await _write_json_line(proc.stdin, _acp_session_close_request(session_id))
        return models
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()


async def _write_json_line(writer: asyncio.StreamWriter, payload: dict) -> None:
    writer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
    await writer.drain()


async def _read_json_response(
    reader: asyncio.StreamReader,
    *,
    request_id: int,
) -> dict:
    while True:
        line = await reader.readline()
        if not line:
            raise ValueError("ACP process closed stdout")
        try:
            payload = json.loads(line.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("id") == request_id:
            return payload


def _acp_initialize_request() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": False,
            },
            "clientInfo": {
                "name": "msg-listener",
                "title": "msg-listener",
                "version": "0.1.0",
            },
        },
    }


def _acp_session_new_request(cwd: Path | None) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "session/new",
        "params": {
            "cwd": str(cwd.resolve()) if cwd else str(Path.cwd().resolve()),
            "mcpServers": [],
        },
    }


def _acp_session_close_request(session_id: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "session/close",
        "params": {"sessionId": session_id},
    }


def _extract_acp_models(result: object) -> tuple[str, ...]:
    if not isinstance(result, dict):
        return ()
    options = result.get("configOptions")
    if not isinstance(options, list):
        return ()
    models: list[str] = []
    for option in options:
        if not isinstance(option, dict):
            continue
        category = str(option.get("category") or "").lower()
        option_id = str(option.get("id") or "").lower()
        option_name = str(option.get("name") or "").lower()
        if category != "model" and option_id != "model" and "model" not in option_name:
            continue
        raw_values = option.get("options") or []
        if not isinstance(raw_values, list):
            continue
        for raw_value in raw_values:
            if isinstance(raw_value, dict):
                value = str(raw_value.get("value") or raw_value.get("id") or "").strip()
            else:
                value = str(raw_value or "").strip()
            if value:
                models.append(value)
    return merge_disallowed_tools(models)


def _extract_session_id(result: object) -> str:
    if not isinstance(result, dict):
        return ""
    return str(result.get("sessionId") or "")
