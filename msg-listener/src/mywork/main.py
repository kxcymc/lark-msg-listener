"""One-shot mywork entrypoint."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

from src.bot.notifier import BotNotifier, NotifierError
from src.common.filter import OwnerCache
from src.common.logging_setup import configure_logging

from .collector import MyworkCollectConfig, collect_mywork
from .concluder import conclude_mywork_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.toml"
LARK_CLI_CONFIG_PATH = PROJECT_ROOT / ".local" / "lark-cli" / "config.json"
_TITLE_DATE_RANGE_RE = re.compile(r"^(\d{8})-(\d{8})_")

logger = logging.getLogger(__name__)


def main() -> None:
    try:
        exit_code = asyncio.run(_amain())
    except KeyboardInterrupt:
        exit_code = 130
    sys.exit(exit_code)


async def _amain() -> int:
    cfg = _load_config(CONFIG_PATH)
    configure_logging(
        service_name="mywork",
        level=str(cfg.get("log_level", "INFO") or "INFO"),
    )
    mywork_cfg = cfg.get("mywork") or {}
    if not bool(mywork_cfg.get("enabled", True)):
        logger.info("mywork.enabled=false，跳过本次运行")
        return 0

    now = datetime.now().astimezone()
    output_dir = PROJECT_ROOT / "out" / "mywork"
    output_dir.mkdir(parents=True, exist_ok=True)
    date_key = now.strftime("%Y%m%d")
    json_path = output_dir / f"{date_key}_mywork.json"
    doc_result_path = output_dir / f"{date_key}_mywork_doc.json"

    owner = OwnerCache(PROJECT_ROOT / "cache")
    owner_open_id = await owner.load_or_fetch()
    owner_name = owner.name or ""
    logger.info("当前用户: %s (%s)", owner_name, owner_open_id)

    collected = await collect_mywork(
        owner_open_id=owner_open_id,
        owner_name=owner_name,
        config=_collect_config(mywork_cfg),
    )
    json_path.write_text(json.dumps(collected, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("mywork JSON 已写入 %s", json_path)

    result = await conclude_mywork_json(
        cfg=cfg,
        json_path=json_path,
        doc_result_path=doc_result_path,
        now=now,
    )
    if not result.success:
        logger.error(
            "mywork 总结失败 provider=%s rc=%s stderr=%s",
            result.provider_name,
            result.returncode,
            result.stderr[:500],
        )
        return result.returncode or 1
    logger.info("mywork 总结完成 document_url=%s token=%s", result.document_url, result.document_token)
    await _notify_mywork_document(
        owner_open_id=owner_open_id,
        document_title=result.document_title,
        document_url=result.document_url,
    )
    return 0


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        default_path = path.with_name("config.toml.default")
        if default_path.exists():
            path = default_path
        else:
            return {}
    with path.open("rb") as file:
        return tomllib.load(file)


def _collect_config(mywork_cfg: dict[str, Any]) -> MyworkCollectConfig:
    return MyworkCollectConfig(
        lookback_days=int(mywork_cfg.get("lookback_days", 1)),
        context_window=int(mywork_cfg.get("context_window", 10)),
        chat_history_buffer_hours=int(mywork_cfg.get("chat_history_buffer_hours", 24)),
        page_size=int(mywork_cfg.get("page_size", 50)),
        max_pages_per_chat=int(mywork_cfg.get("max_pages_per_chat", 40)),
    )


async def _notify_mywork_document(
    *,
    owner_open_id: str,
    document_title: str,
    document_url: str,
) -> None:
    if not document_url:
        logger.warning("mywork 文档 URL 为空，跳过气泡通知")
        return
    username = _read_lark_cli_username(LARK_CLI_CONFIG_PATH) or "用户"
    date_range = _format_title_date_range(document_title)
    text = (
        f"您好，{username}。这是您{date_range}的工作总结，请查收～\n"
        f"{document_url}"
    )
    # 飞书 im/v1/messages 的 uuid（由 lark-cli 的 --idempotency-key 映射）最大 50 字符，
    # 超长时接口只返回 HTTP 400 / field validation failed，日志会误导排查方向。
    # document_url 末段长度不定，直接拼接易超限；改为对稳定输入做 sha1 取短摘要，
    # 既保证同一文档重试时幂等值不变，也不受 url 长度影响。
    idempotency_key = _build_idempotency_key("mywork-doc", date_range, document_url)
    try:
        message_id = await BotNotifier(owner_open_id=owner_open_id).send_text(
            text,
            idempotency_key=idempotency_key,
        )
    except NotifierError as exc:
        logger.warning("mywork 文档气泡通知发送失败：%s", exc)
        return
    logger.info("mywork 文档气泡通知已发送 message_id=%s", message_id)


def _read_lark_cli_username(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("读取 lark-cli 用户名失败 path=%s: %s", path, exc)
        return ""
    current_app = str(payload.get("currentApp") or "")
    apps = payload.get("apps") or []
    if not isinstance(apps, list):
        return ""
    matched_apps = [
        app
        for app in apps
        if isinstance(app, dict)
        and (not current_app or str(app.get("appId") or "") == current_app)
    ]
    for app in matched_apps or [app for app in apps if isinstance(app, dict)]:
        users = app.get("users") or []
        if not isinstance(users, list):
            continue
        for user in users:
            if isinstance(user, dict) and user.get("userName"):
                return str(user["userName"])
    return ""


def _format_title_date_range(document_title: str) -> str:
    match = _TITLE_DATE_RANGE_RE.search(document_title or "")
    if not match:
        return document_title.split("_", 1)[0] if document_title else ""
    start, end = match.groups()
    return f"{start[2:]}-{end[2:]}"


def _build_idempotency_key(prefix: str, *parts: object) -> str:
    # 对稳定输入做 sha1 取前 16 位摘要，拼接固定前缀，保证整体长度远低于飞书 50 字符上限。
    raw = "-".join(str(part) for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


if __name__ == "__main__":
    main()
