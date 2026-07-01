"""kxcymc_openapi 启动期校验与 /config 卡片支持。"""
from __future__ import annotations

import logging
import sys

from ..types import DevTaskExecutorCardSupport
from .executor import kxcymcOpenApiExecutor
from .models import (
    kxcymc_OPENAPI_DEFAULT_AGENT,
    kxcymc_OPENAPI_DEFAULT_ENDPOINT,
    kxcymc_OPENAPI_DEFAULT_FILE_ENDPOINT,
    kxcymc_OPENAPI_DEFAULT_MODEL,
    kxcymcOpenApiConfig,
)

logger = logging.getLogger(__name__)


def _build_config(cfg: dict) -> kxcymcOpenApiConfig:
    dev_task_cfg = ((cfg.get("phase3") or {}).get("dev_task") or {})
    kxcymc_cfg = dev_task_cfg.get("kxcymc_openapi") or {}
    return kxcymcOpenApiConfig(
        endpoint=str(kxcymc_cfg.get("endpoint", kxcymc_OPENAPI_DEFAULT_ENDPOINT))
        or kxcymc_OPENAPI_DEFAULT_ENDPOINT,
        file_endpoint=str(kxcymc_cfg.get("file_endpoint", kxcymc_OPENAPI_DEFAULT_FILE_ENDPOINT))
        or kxcymc_OPENAPI_DEFAULT_FILE_ENDPOINT,
        pat_token=str(kxcymc_cfg.get("pat_token", "")),
        agent_name=str(kxcymc_cfg.get("agent_name", kxcymc_OPENAPI_DEFAULT_AGENT))
        or kxcymc_OPENAPI_DEFAULT_AGENT,
        model_name=str(kxcymc_cfg.get("model_name", kxcymc_OPENAPI_DEFAULT_MODEL))
        or kxcymc_OPENAPI_DEFAULT_MODEL,
        submit_timeout_seconds=float(
            dev_task_cfg.get("submit_timeout_seconds", 30)
        ),
    )


def build_executor(cfg: dict) -> kxcymcOpenApiExecutor:
    return kxcymcOpenApiExecutor(_build_config(cfg))


async def build_card_support(cfg: dict) -> DevTaskExecutorCardSupport:
    """kxcymc_openapi 当前不向 /config 注入额外动态选项，只标记可用性。"""
    config = _build_config(cfg)
    return DevTaskExecutorCardSupport(available=bool(config.pat_token.strip()))


async def ensure_ready(cfg: dict) -> None:
    config = _build_config(cfg)
    if not config.endpoint.startswith("http"):
        logger.error(
            "phase3.dev_task.kxcymc_openapi.endpoint=%s 非法，请配置完整 https URL",
            config.endpoint,
        )
        sys.exit(1)
    if not config.pat_token.strip():
        logger.warning(
            "未检测到 kxcymc OpenAPI PAT：phase3.dev_task.kxcymc_openapi.pat_token 为空。\n"
            "服务将继续启动，以便通过 /config 卡片配置 pat_token；确认开发前必须先补齐。",
        )
        return
    logger.debug(
        "kxcymc_openapi 启动检查通过 endpoint=%s pat_token=***",
        config.endpoint,
    )
