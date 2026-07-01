"""trae_side_chat startup checks and /config card support."""
from __future__ import annotations

from ..types import DevTaskExecutorCardSupport
from .executor import TraeSideChatExecutor


def build_executor(cfg: dict) -> TraeSideChatExecutor:
    del cfg
    return TraeSideChatExecutor()


async def build_card_support(cfg: dict) -> DevTaskExecutorCardSupport:
    del cfg
    return DevTaskExecutorCardSupport(available=True)


async def ensure_ready(cfg: dict) -> None:
    del cfg
