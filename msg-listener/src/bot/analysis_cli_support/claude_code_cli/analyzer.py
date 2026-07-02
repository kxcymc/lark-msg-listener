"""Claude Code CLI 分析器适配。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ....common.claude_code_cli import (
    CLAUDE_CODE_CLI_ANALYSIS_TASK_STATE_DIR,
    CLAUDE_CODE_CLI_DEFAULT_COMMAND,
    CLAUDE_CODE_CLI_DEFAULT_OUTPUT_FORMAT,
    CLAUDE_CODE_CLI_READONLY_DISALLOWED_TOOLS,
    build_claude_print_argv,
    merge_disallowed_tools,
)
from ...analysis.analyzers.types import (
    AnalyzerError,
    AnalyzerInput,
    AnalyzerRateLimited,
    AnalyzerResult,
    CATEGORY_UNKNOWN,
    VALID_CATEGORIES,
)

logger = logging.getLogger(__name__)


_DEFAULT_PROMPT = """你是飞书消息记录的需求探测助手。

我会在 `=== INPUT BEGIN ===` 与 `=== INPUT END ===` 之间给你一个 JSON 对象，结构示例：
{
  "record_id": "...",
  "record_path": "...",
  "chat_type": "group|p2p",
  "anchor": {"sender_name": "...", "create_time": "...", "text": "...", "links": ["https://..."]},
  "messages": [{"position": "before|after", "sender_name": "...", "create_time": "...", "type": "...", "text": "...", "links": ["https://..."]}],
  "media": [{"kind": "image|video", "local_path": "本地可读绝对路径", "record_relative_path": "..."}]
}

请阅读输入区间内的 JSON，并判定以下问题：
1. 这条 anchor 消息（结合前后文与 media 中可读取的本地图片）是否构成一个面向研发执行者的、可直接落地的需求？
2. 把判断结果归一化为下面定义的 JSON schema，严格只输出该 JSON，不要输出任何解释、注释或 Markdown 代码块。

输出 JSON schema：
{
  "category": "requirement|question|chat|noise|unknown",
  "prompt": "面向执行者、可直接落地的需求描述。覆盖客户端、后端、测试、算法、数据处理、脚本、配置等任意类型；非 requirement 时填空字符串",
  "summary": "对需求的简洁归纳；非 requirement 时填空字符串",
  "evidence": ["anchor.text" 或 "messages[2]" 或 "media[0]" 等，标识判断依据来源",
  "reason": "为什么判定为该 category 的简要说明"
}

规则：
- 只输出严格 JSON，不要带前后文字。
- 如果证据不足，category 用 "unknown"。
- 仅当 category == "requirement" 时填写 prompt 与 summary。
- prompt 必须是面向执行者的可落地描述，不要复述原话，不要罗列对话片段。
- 如输入中的 anchor.links 或 messages[].links 对需求落地有价值，必须在 prompt 或 evidence 中原样保留，不要省略、改写或只总结链接内容。
"""


_RATE_LIMIT_PATTERNS = [
    re.compile(r"rate[\s_-]?limit", re.IGNORECASE),
    re.compile(r"too many requests", re.IGNORECASE),
    re.compile(r"429", re.IGNORECASE),
    re.compile(r"quota", re.IGNORECASE),
    re.compile(r"credit", re.IGNORECASE),
    re.compile(r"usage limit", re.IGNORECASE),
]


@dataclass
class ClaudeCodeCliConfig:
    command: str = CLAUDE_CODE_CLI_DEFAULT_COMMAND
    output_format: str = CLAUDE_CODE_CLI_DEFAULT_OUTPUT_FORMAT
    analysis_prompt_template: str = ""
    model_name: str = ""
    disallowed_tools: tuple[str, ...] = field(
        default_factory=lambda: CLAUDE_CODE_CLI_READONLY_DISALLOWED_TOOLS
    )
    timeout_seconds: float = 240.0
    state_dir: Path = Path(CLAUDE_CODE_CLI_ANALYSIS_TASK_STATE_DIR)
    ccr_env: dict[str, str] | None = None   # CCR 注入后的子进程环境；None 表示不注入（继承父进程环境）


def _looks_like_rate_limit(text: str) -> bool:
    if not text:
        return False
    return any(p.search(text) for p in _RATE_LIMIT_PATTERNS)


def _parse_json_string(text: str) -> dict | list | None:
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
        stripped = stripped.strip()
    if not (
        (stripped.startswith("{") and stripped.endswith("}"))
        or (stripped.startswith("[") and stripped.endswith("]"))
    ):
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, (dict, list)):
        return parsed
    return None


def _extract_target_payload(obj: object, *, depth: int = 0) -> dict | None:
    if depth > 12:
        return None
    if isinstance(obj, dict):
        if obj.get("category") in VALID_CATEGORIES:
            return obj
        priority_keys = (
            "result",
            "data",
            "content",
            "message",
            "messages",
            "response",
            "text",
        )
        keys = [key for key in priority_keys if key in obj]
        keys.extend(key for key in obj.keys() if key not in set(keys))
        for key in keys:
            nested = _extract_target_payload(obj.get(key), depth=depth + 1)
            if nested is not None:
                return nested
        return None
    if isinstance(obj, list):
        for item in reversed(obj):
            nested = _extract_target_payload(item, depth=depth + 1)
            if nested is not None:
                return nested
        return None
    if isinstance(obj, str):
        parsed = _parse_json_string(obj)
        if parsed is None:
            return None
        return _extract_target_payload(parsed, depth=depth + 1)
    return None


def _strip_json_envelope(stdout: str) -> str | None:
    if not stdout:
        return None
    text = stdout.strip()
    if not text:
        return None

    if text.startswith("{") and text.endswith("}"):
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(obj, dict):
                target = _extract_target_payload(obj)
                if target is not None:
                    return json.dumps(target, ensure_ascii=False)

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    candidate_payloads: list[str] = []
    for line in reversed(lines):
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        target = _extract_target_payload(obj)
        if target is not None:
            return json.dumps(target, ensure_ascii=False)
        candidate_payloads.append(line)

    for payload in candidate_payloads:
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("category") in VALID_CATEGORIES:
            return payload

    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(obj, dict) and obj.get("category") in VALID_CATEGORIES:
        return text
    return None


def _coerce_result(payload: dict) -> AnalyzerResult:
    category = payload.get("category")
    if category not in VALID_CATEGORIES:
        logger.warning("category 字段非法，按 unknown 处理: %r", category)
        category = CATEGORY_UNKNOWN
    prompt = str(payload.get("prompt") or "")
    summary = str(payload.get("summary") or "")
    reason = str(payload.get("reason") or "")
    evidence_raw = payload.get("evidence") or []
    if isinstance(evidence_raw, list):
        evidence = [str(item) for item in evidence_raw if item]
    else:
        evidence = []
    return AnalyzerResult(
        category=str(category),
        prompt=prompt,
        summary=summary,
        evidence=evidence,
        reason=reason,
        raw=payload,
    )


class ClaudeCodeCliAnalyzer:
    """通过 Claude Code CLI 子进程分析 record。"""

    name = "claude_code_cli"

    def __init__(self, config: ClaudeCodeCliConfig) -> None:
        self.config = config

    def is_available(self) -> bool:
        return shutil.which(self.config.command) is not None

    def _build_prompt_payload(self, prompt: str, payload: AnalyzerInput) -> str:
        body = json.dumps(payload.to_dict(), ensure_ascii=False)
        return (
            f"{prompt}\n\n"
            f"=== INPUT BEGIN ===\n{body}\n=== INPUT END ===\n"
        )

    def _build_argv(self, prompt: str, payload: AnalyzerInput) -> list[str]:
        return build_claude_print_argv(
            model=self.config.model_name,
            output_format=self.config.output_format,
            prompt=self._build_prompt_payload(prompt, payload),
            # 需求分析场景必须只读：无论配置怎么填，都固定追加写入类禁用工具。
            disallowed_tools=merge_disallowed_tools(
                self.config.disallowed_tools,
                CLAUDE_CODE_CLI_READONLY_DISALLOWED_TOOLS,
            ),
        )

    def _build_prompt(self) -> str:
        return self.config.analysis_prompt_template.strip() or _DEFAULT_PROMPT

    async def analyze(self, payload: AnalyzerInput) -> AnalyzerResult:
        prompt = self._build_prompt()
        argv = self._build_argv(prompt, payload)
        task = _AnalysisTaskMonitor.create(
            state_dir=self.config.state_dir,
            payload=payload,
            command=self.config.command,
            model=self.config.model_name,
            output_format=self.config.output_format,
            disallowed_tools=self.config.disallowed_tools,
        )

        logger.debug("claude exec: %s", " ".join(argv))
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.config.ccr_env,
            )
            task.write_process(pid=proc.pid)
        except FileNotFoundError as exc:
            task.write_result(
                status="failed",
                error=f"未找到 Claude Code CLI 可执行文件：{self.config.command}",
            )
            raise AnalyzerError(
                f"未找到 Claude Code CLI 可执行文件：{self.config.command}",
                retriable=False,
            ) from exc

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=b""),
                timeout=self.config.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            task.write_result(
                status="failed",
                error=f"Claude Code CLI 子进程超时 (> {self.config.timeout_seconds:.0f}s)",
            )
            raise AnalyzerError(
                f"Claude Code CLI 子进程超时 (> {self.config.timeout_seconds:.0f}s)",
                retriable=True,
            ) from exc

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        rc = proc.returncode
        task.write_logs(stdout=stdout, stderr=stderr)

        if rc != 0:
            if _looks_like_rate_limit(stderr) or _looks_like_rate_limit(stdout):
                task.write_result(
                    status="failed",
                    returncode=rc,
                    error=f"rate_limited: {stderr.strip()[:400] or stdout.strip()[:400]}",
                )
                raise AnalyzerRateLimited(
                    f"Claude Code CLI 限流/额度不足 (rc={rc}): {stderr.strip()[:200]}",
                    raw_stdout=stdout,
                    raw_stderr=stderr,
                )
            task.write_result(
                status="failed",
                returncode=rc,
                error=stderr.strip()[:1000] or stdout.strip()[:1000],
            )
            raise AnalyzerError(
                f"Claude Code CLI 子进程失败 (rc={rc}): {stderr.strip()[:400]}",
                retriable=True,
                raw_stdout=stdout,
                raw_stderr=stderr,
            )

        envelope = _strip_json_envelope(stdout)
        if not envelope:
            if _looks_like_rate_limit(stdout) or _looks_like_rate_limit(stderr):
                task.write_result(
                    status="failed",
                    returncode=rc,
                    error="rate_limited: stdout/stderr matched rate-limit patterns",
                )
                raise AnalyzerRateLimited(
                    "Claude Code CLI 输出疑似限流/额度不足",
                    raw_stdout=stdout,
                    raw_stderr=stderr,
                )
            task.write_result(
                status="failed",
                returncode=rc,
                error="Claude Code CLI stdout 无法解析为目标 JSON",
            )
            raise AnalyzerError(
                "Claude Code CLI stdout 无法解析为目标 JSON",
                retriable=False,
                raw_stdout=stdout,
                raw_stderr=stderr,
            )
        try:
            data = json.loads(envelope)
        except json.JSONDecodeError as exc:
            task.write_result(
                status="failed",
                returncode=rc,
                error=f"Claude Code CLI JSON 解析失败: {exc}",
            )
            raise AnalyzerError(
                f"Claude Code CLI JSON 解析失败: {exc}",
                retriable=False,
                raw_stdout=stdout,
                raw_stderr=stderr,
            ) from exc
        if not isinstance(data, dict):
            task.write_result(
                status="failed",
                returncode=rc,
                error="Claude Code CLI JSON 顶层不是对象",
            )
            raise AnalyzerError(
                "Claude Code CLI JSON 顶层不是对象",
                retriable=False,
                raw_stdout=stdout,
                raw_stderr=stderr,
            )
        result = _coerce_result(data)
        task.write_result(
            status="succeeded",
            returncode=rc,
            category=result.category,
            summary=result.summary,
            reason=result.reason,
        )
        return result


class _AnalysisTaskMonitor:
    """把分析侧 Claude CLI 子进程状态落盘，便于从 out 目录观察执行过程。"""

    def __init__(self, task_dir: Path) -> None:
        self.task_dir = task_dir

    @classmethod
    def create(
        cls,
        *,
        state_dir: Path,
        payload: AnalyzerInput,
        command: str,
        model: str,
        output_format: str,
        disallowed_tools: tuple[str, ...],
    ) -> "_AnalysisTaskMonitor":
        task_id = f"analysis_{uuid.uuid4().hex}"
        task_dir = state_dir / task_id
        task_dir.mkdir(parents=True, exist_ok=False)
        monitor = cls(task_dir)
        monitor._write_json(
            "metadata.json",
            {
                "analysis_task_id": task_id,
                "record_id": payload.record_id,
                "record_path": payload.record_path,
                "chat_type": payload.chat_type,
                "command": command,
                "model": model,
                "output_format": output_format,
                "disallowed_tools": list(disallowed_tools),
                "created_at": time.time(),
                "status": "running",
            },
        )
        return monitor

    def write_process(self, *, pid: int) -> None:
        self._write_json(
            "process.json",
            {
                "pid": pid,
                "started_at": time.time(),
                "metadata": str(self.task_dir / "metadata.json"),
                "stdout": str(self.task_dir / "stdout.log"),
                "stderr": str(self.task_dir / "stderr.log"),
                "result": str(self.task_dir / "result.json"),
            },
        )

    def write_logs(self, *, stdout: str, stderr: str) -> None:
        (self.task_dir / "stdout.log").write_text(stdout, encoding="utf-8")
        (self.task_dir / "stderr.log").write_text(stderr, encoding="utf-8")

    def write_result(
        self,
        *,
        status: str,
        returncode: int | None = None,
        error: str = "",
        category: str = "",
        summary: str = "",
        reason: str = "",
    ) -> None:
        self._write_json(
            "result.json",
            {
                "status": status,
                "returncode": returncode,
                "error": error,
                "category": category,
                "summary": summary,
                "reason": reason,
                "finished_at": time.time(),
            },
        )

    def _write_json(self, name: str, payload: dict) -> None:
        (self.task_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
