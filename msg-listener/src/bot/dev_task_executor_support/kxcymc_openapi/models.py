"""kxcymc_openapi 配置与 HTTP 协议模型。"""
from __future__ import annotations

from dataclasses import dataclass


kxcymc_OPENAPI_DEFAULT_ENDPOINT = "https://kxcymc.com/v2"
kxcymc_OPENAPI_DEFAULT_FILE_ENDPOINT = "https://kxcymc.com/api/"
kxcymc_OPENAPI_DEFAULT_AGENT = ""
kxcymc_OPENAPI_DEFAULT_MODEL = ""


@dataclass(frozen=True)
class kxcymcOpenApiConfig:
    """kxcymc_openapi 执行器运行时配置。"""

    endpoint: str
    file_endpoint: str
    pat_token: str
    agent_name: str
    model_name: str
    submit_timeout_seconds: float


# kxcymc OpenAPI 终态 set
TERMINAL_STATUSES_SUCCESS: tuple[str, ...] = ("succeeded", "success", "completed")
TERMINAL_STATUSES_FAILED: tuple[str, ...] = ("failed", "error", "cancelled", "canceled")
RUNNING_STATUSES: tuple[str, ...] = ("submitted", "queued", "running", "pending", "working")
