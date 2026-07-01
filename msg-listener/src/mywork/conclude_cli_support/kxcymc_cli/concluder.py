"""kxcymc CLI concluder for mywork JSON."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from src.common.lark_app_config import lark_cli_subprocess_env
from src.mywork.conclude_cli_types import ConcludeCliInput, ConcludeCliOutput

logger = logging.getLogger(__name__)

# kxcymc 输出为 Markdown，URL 常被 `**粗体**` 包裹（如 `**https://.../docx/xxx**`）。
# 字符类需排除 `*`，否则会把末尾的 `**` 当作 URL 的一部分，导致链接失效拿不到真实地址。
_DOC_URL_RE = re.compile(r"https?://[^\s)>\"*]+/(?:docx|docs|wiki)/[^\s)>\"*]+")
_TOKEN_RE = re.compile(r"\b(?:docx|doc|wiki)[A-Za-z0-9_-]{8,}\b")


@dataclass
class kxcymcConcludeCliConfig:
    command: str = "kxcymc"
    model_name: str = ""
    timeout_seconds: float = 3600.0
    cwd: str | None = None


class kxcymcCliConcluder:
    name = "kxcymc_cli"

    def __init__(self, config: kxcymcConcludeCliConfig) -> None:
        self.config = config

    def _build_argv(self, payload: ConcludeCliInput) -> list[str]:
        argv = [self.config.command, "-p", payload.prompt, "-y"]
        if self.config.model_name:
            argv += ["-c", f"model.name={self.config.model_name}"]
        return argv

    async def conclude(self, payload: ConcludeCliInput) -> ConcludeCliOutput:
        argv = self._build_argv(payload)
        timeout_errors: list[str] = []
        for attempt in range(1, 3):
            logger.debug("kxcymc mywork conclude exec attempt=%d: %s", attempt, " ".join(argv))
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.config.cwd,
                    env=lark_cli_subprocess_env(),
                )
            except FileNotFoundError as exc:
                return ConcludeCliOutput(
                    provider_name=self.name,
                    document_title=payload.document_title,
                    returncode=127,
                    stderr=f"未找到 kxcymc 可执行文件：{self.config.command}: {exc}",
                )

            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self.config.timeout_seconds,
                )
                break
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                message = f"第 {attempt} 次 kxcymc 子进程超时 (> {self.config.timeout_seconds:.0f}s)"
                timeout_errors.append(message)
                if attempt < 2:
                    logger.warning("%s，将重试", message)
                    continue
                return ConcludeCliOutput(
                    provider_name=self.name,
                    document_title=payload.document_title,
                    returncode=124,
                    stderr="\n".join(timeout_errors),
                )

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        if timeout_errors:
            stderr = "\n".join([*timeout_errors, stderr]).strip()
        doc_url = _extract_document_url(stdout) or _extract_document_url(stderr)
        doc_token = _extract_document_token(doc_url or stdout) or _extract_document_token(stderr)
        return ConcludeCliOutput(
            provider_name=self.name,
            document_title=payload.document_title,
            returncode=int(proc.returncode or 0),
            stdout=stdout,
            stderr=stderr,
            document_url=doc_url,
            document_token=doc_token,
        )


def _extract_document_url(text: str) -> str:
    match = _DOC_URL_RE.search(text or "")
    return match.group(0) if match else ""


def _extract_document_token(text: str) -> str:
    match = _TOKEN_RE.search(text or "")
    return match.group(0) if match else ""
