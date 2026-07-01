"""claude_code_cli 独立 worker 入口。"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .models import (
    CLAUDE_CODE_CLI_DISALLOWED_TOOLS,
    CLAUDE_CODE_CLI_PERMISSION_MODE,
    ERROR_CLAUDE_CLI_FAILED,
    ERROR_CLAUDE_NOT_FOUND,
    ERROR_DIRTY_WORKTREE,
    ERROR_INVALID_JSON_OUTPUT,
    ERROR_PERMISSION_DENIED,
    ERROR_REPO_NOT_FOUND,
)
from ....common.ccr_gateway import CcrGatewayConfig, build_ccr_env

ERROR_CLAUDE_CLI_TIMEOUT = "claude_cli_timeout"


def main() -> int:
    parser = argparse.ArgumentParser(prog="claude-code-cli-worker")
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--stdout", required=True)
    parser.add_argument("--stderr", required=True)
    args = parser.parse_args()

    metadata_path = Path(args.metadata)
    result_path = Path(args.result)
    stdout_path = Path(args.stdout)
    stderr_path = Path(args.stderr)
    metadata = _read_json(metadata_path)
    try:
        result = _run(metadata=metadata, stdout_path=stdout_path, stderr_path=stderr_path)
    except Exception as exc:  # noqa: BLE001
        result = _failed(
            error=ERROR_CLAUDE_CLI_FAILED,
            message=f"{type(exc).__name__}: {exc}",
            metadata=metadata,
        )
    _write_json_atomic(result_path, result)
    return 0 if result.get("status") == "succeeded" else 1


def _run(*, metadata: dict[str, Any], stdout_path: Path, stderr_path: Path) -> dict[str, Any]:
    repo_path = Path(str(metadata.get("repo_local_path") or "")).expanduser()
    if not repo_path.is_dir():
        return _failed(ERROR_REPO_NOT_FOUND, f"repo path not found: {repo_path}", metadata)
    if not (repo_path / ".git").exists():
        return _failed(ERROR_REPO_NOT_FOUND, f"not a git repository: {repo_path}", metadata)
    dirty = _run_command(["git", "status", "--porcelain"], cwd=repo_path)
    if dirty.returncode != 0:
        return _failed(ERROR_CLAUDE_CLI_FAILED, dirty.stderr[-1000:], metadata)
    if dirty.stdout.strip():
        return _failed(ERROR_DIRTY_WORKTREE, dirty.stdout[:1000], metadata)

    base_branch = str(metadata.get("base_branch") or "").strip()
    if base_branch:
        checkout_base = _run_command(["git", "checkout", base_branch], cwd=repo_path)
        if checkout_base.returncode != 0:
            return _failed(ERROR_CLAUDE_CLI_FAILED, checkout_base.stderr[-1000:], metadata)

    command = str(metadata.get("command") or "claude")
    if shutil.which(command) is None:
        return _failed(ERROR_CLAUDE_NOT_FOUND, f"command not found: {command}", metadata)
    cli_cmd = _build_cli_command(metadata)
    prompt = str(metadata.get("prompt") or "")
    with stdout_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open("w", encoding="utf-8") as stderr_file:
        # 由 executor 写入的 metadata 还原 CCR 配置，把 claude 请求路由到网关；
        # ccr_enabled=false 时 build_ccr_env 返回不含 CCR 变量的副本，等价直连。
        run_env = build_ccr_env(
            CcrGatewayConfig(
                enabled=bool(metadata.get("ccr_enabled", True)),
                base_url=str(metadata.get("ccr_base_url") or ""),
            ),
            base_env={**os.environ, "NO_COLOR": "1"},
        )
        try:
            proc = subprocess.run(
                cli_cmd,
                input=prompt,
                cwd=str(repo_path),
                text=True,
                stdout=stdout_file,
                stderr=stderr_file,
                timeout=_cli_timeout(metadata),
                env=run_env,
            )
        except subprocess.TimeoutExpired:
            stdout_file.write(f"\nClaude CLI timed out at {time.time()}\n")
            return _failed(
                ERROR_CLAUDE_CLI_TIMEOUT,
                f"timeout_seconds={_cli_timeout(metadata)}",
                metadata,
            )
    stdout_text = _safe_read(stdout_path)
    stderr_text = _safe_read(stderr_path)
    if proc.returncode != 0:
        payload = _extract_json_object(stdout_text)
        if payload is not None:
            permission_error = _extract_permission_denied_error(payload)
            if permission_error:
                return _failed(
                    ERROR_PERMISSION_DENIED,
                    permission_error,
                    metadata,
                    returncode=proc.returncode,
                )
            payload = _normalize_cli_payload(payload)
            permission_error = _extract_permission_denied_error(payload)
            if permission_error:
                return _failed(
                    ERROR_PERMISSION_DENIED,
                    permission_error,
                    metadata,
                    returncode=proc.returncode,
                )
            error_msg = str(payload.get("error_msg") or payload.get("result") or "")
            if error_msg:
                return _failed(
                    ERROR_CLAUDE_CLI_FAILED,
                    error_msg,
                    metadata,
                    returncode=proc.returncode,
                )
        return _failed(
            ERROR_CLAUDE_CLI_FAILED,
            stderr_text[-2000:] or stdout_text[-2000:],
            metadata,
            returncode=proc.returncode,
        )
    payload = _extract_json_object(stdout_text)
    if payload is None:
        return _failed(
            ERROR_INVALID_JSON_OUTPUT,
            stdout_text[-2000:],
            metadata,
            returncode=proc.returncode,
        )
    permission_error = _extract_permission_denied_error(payload)
    if permission_error:
        return _failed(
            ERROR_PERMISSION_DENIED,
            permission_error,
            metadata,
            returncode=proc.returncode,
        )
    payload = _normalize_cli_payload(payload)
    permission_error = _extract_permission_denied_error(payload)
    if permission_error:
        return _failed(
            ERROR_PERMISSION_DENIED,
            permission_error,
            metadata,
            returncode=proc.returncode,
        )
    if payload.get("error_msg"):
        return _failed(
            ERROR_CLAUDE_CLI_FAILED,
            str(payload.get("error_msg")),
            metadata,
            returncode=proc.returncode,
        )
    changed = _changed_files(repo_path)
    return {
        "status": "succeeded",
        "returncode": proc.returncode,
        "work_branch": str(payload.get("work_branch") or _current_branch(repo_path)),
        "changed_files": payload.get("changed_files") if isinstance(payload.get("changed_files"), list) else changed,
        "commit_sha": _head_sha(repo_path),
        "summary": str(payload.get("summary") or ""),
        "error": "",
    }


def _build_cli_command(metadata: dict[str, Any]) -> list[str]:
    cmd = [
        str(metadata.get("command") or "claude"),
        "-p",
        "--permission-mode",
        CLAUDE_CODE_CLI_PERMISSION_MODE,
    ]
    model = str(metadata.get("model") or "")
    if model:
        cmd.extend(["--model", model])
    output_format = str(metadata.get("output_format") or "")
    if output_format:
        cmd.extend(["--output-format", output_format])
    for tool in CLAUDE_CODE_CLI_DISALLOWED_TOOLS:
        cmd.extend(["--disallowedTools", tool])
    return cmd


def _cli_timeout(metadata: dict[str, Any]) -> float | None:
    raw = metadata.get("cli_timeout_seconds")
    if raw in (None, "", 0, "0"):
        return None
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return None


def _normalize_cli_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("result"), str):
        nested = _extract_json_object(str(payload.get("result") or ""))
        if nested is not None:
            return nested
    return payload


def _extract_permission_denied_error(payload: dict[str, Any]) -> str:
    denials = payload.get("permission_denials")
    if not isinstance(denials, list) or not denials:
        return ""
    lines: list[str] = []
    for item in denials[:3]:
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool_name") or "tool").strip() or "tool"
        tool_input = item.get("tool_input")
        command = ""
        if isinstance(tool_input, dict):
            command = str(
                tool_input.get("command")
                or tool_input.get("description")
                or tool_input.get("path")
                or ""
            ).strip()
        lines.append(f"{tool_name}: {command}" if command else tool_name)
    summary = "; ".join(lines) if lines else "permission denied"
    result_text = str(payload.get("result") or "").strip()
    if result_text:
        return f"{summary}; {result_text[:400]}"
    return summary


def _run_command(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, timeout=60)


def _changed_files(repo_path: Path) -> list[str]:
    result = _run_command(["git", "status", "--porcelain"], cwd=repo_path)
    if result.returncode != 0:
        return []
    files: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) > 3:
            files.append(line[3:].strip())
    return files


def _current_branch(repo_path: Path) -> str:
    result = _run_command(["git", "branch", "--show-current"], cwd=repo_path)
    return result.stdout.strip() if result.returncode == 0 else ""


def _head_sha(repo_path: Path) -> str:
    result = _run_command(["git", "rev-parse", "HEAD"], cwd=repo_path)
    return result.stdout.strip() if result.returncode == 0 else ""


def _extract_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for idx, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _failed(
    error: str,
    message: str,
    metadata: dict[str, Any],
    *,
    returncode: int = 1,
) -> dict[str, Any]:
    return {
        "status": "failed",
        "returncode": returncode,
        "work_branch": str(metadata.get("work_branch_hint") or ""),
        "changed_files": [],
        "commit_sha": "",
        "summary": "",
        "error": error if not message else f"{error}: {message[:1000]}",
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


if __name__ == "__main__":
    sys.exit(main())
