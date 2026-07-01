"""通过 lark-cli 从飞书文档中查找 figma 链接。

仅提供异步工具函数，复用 :mod:`.cli` 的 ``run_json_command`` 调用
``lark-cli docs +fetch --api-version v2``，并对返回的文档内容做模糊正则匹配。
"""
from __future__ import annotations

import html
import logging
import re

from .cli import run_json_command

logger = logging.getLogger(__name__)

# 模糊匹配 figma 相关 URL：协议可选，URL 中只要包含 figma 关键字即可。
_FIGMA_URL_PATTERN = re.compile(
    r"https?://[^\s\"'<>()\[\]]*figma[^\s\"'<>()\[\]]*",
    re.IGNORECASE,
)


async def find_figma_link_in_doc(doc_url: str) -> str | None:
    """从飞书云文档中提取首个 figma 链接，未命中或拉取失败均返回 ``None``。"""
    url = (doc_url or "").strip()
    if not url:
        return None

    result = await run_json_command(
        [
            "lark-cli",
            "docs",
            "+fetch",
            "--api-version",
            "v2",
            "--doc",
            url,
            "--format",
            "json",
        ],
        timeout_seconds=60.0,
    )

    payload = result.data if isinstance(result.data, dict) else None
    if not result.ok or not isinstance(payload, dict) or not payload.get("ok"):
        logger.warning(
            "lark-cli docs +fetch 失败 doc=%s returncode=%s stderr=%s",
            url,
            result.returncode,
            result.stderr,
        )
        return None

    data = payload.get("data")
    document = data.get("document") if isinstance(data, dict) else None
    content = document.get("content") if isinstance(document, dict) else None
    if not isinstance(content, str) or not content:
        return None

    decoded = html.unescape(content)
    match = _FIGMA_URL_PATTERN.search(decoded)
    if not match:
        return None
    return match.group(0).rstrip(".,;:!?")
