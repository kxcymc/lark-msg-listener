"""Orchestrate mywork JSON conclusion and document result persistence."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .conclude_cli_registry import (
    ensure_selected_conclude_cli_ready,
    resolve_conclude_cli,
    build_selected_concluder,
)
from .conclude_cli_types import ConcludeCliInput, ConcludeCliOutput

logger = logging.getLogger(__name__)


async def conclude_mywork_json(
    *,
    cfg: dict[str, Any],
    json_path: Path,
    doc_result_path: Path,
    now: datetime | None = None,
) -> ConcludeCliOutput:
    json.loads(json_path.read_text(encoding="utf-8"))
    current = now or datetime.now().astimezone()
    mywork_cfg = cfg.get("mywork") or {}
    provider_name = resolve_conclude_cli(cfg)
    provider_cfg = _provider_config(mywork_cfg, provider_name)
    title_text = str(provider_cfg.get("doc_title_text") or "上周工作总结").strip()
    lookback_days = max(1, int(mywork_cfg.get("lookback_days", 1) or 1))
    range_start = current - timedelta(days=lookback_days - 1)
    title = (
        f"{range_start.strftime('%Y%m%d')}-{current.strftime('%Y%m%d')}_"
        f"{title_text or '上周工作总结'}"
    )
    prompt_content = str(
        provider_cfg.get("prompt_template")
        or "工作事项概览、按会话归纳的进展、待跟进事项"
    ).strip()
    prompt = _build_prompt(
        json_path=json_path,
        doc_title=title,
        prompt_content=prompt_content,
    )

    await ensure_selected_conclude_cli_ready(cfg)
    concluder = build_selected_concluder(cfg)
    result = await concluder.conclude(
        ConcludeCliInput(
            json_path=json_path,
            document_title=title,
            prompt=prompt,
        )
    )
    payload = {
        "created_at": current.isoformat(),
        "provider_name": result.provider_name,
        "document_title": result.document_title,
        "returncode": result.returncode,
        "document_url": result.document_url,
        "document_token": result.document_token,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "json_path": str(json_path),
    }
    doc_result_path.parent.mkdir(parents=True, exist_ok=True)
    doc_result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("mywork 总结结果已写入 %s", doc_result_path)
    return result


def result_to_dict(result: ConcludeCliOutput) -> dict[str, Any]:
    return asdict(result)


def _provider_config(mywork_cfg: dict[str, Any], provider_name: str) -> dict[str, Any]:
    providers = mywork_cfg.get("conclude_cli_providers") or {}
    provider_cfg = providers.get(provider_name) if isinstance(providers, dict) else None
    return provider_cfg if isinstance(provider_cfg, dict) else {}


def _build_prompt(
    *,
    json_path: Path,
    doc_title: str,
    prompt_content: str,
) -> str:
    return (
        f"请读取本地 JSON 文件 `{json_path}`，整理其中的聊天记录，生成标题为"
        f"「{doc_title}」的飞书云文档。要求使用 lark-doc skill 创建文档，内容包含：\n"
        f"{prompt_content}\n"
        "创建成功后输出飞书云文档的 URL。"
    )
