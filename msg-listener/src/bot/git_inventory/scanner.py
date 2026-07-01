"""Read-only local git repository scanner."""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GitRemote:
    name: str
    url: str


@dataclass(frozen=True)
class RepoInfo:
    repo_id: str
    label: str
    local_path: str
    remotes: list[GitRemote]
    branches: list[str]


class RepoScanner:
    def __init__(
        self,
        *,
        roots: list[str],
        ignore_substrings: list[str],
        max_depth: int,
        command_timeout_seconds: float = 5.0,
        scan_timeout_seconds: float = 30.0,
    ) -> None:
        self.roots = roots
        self.ignore_substrings = ignore_substrings
        self.max_depth = max(0, max_depth)
        self.command_timeout_seconds = command_timeout_seconds
        self.scan_timeout_seconds = scan_timeout_seconds

    async def scan(self) -> list[RepoInfo]:
        try:
            return await asyncio.wait_for(self._scan(), timeout=self.scan_timeout_seconds)
        except asyncio.TimeoutError:
            logger.warning("git 仓库扫描整体超时 %.1fs", self.scan_timeout_seconds)
            return []

    async def _scan(self) -> list[RepoInfo]:
        repo_paths = self._discover_repo_paths()
        if not repo_paths:
            logger.info("git 仓库扫描完成 count=0")
            return []
        # 并发扫描各仓库：每个 _scan_repo 内部仅是 git 子进程 + 网络/磁盘 IO，
        # 串行 await 会被外部命令吞掉时间；用 gather 让事件循环并发推进。
        # return_exceptions=True 保证单仓异常不影响其它仓库结果。
        outcomes = await asyncio.gather(
            *(self._scan_repo(path) for path in repo_paths),
            return_exceptions=True,
        )
        repos: list[RepoInfo] = []
        seen_repo_ids: dict[str, str] = {}
        # 保持与 repo_paths 一致的稳定顺序，确保 repo_id 冲突时"先发现者保留"。
        for repo_path, outcome in zip(repo_paths, outcomes):
            if isinstance(outcome, Exception):
                logger.warning("扫描 git 仓库失败 path=%s: %s", repo_path, outcome)
                continue
            if outcome is None:
                continue
            existing_path = seen_repo_ids.get(outcome.repo_id)
            if existing_path is not None:
                logger.warning(
                    "git 仓库 repo_id 冲突，跳过重复项 repo_id=%s 已记录=%s 跳过=%s",
                    outcome.repo_id,
                    existing_path,
                    outcome.local_path,
                )
                continue
            seen_repo_ids[outcome.repo_id] = outcome.local_path
            repos.append(outcome)
        logger.info("git 仓库扫描完成 count=%d", len(repos))
        return repos

    def _discover_repo_paths(self) -> list[Path]:
        found: list[Path] = []
        seen: set[Path] = set()
        for raw_root in self.roots:
            root = Path(raw_root).expanduser()
            if not root.exists():
                continue
            root = root.resolve()
            for current, dirnames, filenames in os.walk(root):
                current_path = Path(current)
                current_text = current_path.as_posix()
                if any(item and item in current_text for item in self.ignore_substrings):
                    dirnames[:] = []
                    continue
                if not self._within_depth(root, current_path):
                    dirnames[:] = []
                    continue
                if ".git" in dirnames or ".git" in filenames:
                    if current_path not in seen:
                        seen.add(current_path)
                        found.append(current_path)
                    dirnames[:] = [name for name in dirnames if name != ".git"]
                    continue
                next_depth = len(current_path.relative_to(root).parts) + 1
                if next_depth > self.max_depth:
                    dirnames[:] = []
                else:
                    dirnames[:] = [
                        name
                        for name in dirnames
                        if not any(
                            item and item in (current_path / name).as_posix()
                            for item in self.ignore_substrings
                        )
                    ]
        return found

    def _within_depth(self, root: Path, path: Path) -> bool:
        try:
            relative = path.relative_to(root)
        except ValueError:
            return False
        return len(relative.parts) <= self.max_depth

    async def _scan_repo(
        self,
        repo_path: Path,
    ) -> RepoInfo | None:
        remotes_raw = await self._git(repo_path, "remote", "-v")
        branches_raw = await self._git(
            repo_path,
            "for-each-ref",
            "--format=%(refname:short)",
            "refs/heads",
        )
        branches = [line.strip() for line in branches_raw.splitlines() if line.strip()]
        if not branches:
            logger.debug("git 仓库无本地分支，跳过: %s", repo_path)
            return None
        repo_id = repo_path.name
        return RepoInfo(
            repo_id=repo_id,
            label=repo_path.name,
            local_path=str(repo_path),
            remotes=_parse_remotes(remotes_raw),
            branches=branches,
        )

    async def _git(self, repo_path: Path, *args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(repo_path),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.command_timeout_seconds,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise RuntimeError(f"git {' '.join(args)} timeout")
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"git {' '.join(args)} failed: {err}")
        return stdout.decode("utf-8", errors="replace")


def _parse_remotes(output: str) -> list[GitRemote]:
    remotes: list[GitRemote] = []
    seen: set[tuple[str, str]] = set()
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        key = (parts[0], parts[1])
        if key in seen:
            continue
        seen.add(key)
        remotes.append(GitRemote(name=parts[0], url=parts[1]))
    return remotes
