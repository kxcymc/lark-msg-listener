"""Claude Code CLI 分析器适配。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from dataclasses import dataclass, field

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
    command: str = "claude"
    args: list[str] = field(default_factory=lambda: ["-p", "--output-format", "json"])
    prompt_via: str = "argv"
    analysis_prompt_template: str = ""
    model_name: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(
        default_factory=lambda: ["Edit", "Write", "MultiEdit", "Bash"]
    )
    timeout_seconds: float = 240.0
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
        argv = [self.config.command, *self.config.args]
        if self.config.prompt_via != "stdin":
            argv.append(self._build_prompt_payload(prompt, payload))
        if self.config.model_name:
            argv += ["--model", self.config.model_name]
        argv.extend(self._build_tool_args())
        return argv

    def _build_tool_args(self) -> list[str]:
        argv: list[str] = []
        for tool in self.config.allowed_tools:
            argv += ["--allowedTools", tool]
        for tool in self.config.disallowed_tools:
            argv += ["--disallowedTools", tool]
        return argv

    def _build_prompt(self) -> str:
        return self.config.analysis_prompt_template.strip() or _DEFAULT_PROMPT

    def _build_stdin(self, prompt: str, payload: AnalyzerInput) -> bytes:
        if self.config.prompt_via == "stdin":
            return self._build_prompt_payload(prompt, payload).encode("utf-8")
        return b""

    async def analyze(self, payload: AnalyzerInput) -> AnalyzerResult:
        prompt = self._build_prompt()
        argv = self._build_argv(prompt, payload)
        stdin_bytes = self._build_stdin(prompt, payload)

        logger.debug("claude exec: %s", " ".join(argv))
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.config.ccr_env,
            )
        except FileNotFoundError as exc:
            raise AnalyzerError(
                f"未找到 Claude Code CLI 可执行文件：{self.config.command}",
                retriable=False,
            ) from exc

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=stdin_bytes),
                timeout=self.config.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise AnalyzerError(
                f"Claude Code CLI 子进程超时 (> {self.config.timeout_seconds:.0f}s)",
                retriable=True,
            ) from exc

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        rc = proc.returncode

        if rc != 0:
            if _looks_like_rate_limit(stderr) or _looks_like_rate_limit(stdout):
                raise AnalyzerRateLimited(
                    f"Claude Code CLI 限流/额度不足 (rc={rc}): {stderr.strip()[:200]}",
                    raw_stdout=stdout,
                    raw_stderr=stderr,
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
                raise AnalyzerRateLimited(
                    "Claude Code CLI 输出疑似限流/额度不足",
                    raw_stdout=stdout,
                    raw_stderr=stderr,
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
            raise AnalyzerError(
                f"Claude Code CLI JSON 解析失败: {exc}",
                retriable=False,
                raw_stdout=stdout,
                raw_stderr=stderr,
            ) from exc
        if not isinstance(data, dict):
            raise AnalyzerError(
                "Claude Code CLI JSON 顶层不是对象",
                retriable=False,
                raw_stdout=stdout,
                raw_stderr=stderr,
            )
        return _coerce_result(data)
