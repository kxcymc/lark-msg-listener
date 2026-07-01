#!/usr/bin/env python3
"""统一启动前置入口。

`--mode bootstrap` 会补齐 lark-cli、应用配置、用户授权与 Python 依赖；
`--mode run` 只做快速校验。两种模式都会先启动 `python -m src.main`，
并在 bot 真正就绪后按配置拉起 dashboard_api。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.common.process_lifecycle import (  # noqa: E402
    DEFAULT_SIGNAL_SET,
    install_signal_handlers,
    log_process_started,
    parent_process_changed,
)
from src.common.logging_setup import (  # noqa: E402
    configure_logging,
    install_system_log_tee,
)
from src.common.lark_app_config import (  # noqa: E402
    LarkConfigError,
    PROJECT_LARK_CLI_CONFIG_PATH,
    lark_cli_subprocess_env,
    project_lark_cli_config_exists,
    snapshot_current_lark_cli_config_to_project,
)
from src.dashboard_api.config import DashboardConfig  # noqa: E402
from src.common.tls import ensure_system_tls_trust  # noqa: E402

PYTHON_VERSION = "3.11"
CONFIG_PATH = PROJECT_ROOT / "config.toml"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.toml.default"
UV_LOCK_PATH = PROJECT_ROOT / "uv.lock"
VENV_PATH = PROJECT_ROOT / ".venv"
DEPS_STAMP_PATH = PROJECT_ROOT / ".venv" / ".uv-lock.stamp"
UV_INSTALL_DIR = PROJECT_ROOT / ".local" / "uv"
BOT_READY_STAMP_PATH = PROJECT_ROOT / "cache" / "bot.ready"

REQUIRED_BOT_SCOPES = [
    "im:message.p2p_msg:readonly",
    "im:message:send_as_bot",
    "im:resource",
]
REQUIRED_BOT_EVENTS = [
    "im.message.receive_v1",
]
REQUIRED_CARD_CALLBACK_EVENT = "card.action.trigger"
REQUIRED_BOT_MENU_EVENT = "application.bot.menu_v6"
BOT_MENU_EVENT_KEY_MAP = [
    ("配置与帮助 / 修改配置", "open_config"),
    ("配置与帮助 / 查看帮助", "show_help"),
    ("创建开发任务", "open_devtask"),
    ("meego开发接力 / 通过链接接力", "open_meego_url"),
    ("meego开发接力 / 通过群聊名接力", "open_meego_chat"),
]
APP_META_SCOPES = [
    "application:application:self_manage",
    "application:application.app_version:readonly",
]
REQUIRED_LARK_CLI_VERSION = "1.0.44"
REQUIRED_MEEGLE_PACKAGE = "@lark-project/meegle"
BOOTSTRAP_HINT = "npm run bootstrap"
RUN_HINT = "npm run start"
CLI_FLOW_RETRY_INTERVAL_SECONDS = 5.0
PERMISSION_RETRY_INTERVAL_SECONDS = 120.0
logger = logging.getLogger("msg-listener-setup")


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print(f"[setup] $ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, **kwargs)


def _which(name: str) -> str | None:
    return shutil.which(name)


def _hint_bootstrap_and_exit(reason: str) -> None:
    print(
        f"[setup] {reason}\n"
        f"[setup] 请先执行：{BOOTSTRAP_HINT} 完成初始化",
        file=sys.stderr,
    )
    sys.exit(1)


def ensure_config_file(mode: str) -> None:
    if CONFIG_PATH.exists():
        print(f"[{mode}] 检查配置文件：已存在 {CONFIG_PATH.name}", flush=True)
        return
    if not DEFAULT_CONFIG_PATH.exists():
        print(
            f"[{mode}] 缺少配置文件 {CONFIG_PATH.name}，且未找到默认模板 {DEFAULT_CONFIG_PATH.name}。",
            file=sys.stderr,
        )
        sys.exit(1)
    shutil.copyfile(DEFAULT_CONFIG_PATH, CONFIG_PATH)
    print(
        f"[{mode}] 检查配置文件：未找到 {CONFIG_PATH.name}，已从 {DEFAULT_CONFIG_PATH.name} 复制生成",
        flush=True,
    )


def ensure_kxcymc_openapi_pat_configured(mode: str) -> None:
    try:
        toml_reader = importlib.import_module("tomllib")
    except ModuleNotFoundError:
        return

    try:
        with CONFIG_PATH.open("rb") as f:
            cfg = toml_reader.load(f)
    except FileNotFoundError:
        return
    except getattr(toml_reader, "TOMLDecodeError", ValueError) as exc:
        print(f"[{mode}] config.toml 解析失败：{exc}", file=sys.stderr)
        sys.exit(1)

    card_cfg = ((cfg.get("phase2") or {}).get("card") or {})
    dev_task_cfg = ((cfg.get("phase3") or {}).get("dev_task") or {})
    executor = str(dev_task_cfg.get("executor", "")).strip()
    if executor != "kxcymc_openapi":
        if not str(card_cfg.get("meego_requirement_host", "")).strip():
            print(
                f"[{mode}] 您还未填写 meego_requirement_host。"
                f"请更新 {CONFIG_PATH} 后，重新启动应用。",
                file=sys.stderr,
                flush=True,
            )
            sys.exit(1)
        return

    kxcymc_cfg = dev_task_cfg.get("kxcymc_openapi") or {}
    pat_token = str(kxcymc_cfg.get("pat_token", "")).strip()
    meego_host = str(card_cfg.get("meego_requirement_host", "")).strip()
    if pat_token and meego_host:
        return

    if not meego_host:
        print(
            f"[{mode}] 您还未填写 meego_requirement_host。"
            f"请更新 {CONFIG_PATH} 后，重新启动应用。",
            file=sys.stderr,
            flush=True,
        )
    if not pat_token:
        print(
            f"[{mode}] 当前开发任务执行器为 kxcymc_openapi，但 pat_token 为空。\n"
            f"[{mode}] 请打开 https://example.com/tokens，创建个人访问令牌，"
            f"手动写入 {CONFIG_PATH} 后，重新启动应用。",
            file=sys.stderr,
            flush=True,
        )
    sys.exit(1)


def _open_console_host(brand: str) -> str:
    return "https://open.larksuite.com" if brand == "lark" else "https://open.feishu.cn"


def _console_scope_grant_url(brand: str, app_id: str, scopes: list[str]) -> str:
    return (
        f"{_open_console_host(brand)}/app/{app_id}/auth"
        f"?q={','.join(scopes)}&op_from=openapi&token_type=tenant"
    )


def _console_event_url(brand: str, app_id: str) -> str:
    return f"{_open_console_host(brand)}/app/{app_id}/event"


def _console_callback_url(app_id: str) -> str:
    return f"https://open.larkoffice.com/app/{app_id}/event?tab=callback"


def _console_bot_menu_event_url(app_id: str) -> str:
    return f"https://open.larkoffice.com/app/{app_id}/event?tab=event"

def _console_bot_menu_config_url(app_id: str) -> str:
    return f"https://open.larkoffice.com/app/{app_id}/bot"

def _load_current_app(*, bootstrap: bool, project_local: bool = False) -> tuple[str, str]:
    res = subprocess.run(
        ["lark-cli", "config", "show"],
        capture_output=True,
        text=True,
        timeout=15,
        env=lark_cli_subprocess_env() if project_local else None,
    )
    if res.returncode != 0:
        message = (
            "`lark-cli config show` 返回非零，疑似未完成应用配置。\n"
            f"  stderr: {(res.stderr or '').strip()}"
        )
        if bootstrap:
            print(message, file=sys.stderr)
            sys.exit(1)
        _hint_bootstrap_and_exit(message)
    try:
        data = json.loads(res.stdout or "{}")
    except json.JSONDecodeError:
        message = "`lark-cli config show` 输出不是合法 JSON。"
        if bootstrap:
            print(message, file=sys.stderr)
            sys.exit(1)
        _hint_bootstrap_and_exit(message)
    app = data if isinstance(data, dict) else {}
    apps = app.get("apps") or app.get("Apps") or []
    if isinstance(apps, list) and apps:
        app = next((item for item in apps if isinstance(item, dict)), app)
    app_id = str(app.get("appId") or app.get("AppId") or app.get("app_id") or "")
    brand = str(app.get("brand") or app.get("Brand") or "feishu").lower()
    if not app_id:
        message = "未检测到飞书应用配置。"
        if bootstrap:
            print(message, file=sys.stderr)
            sys.exit(1)
        _hint_bootstrap_and_exit(message)
    return app_id, brand


def _extract_data_object(raw: object) -> dict:
    if not isinstance(raw, dict):
        return {}
    data = raw.get("data")
    if isinstance(data, dict):
        nested = data.get("data")
        if isinstance(nested, dict):
            return nested
        return data
    return raw


def _fetch_app_version(app_id: str, *, project_local: bool = False) -> dict | None:
    res = subprocess.run(
        [
            "lark-cli",
            "api",
            "GET",
            f"/open-apis/application/v6/applications/{app_id}/app_versions",
            "--params",
            '{"lang":"zh_cn","page_size":2}',
            "--as",
            "bot",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=lark_cli_subprocess_env() if project_local else None,
    )
    if res.returncode != 0:
        return None
    try:
        payload = json.loads(res.stdout or "{}")
    except json.JSONDecodeError:
        return None
    data = _extract_data_object(payload)
    items = data.get("items") if isinstance(data, dict) else []
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("status") != 1 or item.get("publish_time") in (None, ""):
            continue
        return item
    return None


def _tenant_scopes(app_version: dict) -> set[str]:
    out: set[str] = set()
    scopes = app_version.get("scopes") or []
    if not isinstance(scopes, list):
        return out
    for item in scopes:
        if not isinstance(item, dict):
            continue
        token_types = item.get("token_types") or item.get("tokenTypes") or []
        if "tenant" in token_types and item.get("scope"):
            out.add(str(item["scope"]))
    return out


def _event_types(app_version: dict) -> set[str]:
    out: set[str] = set()
    event_infos = app_version.get("event_infos") or app_version.get("eventInfos") or []
    if not isinstance(event_infos, list):
        return out
    for item in event_infos:
        if isinstance(item, dict) and item.get("event_type"):
            out.add(str(item["event_type"]))
    return out


def _report_permission_wait(
    *,
    mode: str,
    app_id: str,
    brand: str,
    missing_scopes: list[str] | None = None,
    missing_events: list[str] | None = None,
    no_version: bool = False,
) -> None:
    prefix = f"[{mode}]"
    if no_version:
        print(
            f"{prefix} 无法读取当前已发布应用版本，可能尚未开通应用版本查询权限或版本仍在发布中。\n"
            f"{prefix} 应用版本查询权限申请链接：{_console_scope_grant_url(brand, app_id, APP_META_SCOPES)}\n"
            f"{prefix} 应用版本页面：{_open_console_host(brand)}/app/{app_id}/version",
            file=sys.stderr,
        )
        return
    if missing_scopes:
        print(
            f"{prefix} 缺少 bot 应用权限：{' '.join(missing_scopes)}\n"
            f"{prefix} 权限申请链接：{_console_scope_grant_url(brand, app_id, missing_scopes)}",
            file=sys.stderr,
        )
    if missing_events:
        print(
            f"{prefix} 缺少事件订阅：{' '.join(missing_events)}\n"
            f"{prefix} 事件订阅页面：{_console_event_url(brand, app_id)}",
            file=sys.stderr,
        )
    print(f"{prefix} 完成 lark-cli/开放平台流程后脚本会持续重试检查。", file=sys.stderr)


def _wait_before_retry(prefix: str, interval: float, reason: str) -> None:
    print(
        f"[{prefix}] {reason}；{interval:g} 秒后继续重试。",
        file=sys.stderr,
        flush=True,
    )
    time.sleep(interval)


def ensure_bot_app_permissions(mode: str) -> None:
    project_local = mode != "bootstrap"
    app_id, brand = _load_current_app(
        bootstrap=mode == "bootstrap",
        project_local=project_local,
    )
    while True:
        app_version = _fetch_app_version(app_id, project_local=project_local)
        if app_version is None:
            _report_permission_wait(mode=mode, app_id=app_id, brand=brand, no_version=True)
            _wait_before_retry(mode, PERMISSION_RETRY_INTERVAL_SECONDS, "bot 应用版本或权限暂未就绪")
            continue

        granted_scopes = _tenant_scopes(app_version)
        missing_scopes = [s for s in REQUIRED_BOT_SCOPES if s not in granted_scopes]
        subscribed_events = _event_types(app_version)
        missing_events = [e for e in REQUIRED_BOT_EVENTS if e not in subscribed_events]
        if not missing_scopes and not missing_events:
            print(f"[{mode}] 检查 bot 应用权限和事件订阅：已通过", flush=True)
            return
        _report_permission_wait(
            mode=mode,
            app_id=app_id,
            brand=brand,
            missing_scopes=missing_scopes,
            missing_events=missing_events,
        )
        _wait_before_retry(mode, PERMISSION_RETRY_INTERVAL_SECONDS, "bot 应用权限或事件订阅暂未就绪")


def ensure_card_callback_event(mode: str) -> None:
    """卡片回传交互回调（card.action.trigger）人工确认原子。

    背景：飞书后台「回调」tab 的订阅状态在 OpenAPI、lark-cli、oapi-sdk 三层均
    无可靠查询通道；唯一可行方案是输出引导链接后由用户在终端回车确认，并把
    确认状态写回 config.toml，避免下次启动重复打扰。
    """
    app_id, _ = _load_current_app(
        bootstrap=mode == "bootstrap",
        project_local=mode != "bootstrap",
    )

    if _read_card_callback_confirmed():
        print(f"[{mode}] 检查卡片回调事件：已人工确认，跳过", flush=True)
        return

    callback_url = _console_callback_url(app_id)
    print(
        f"[{mode}] 卡片回传交互回调（{REQUIRED_CARD_CALLBACK_EVENT}）需要人工在飞书后台配置。\n"
        f"[{mode}] 飞书 OpenAPI/lark-cli/SDK 均无法查询其订阅状态，因此本步骤改为人工确认。\n"
        f"[{mode}] 请打开以下链接，在「回调配置」tab ，点击“添加订阅”按钮，订阅 {REQUIRED_CARD_CALLBACK_EVENT} 后回到此处：\n"
        f"[{mode}] {callback_url}",
        file=sys.stderr,
        flush=True,
    )
    while True:
        try:
            answer = input(
                f"[{mode}] 已在飞书后台订阅 {REQUIRED_CARD_CALLBACK_EVENT} 后输入 yes 继续 "
                f"(回车视为未完成，将重新提示)："
            ).strip().lower()
        except KeyboardInterrupt:
            print(f"\n[{mode}] 已通过 ctrl+C 退出项目", file=sys.stderr, flush=True)
            os._exit(130)
        except EOFError:
            print(
                f"[{mode}] 当前进程没有可用的标准输入，无法人工确认；请通过 `npm run bootstrap` 启动。",
                file=sys.stderr,
            )
            sys.exit(1)
        if answer in ("y", "yes"):
            _write_card_callback_confirmed(True)
            print(f"[{mode}] 检查卡片回调事件：人工确认通过，已写回 config.toml", flush=True)
            return
        print(
            f"[{mode}] 未确认完成，请在飞书后台订阅 {REQUIRED_CARD_CALLBACK_EVENT} 后再输入 yes。",
            file=sys.stderr,
            flush=True,
        )


def _read_card_callback_confirmed() -> bool:
    try:
        toml_reader = importlib.import_module("tomllib")
    except ModuleNotFoundError:
        return False
    try:
        with CONFIG_PATH.open("rb") as f:
            cfg = toml_reader.load(f)
    except FileNotFoundError:
        return False
    except getattr(toml_reader, "TOMLDecodeError", ValueError):
        return False
    section = cfg.get("lark_app") if isinstance(cfg, dict) else None
    if not isinstance(section, dict):
        return False
    return bool(section.get("card_callback_event_confirmed") is True)


def _write_card_callback_confirmed(value: bool) -> None:
    """把 [lark_app].card_callback_event_confirmed 写回 config.toml。

    避免引入 tomli_w/tomlkit 依赖，采用最小化文本替换：
      - 若已存在该 key（任意 section 顶层等价），就地替换其布尔字面量；
      - 否则在文件末尾追加 [lark_app] 段（理论上初始 config 已含，做兜底）。
    """
    if not CONFIG_PATH.exists():
        return
    text = CONFIG_PATH.read_text(encoding="utf-8")
    literal = "true" if value else "false"
    # Match `card_callback_event_confirmed = false|true` with optional spaces.
    pattern = re.compile(
        r"^(\s*card_callback_event_confirmed\s*=\s*)(true|false)\s*$",
        re.MULTILINE,
    )
    new_text, replaced = pattern.subn(lambda m: f"{m.group(1)}{literal}", text, count=1)
    if replaced == 0:
        if not new_text.endswith("\n"):
            new_text += "\n"
        new_text += (
            "\n[lark_app]\n"
            f"card_callback_event_confirmed = {literal}\n"
        )
    CONFIG_PATH.write_text(new_text, encoding="utf-8")


def ensure_bot_menu_config(mode: str) -> None:
    """机器人自定义菜单配置人工确认原子。

    背景：飞书后台「机器人自定义菜单」编辑区的菜单项与事件 ID 映射无可靠查询通道，
    因此改为输出引导链接与菜单项映射后由用户在终端确认，并把确认状态写回 config.toml。
    """
    app_id, _ = _load_current_app(
        bootstrap=mode == "bootstrap",
        project_local=mode != "bootstrap",
    )

    if _read_bot_menu_config_confirmed():
        print(f"[{mode}] 检查机器人菜单配置：已人工确认，跳过", flush=True)
        return

    menu_url = _console_bot_menu_config_url(app_id)
    mapping_lines = "\n".join(
        f"[{mode}]   {label} -> {event_key}" for label, event_key in BOT_MENU_EVENT_KEY_MAP
    )
    print(
        f"[{mode}] 机器人自定义菜单需要人工在飞书后台配置。\n"
        f"[{mode}] 请打开以下链接，进入「机器人自定义菜单」编辑区，将菜单状态选择「开启」，"
        f"菜单的「响应动作」选择「推送事件」，并按下表填写事件 ID：\n"
        f"[{mode}] {menu_url}\n"
        f"{mapping_lines}",
        file=sys.stderr,
        flush=True,
    )
    while True:
        try:
            answer = input(
                f"[{mode}] 已在飞书后台完成机器人自定义菜单配置后输入 yes 继续 "
                f"(回车视为未完成，将重新提示)："
            ).strip().lower()
        except KeyboardInterrupt:
            print(f"\n[{mode}] 已通过 ctrl+C 退出项目", file=sys.stderr, flush=True)
            os._exit(130)
        except EOFError:
            print(
                f"[{mode}] 当前进程没有可用的标准输入，无法人工确认；请通过 `npm run bootstrap` 启动。",
                file=sys.stderr,
            )
            sys.exit(1)
        if answer in ("y", "yes"):
            _write_bot_menu_config_confirmed(True)
            print(f"[{mode}] 检查机器人菜单配置：人工确认通过，已写回 config.toml", flush=True)
            return
        print(
            f"[{mode}] 未确认完成，请在飞书后台完成机器人自定义菜单配置后再输入 yes。",
            file=sys.stderr,
            flush=True,
        )


def ensure_bot_menu_event(mode: str) -> None:
    """机器人菜单事件（application.bot.menu_v6）人工确认原子。

    背景：菜单推送事件需要在飞书后台「事件与回调 → 事件配置」手动添加，并在页面提示时
    确认开通事件权限、发布应用版本；OpenAPI/lark-cli/SDK 均无可靠查询通道，因此改为人工确认。
    """
    app_id, _ = _load_current_app(
        bootstrap=mode == "bootstrap",
        project_local=mode != "bootstrap",
    )

    if _read_bot_menu_event_confirmed():
        print(f"[{mode}] 检查机器人菜单事件：已人工确认，跳过", flush=True)
        return

    event_url = _console_bot_menu_event_url(app_id)
    print(
        f"[{mode}] 机器人菜单事件（{REQUIRED_BOT_MENU_EVENT}）需要人工在飞书后台配置。\n"
        f"[{mode}] 请打开以下链接，在「事件与回调 → 事件配置」添加事件 {REQUIRED_BOT_MENU_EVENT}，"
        f"如页面提示确认开通事件权限请点击确认，并创建/发布应用版本后回到此处：\n"
        f"[{mode}] {event_url}",
        file=sys.stderr,
        flush=True,
    )
    while True:
        try:
            answer = input(
                f"[{mode}] 已在飞书后台添加 {REQUIRED_BOT_MENU_EVENT} 并发布应用版本后输入 yes 继续 "
                f"(回车视为未完成，将重新提示)："
            ).strip().lower()
        except KeyboardInterrupt:
            print(f"\n[{mode}] 已通过 ctrl+C 退出项目", file=sys.stderr, flush=True)
            os._exit(130)
        except EOFError:
            print(
                f"[{mode}] 当前进程没有可用的标准输入，无法人工确认；请通过 `npm run bootstrap` 启动。",
                file=sys.stderr,
            )
            sys.exit(1)
        if answer in ("y", "yes"):
            _write_bot_menu_event_confirmed(True)
            print(f"[{mode}] 检查机器人菜单事件：人工确认通过，已写回 config.toml", flush=True)
            return
        print(
            f"[{mode}] 未确认完成，请在飞书后台添加 {REQUIRED_BOT_MENU_EVENT} 并发布应用版本后再输入 yes。",
            file=sys.stderr,
            flush=True,
        )


def _read_bot_menu_event_confirmed() -> bool:
    return _read_lark_app_bool("bot_menu_event_confirmed")


def _write_bot_menu_event_confirmed(value: bool) -> None:
    _write_lark_app_bool("bot_menu_event_confirmed", value)


def _read_bot_menu_config_confirmed() -> bool:
    return _read_lark_app_bool("bot_menu_config_confirmed")


def _write_bot_menu_config_confirmed(value: bool) -> None:
    _write_lark_app_bool("bot_menu_config_confirmed", value)


def _read_lark_app_bool(key: str) -> bool:
    try:
        toml_reader = importlib.import_module("tomllib")
    except ModuleNotFoundError:
        return False
    try:
        with CONFIG_PATH.open("rb") as f:
            cfg = toml_reader.load(f)
    except FileNotFoundError:
        return False
    except getattr(toml_reader, "TOMLDecodeError", ValueError):
        return False
    section = cfg.get("lark_app") if isinstance(cfg, dict) else None
    if not isinstance(section, dict):
        return False
    return bool(section.get(key) is True)


def _write_lark_app_bool(key: str, value: bool) -> None:
    if not CONFIG_PATH.exists():
        return
    text = CONFIG_PATH.read_text(encoding="utf-8")
    literal = "true" if value else "false"
    pattern = re.compile(
        rf"^(\s*{re.escape(key)}\s*=\s*)(true|false)\s*$",
        re.MULTILINE,
    )
    new_text, replaced = pattern.subn(lambda m: f"{m.group(1)}{literal}", text, count=1)
    if replaced:
        CONFIG_PATH.write_text(new_text, encoding="utf-8")
        return

    # key 不存在：优先插入到已存在的 [lark_app] 段，避免重复声明 [lark_app] 导致 TOML 解析失败。
    section_pattern = re.compile(r"^\[lark_app\]\s*$", re.MULTILINE)
    section_match = section_pattern.search(text)
    if section_match:
        insert_at = section_match.end()
        new_text = f"{text[:insert_at]}\n{key} = {literal}{text[insert_at:]}"
    else:
        new_text = text
        if not new_text.endswith("\n"):
            new_text += "\n"
        new_text += (
            "\n[lark_app]\n"
            f"{key} = {literal}\n"
        )
    CONFIG_PATH.write_text(new_text, encoding="utf-8")


def check_basic_deps() -> None:
    missing = [name for name in ("node", "npx") if not _which(name)]
    if missing:
        print(
            f"[bootstrap] 缺少必要依赖: {', '.join(missing)}。\n"
            "[bootstrap] 请先安装 Node.js（需包含 npx）后再次运行本脚本。",
            file=sys.stderr,
        )
        sys.exit(1)


def check_python_version() -> None:
    if sys.version_info >= (3, 11):
        return
    print(
        f"[setup] 当前 Python 版本是 {sys.version_info.major}.{sys.version_info.minor}，低于要求的 3.11。\n"
        f"[setup] 请改用 3.11+ 的解释器，或通过统一入口 {BOOTSTRAP_HINT} 启动。",
        file=sys.stderr,
    )
    sys.exit(1)


def _resolve_node_tool(name: str) -> list[str]:
    """Resolve a Node.js shipped tool (npm/npx) to an absolute argv prefix.

    Windows 上 `subprocess.run(["npx", ...])` 不会自动解析 `npx.cmd`，会抛
    `FileNotFoundError`。这里通过 `shutil.which` 查到带扩展名的绝对路径再返回，
    并兜底用 `<name>.cmd`。
    """
    resolved = _which(name)
    if resolved:
        return [resolved]
    if os.name == "nt":
        for candidate in (f"{name}.cmd", f"{name}.exe", f"{name}.bat"):
            located = _which(candidate)
            if located:
                return [located]
        return [f"{name}.cmd"]
    return [name]


def ensure_lark_cli() -> None:
    if _which("lark-cli"):
        print("[bootstrap] 检查 lark-cli：已安装", flush=True)
        return
    print(
        f"[bootstrap] 检查 lark-cli：未安装，开始安装锁定版本 {REQUIRED_LARK_CLI_VERSION}",
        flush=True,
    )
    pinned_pkg = f"@larksuite/cli@{REQUIRED_LARK_CLI_VERSION}"
    res = _run([*_resolve_node_tool("npx"), "-y", pinned_pkg, "install"])
    if res.returncode != 0 or not _which("lark-cli"):
        print(
            f"[bootstrap] lark-cli 安装失败，请手动执行 `npm install -g {pinned_pkg}` 后重试",
            file=sys.stderr,
        )
        sys.exit(1)


def check_lark_cli_present() -> None:
    if not _which("lark-cli"):
        _hint_bootstrap_and_exit("未检测到 lark-cli。")


def ensure_meegle_cli() -> None:
    if _which("meegle"):
        print("[bootstrap] 检查 meegle：已安装", flush=True)
        return
    print(
        f"[bootstrap] 检查 meegle：未安装，开始安装 {REQUIRED_MEEGLE_PACKAGE}",
        flush=True,
    )
    res = _run([*_resolve_node_tool("npm"), "install", "-g", REQUIRED_MEEGLE_PACKAGE])
    if res.returncode != 0 or not _which("meegle"):
        print(
            f"[bootstrap] meegle 安装失败，请手动执行 `npm install -g {REQUIRED_MEEGLE_PACKAGE}` 后重试",
            file=sys.stderr,
        )
        sys.exit(1)


def check_meegle_present() -> None:
    if not _which("meegle"):
        _hint_bootstrap_and_exit("未检测到 meegle。")


def _configured_meegle_host() -> str:
    host = os.environ.get("MEEGLE_HOST", "").strip()
    if host:
        return host
    return _persisted_meegle_host()


def _persisted_meegle_host() -> str:
    try:
        res = subprocess.run(
            ["meegle", "config", "get", "host"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""
    if res.returncode != 0:
        return ""
    return (res.stdout or "").strip()


def _config_meegle_host() -> str:
    try:
        toml_reader = importlib.import_module("tomllib")
    except ModuleNotFoundError:
        return ""

    try:
        with CONFIG_PATH.open("rb") as f:
            cfg = toml_reader.load(f)
    except FileNotFoundError:
        return ""
    except getattr(toml_reader, "TOMLDecodeError", ValueError):
        return ""

    card_cfg = ((cfg.get("phase2") or {}).get("card") or {})
    return str(card_cfg.get("meego_requirement_host", "")).strip()


def _target_meegle_host() -> str:
    # 启动阶段的登录态必须和运行期 Meego 采集使用的配置域名保持一致。
    return (
        os.environ.get("MEEGLE_HOST", "").strip()
        or _config_meegle_host()
        or _persisted_meegle_host()
    )


def ensure_meegle_host(mode: str) -> str:
    target_host = _target_meegle_host()
    if not target_host:
        print(
            f"[{mode}] meegle host 未配置，请先填写 {CONFIG_PATH} 中的 meego_requirement_host。",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)
    configured = _configured_meegle_host()
    if configured == target_host:
        print(f"[{mode}] 检查 meegle host：{configured}", flush=True)
        return configured
    if configured:
        print(
            f"[{mode}] 检查 meegle host：当前为 {configured}，开始设置为 {target_host}",
            flush=True,
        )
    else:
        print(f"[{mode}] 检查 meegle host：未配置，开始设置为 {target_host}", flush=True)
    res = _run(["meegle", "config", "set", "host", target_host])
    if res.returncode != 0:
        if mode == "bootstrap":
            print(
                f"[bootstrap] meegle host 设置失败，请手动执行 `meegle config set host {target_host}` 后重试",
                file=sys.stderr,
            )
            sys.exit(1)
        _hint_bootstrap_and_exit(f"meegle host 未配置，且自动设置为 {target_host} 失败。")
    resolved = _configured_meegle_host() or target_host
    print(f"[{mode}] 检查 meegle host：已设置为 {resolved}", flush=True)
    return resolved


def is_meegle_logged_in(host: str) -> bool:
    try:
        res = subprocess.run(
            ["meegle", "auth", "status", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=15,
            env={**os.environ, "MEEGLE_HOST": host},
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    if res.returncode != 0:
        return False
    try:
        data = json.loads(res.stdout or "{}")
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    authenticated = data.get("authenticated")
    if isinstance(authenticated, bool):
        return authenticated
    token_status = str(data.get("tokenStatus") or data.get("token_status") or "").strip().lower()
    if token_status and token_status not in {"expired", "invalid", "logged_out", "not_logged_in"}:
        return True
    return False


def ensure_meegle_login(mode: str, host: str) -> None:
    while True:
        if is_meegle_logged_in(host):
            print(f"[{mode}] 检查 meegle 登录：已完成（host={host}）", flush=True)
            return
        print(
            f"[{mode}] 检查 meegle 登录：未完成，开始 `meegle auth login --device-code --host {host}`",
            flush=True,
        )
        res = _run(["meegle", "auth", "login", "--device-code", "--host", host])
        if res.returncode == 0:
            continue
        _wait_before_retry(
            mode,
            CLI_FLOW_RETRY_INTERVAL_SECONDS,
            f"`meegle auth login --device-code --host {host}` 未完成或已超时",
        )


# 更新是必要的，如带来项目运行问题，大概率为lark-cli的新特性
def update_lark_cli_for_run_mode() -> None:
    print("[run] 检查 lark-cli 更新", flush=True)
    try:
        check_res = subprocess.run(
            ["lark-cli", "update", "--check", "--json"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        print(f"[run] 检查 lark-cli 更新失败，继续使用当前版本：{exc}", file=sys.stderr)
        return

    if check_res.returncode != 0:
        stderr = (check_res.stderr or "").strip()
        print(
            "[run] 检查 lark-cli 更新失败，继续使用当前版本。"
            + (f"\n[run] stderr: {stderr}" if stderr else ""),
            file=sys.stderr,
        )
        return

    try:
        payload = json.loads(check_res.stdout or "{}")
    except json.JSONDecodeError:
        stdout = (check_res.stdout or "").strip()
        print(
            "[run] 无法解析 lark-cli 更新检查结果，继续使用当前版本。"
            + (f"\n[run] stdout: {stdout}" if stdout else ""),
            file=sys.stderr,
        )
        return

    action = str(payload.get("action") or "").strip()
    message = str(payload.get("message") or "").strip()
    if action != "update_available":
        if message:
            print(f"[run] {message}", flush=True)
        else:
            print("[run] lark-cli 已是最新版本", flush=True)
        return

    if payload.get("auto_update") is not True:
        url = str(payload.get("url") or "").strip()
        print(
            "[run] 检测到 lark-cli 有新版本，但当前安装方式不支持自动更新。"
            + (f"\n[run] 下载地址：{url}" if url else ""),
            file=sys.stderr,
        )
        return

    if message:
        print(f"[run] {message}，开始自动更新", flush=True)
    else:
        print("[run] 检测到 lark-cli 有新版本，开始自动更新", flush=True)
    res = _run(["lark-cli", "update", "--json"])
    if res.returncode != 0:
        print("[run] lark-cli 自动更新失败。", file=sys.stderr)
        sys.exit(1)


def _load_json_command(
    cmd: list[str],
    timeout: float = 15,
    *,
    project_local: bool = False,
) -> dict | None:
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=lark_cli_subprocess_env() if project_local else None,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if res.returncode != 0:
        return None
    try:
        data = json.loads(res.stdout or "{}")
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def is_app_configured(*, project_local: bool = False) -> bool:
    data = _load_json_command(["lark-cli", "config", "show"], project_local=project_local)
    if not data:
        return False
    if data.get("appId") or data.get("app_id"):
        return True
    apps = data.get("apps") or data.get("Apps")
    if isinstance(apps, list):
        return any(
            isinstance(app, dict) and (app.get("appId") or app.get("AppId") or app.get("app_id"))
            for app in apps
        )
    return False


def ensure_app_config(mode: str) -> None:
    if mode != "bootstrap":
        if not project_lark_cli_config_exists():
            _hint_bootstrap_and_exit(
                f"未检测到项目内飞书应用配置：{PROJECT_LARK_CLI_CONFIG_PATH}。"
            )
        if is_app_configured(project_local=True):
            print(f"[{mode}] 检查项目内应用配置：已完成", flush=True)
            return
        _hint_bootstrap_and_exit(
            f"项目内飞书应用配置不可用：{PROJECT_LARK_CLI_CONFIG_PATH}。"
        )
    while True:
        if is_app_configured():
            print(f"[{mode}] 检查应用配置：已完成", flush=True)
            return
        print(f"[{mode}] 检查应用配置：未完成，开始 `lark-cli config init --new`", flush=True)
        res = _run(["lark-cli", "config", "init", "--new"])
        if res.returncode == 0:
            continue
        _wait_before_retry(
            mode,
            CLI_FLOW_RETRY_INTERVAL_SECONDS,
            "`lark-cli config init --new` 未完成或已超时",
        )


def check_app_configured() -> None:
    if not is_app_configured():
        _hint_bootstrap_and_exit("未检测到飞书应用配置。")


def is_user_logged_in(*, project_local: bool = False) -> bool:
    data = _load_json_command(["lark-cli", "auth", "status"], project_local=project_local)
    if not data:
        return False
    identity = (data.get("identity") or "").lower()
    if identity != "user":
        return False

    # lark-cli 1.0.44+ 把字段嵌到 identities.user 下；旧版本字段仍在顶层。
    identities = data.get("identities")
    user_block = identities.get("user") if isinstance(identities, dict) else None
    if isinstance(user_block, dict):
        if user_block.get("available") is True and (
            (user_block.get("status") or "").lower() == "ready"
        ):
            return True
        token_status = (user_block.get("tokenStatus") or "").lower()
        if token_status and token_status != "expired":
            return True

    token_status = (data.get("tokenStatus") or "").lower()
    if token_status and token_status != "expired":
        return True
    return data.get("verified") is True


def ensure_user_login(mode: str) -> None:
    project_local = mode != "bootstrap"
    while True:
        if is_user_logged_in(project_local=project_local):
            print(f"[{mode}] 检查用户授权：已完成", flush=True)
            return
        print(f"[{mode}] 检查用户授权：未完成，开始 `lark-cli auth login --recommend`", flush=True)
        res = _run(
            ["lark-cli", "auth", "login", "--recommend"],
            env=lark_cli_subprocess_env() if project_local else None,
        )
        if res.returncode == 0:
            continue
        _wait_before_retry(
            mode,
            CLI_FLOW_RETRY_INTERVAL_SECONDS,
            "`lark-cli auth login --recommend` 未完成或已超时",
        )


def check_user_logged_in() -> None:
    if not is_user_logged_in(project_local=True):
        _hint_bootstrap_and_exit("当前未以 user 身份登录或登录已过期。")


def check_python_deps() -> None:
    if importlib.util.find_spec("lark_oapi") is not None:
        return
    _hint_bootstrap_and_exit("当前虚拟环境缺少 Python 依赖 `lark-oapi`。")


def _lock_stamp() -> str:
    return hashlib.sha256(
        f"{PYTHON_VERSION}\n{UV_LOCK_PATH.read_text(encoding='utf-8')}".encode("utf-8")
    ).hexdigest()


def _read_installed_deps_stamp() -> str | None:
    if not DEPS_STAMP_PATH.exists():
        return None
    return DEPS_STAMP_PATH.read_text(encoding="utf-8").strip() or None


def _write_installed_deps_stamp(stamp: str) -> None:
    DEPS_STAMP_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEPS_STAMP_PATH.write_text(f"{stamp}\n", encoding="utf-8")


def _uv_candidates() -> list[Path]:
    candidates: list[Path] = []
    local_name = "uv.exe" if os.name == "nt" else "uv"
    candidates.append(UV_INSTALL_DIR / local_name)
    home = Path.home()
    if os.name == "nt":
        candidates.append(home / ".local" / "bin" / "uv.exe")
    else:
        candidates.append(home / ".local" / "bin" / "uv")
    return candidates


def _find_uv() -> str | None:
    found = _which("uv")
    if found:
        return found
    for candidate in _uv_candidates():
        if candidate.exists():
            return str(candidate)
    return None


def install_python_deps() -> None:
    if not UV_LOCK_PATH.exists():
        print(
            f"[bootstrap] 未找到 {UV_LOCK_PATH}，请先执行 `uv lock --native-tls` 生成锁文件后再启动。",
            file=sys.stderr,
        )
        sys.exit(1)
    stamp = _lock_stamp()
    if _read_installed_deps_stamp() == stamp:
        print("[bootstrap] 检查 Python 依赖：uv.lock 未变更，跳过", flush=True)
        return
    uv_path = _find_uv()
    if not uv_path:
        print(
            "[bootstrap] 未检测到 uv，无法按 uv.lock 同步依赖。请通过 npm run bootstrap 启动。",
            file=sys.stderr,
        )
        sys.exit(1)
    print("[bootstrap] 检查 Python 依赖：使用 uv.lock 同步", flush=True)
    env = os.environ.copy()
    env["UV_PROJECT_ENVIRONMENT"] = str(VENV_PATH)
    res = _run(
        [
            uv_path,
            "sync",
            "--frozen",
            "--no-install-project",
            "--native-tls",
            "--python",
            sys.executable,
        ],
        env=env,
    )
    if res.returncode != 0:
        print("[bootstrap] Python 依赖安装失败。", file=sys.stderr)
        sys.exit(1)
    _write_installed_deps_stamp(stamp)


def _stop_processes(processes: list[subprocess.Popen], timeout: float = 10.0) -> None:
    running = [proc for proc in processes if proc.poll() is None]
    for proc in running:
        proc.terminate()

    deadline = time.monotonic() + timeout
    for proc in running:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                logger.error("子进程强杀后仍未退出 pid=%s", proc.pid)


def _should_start_dashboard_api() -> bool:
    """仅当配置显式开启时，才允许 dashboard_api 进入编排。"""
    try:
        return DashboardConfig.load().enabled
    except Exception as exc:  # noqa: BLE001
        # 这里保持保守：配置读取异常时不拉起旁路 API，避免启动链继续放大错误。
        logger.error("读取 dashboard_api 配置失败，跳过启动：%s", exc)
        return False


def _start_dashboard_api(env: dict[str, str]) -> subprocess.Popen:
    cmd = [sys.executable, "-m", "src.dashboard_api.main"]
    logger.info("启动 dashboard_api：%s", " ".join(cmd))
    return subprocess.Popen(cmd, env=env)


def start_services(mode: str, bot_args: list[str]) -> None:
    configure_logging(service_name="launcher", level=os.environ.get("MSG_LISTENER_LOG_LEVEL", "INFO"))
    log_process_started(logger, "初始化启动入口" if mode == "bootstrap" else "快速启动入口")
    env = lark_cli_subprocess_env(os.environ.copy())
    py_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{PROJECT_ROOT}{os.pathsep}{py_path}" if py_path else str(PROJECT_ROOT)
    os.chdir(PROJECT_ROOT)
    initial_parent_pid = os.getppid()

    main_cmd = [sys.executable, "-m", "src.main", *bot_args]
    logger.info("启动服务编排入口：%s", " ".join(main_cmd))
    main_proc = subprocess.Popen(main_cmd, env=env)
    dashboard_proc: subprocess.Popen | None = None
    stopped = False

    def stop_children() -> None:
        nonlocal stopped
        if stopped:
            return
        stopped = True
        active = [main_proc]
        if dashboard_proc is not None:
            active.append(dashboard_proc)
        _stop_processes(active)

    def handle_signal(signum: int, _: object) -> None:
        logger.info("收到信号 %s，正在停止服务", signum)
        stop_children()
        sys.exit(128 + signum)

    install_signal_handlers(DEFAULT_SIGNAL_SET, handle_signal)

    while True:
        if parent_process_changed(initial_parent_pid):
            logger.warning(
                "检测到父进程已变更，old_ppid=%s current_ppid=%s，正在停止服务",
                initial_parent_pid,
                os.getppid(),
            )
            stop_children()
            sys.exit(1)
        code = main_proc.poll()
        if code is not None:
            logger.info("服务编排入口已退出，exit_code=%s，正在停止其他服务", code)
            stop_children()
            sys.exit(code)
        bot_ready = BOT_READY_STAMP_PATH.exists()
        if not bot_ready and dashboard_proc is not None:
            logger.info("bot 尚未就绪或正在重启，停止 dashboard_api")
            _stop_processes([dashboard_proc])
            dashboard_proc = None
        if bot_ready and dashboard_proc is None and _should_start_dashboard_api():
            dashboard_proc = _start_dashboard_api(env)
        if dashboard_proc is not None:
            dashboard_code = dashboard_proc.poll()
            if dashboard_code is not None:
                logger.warning("dashboard_api 已退出，exit_code=%s", dashboard_code)
                dashboard_proc = None
        time.sleep(1)


def bootstrap() -> None:
    ensure_config_file("bootstrap")
    ensure_kxcymc_openapi_pat_configured("bootstrap")
    check_basic_deps()
    check_python_version()
    ensure_lark_cli()
    ensure_meegle_cli()
    meegle_host = ensure_meegle_host("bootstrap")
    ensure_app_config("bootstrap")
    ensure_user_login("bootstrap")
    ensure_meegle_login("bootstrap", meegle_host)
    ensure_bot_app_permissions("bootstrap")
    ensure_card_callback_event("bootstrap")
    ensure_bot_menu_event("bootstrap")
    ensure_bot_menu_config("bootstrap")
    try:
        path = snapshot_current_lark_cli_config_to_project()
    except LarkConfigError as exc:
        print(f"[bootstrap] 固化项目内飞书应用配置失败：{exc}", file=sys.stderr)
        sys.exit(1)
    print(f"[bootstrap] 项目内飞书应用配置已写入：{path}", flush=True)
    install_python_deps()


def run_checks() -> None:
    ensure_config_file("run")
    ensure_kxcymc_openapi_pat_configured("run")
    check_python_version()
    check_lark_cli_present()
    check_meegle_present()
    # update_lark_cli_for_run_mode()
    check_python_deps()
    meegle_host = ensure_meegle_host("run")
    ensure_app_config("run")
    ensure_user_login("run")
    ensure_meegle_login("run", meegle_host)
    ensure_bot_app_permissions("run")
    ensure_card_callback_event("run")
    ensure_bot_menu_event("run")
    ensure_bot_menu_config("run")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="msg-listener-setup")
    parser.add_argument("--mode", choices=("bootstrap", "run"), required=True)
    args, passthrough = parser.parse_known_args(argv)
    args.passthrough = passthrough
    return args


def main(argv: list[str] | None = None) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    install_system_log_tee()
    ensure_system_tls_trust()
    if args.mode == "bootstrap":
        bootstrap()
    else:
        run_checks()
    start_services(args.mode, args.passthrough)


if __name__ == "__main__":
    main()
