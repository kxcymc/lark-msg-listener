"""Cached git inventory service."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from .repository import RepoInventoryRepository
from .scanner import RepoInfo, RepoScanner

logger = logging.getLogger(__name__)


class GitInventoryService:
    def __init__(
        self,
        *,
        repository: RepoInventoryRepository,
        scanner: RepoScanner,
        ttl_seconds: int,
    ) -> None:
        self.repository = repository
        self.scanner = scanner
        self.ttl_seconds = max(0, ttl_seconds)
        self._lock = asyncio.Lock()

    async def get_inventory(self) -> list[RepoInfo]:
        cached = self.repository.list_all()
        if cached and self._is_cache_fresh():
            return cached
        return await self.refresh()

    async def refresh(self) -> list[RepoInfo]:
        async with self._lock:
            repos = await self.scanner.scan()
            if not repos:
                logger.warning("git 扫描无结果，本次由需求卡片改为手动填写 repo 与 branch")
                return []
            self.repository.replace_all(repos)
            return repos

    def _is_cache_fresh(self) -> bool:
        raw = self.repository.latest_scanned_at()
        if not raw:
            return False
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return False
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return datetime.now().astimezone() - dt < timedelta(seconds=self.ttl_seconds)
