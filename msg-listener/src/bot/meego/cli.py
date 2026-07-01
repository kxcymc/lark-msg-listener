"""异步 CLI 调用工具，供 Meego 需求收集模块复用。

封装 `lark-cli` / `meegle` 等外部命令的子进程调用与 JSON 解析，
替代此前用独立脚本承载的 `run_json_command` 逻辑。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)
_MEEGLE_AUTH_LOCKS: dict[str, asyncio.Lock] = {}


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    returncode: int
    stdout: str
    stderr: str
    data: Any | None = None
    timed_out: bool = False


async def run_json_command(
    command: list[str],
    *,
    env_overrides: dict[str, str] | None = None,
    timeout_seconds: float | None = 45.0,
) -> CommandResult:
    """运行命令并尝试将其 stdout/stderr 解析为 JSON。"""
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    if _needs_meegle_auth(command):
        auth_error = await _ensure_meegle_authenticated(env)
        if auth_error:
            return CommandResult(
                ok=False,
                returncode=1,
                stdout="",
                stderr=auth_error,
                data=None,
            )
    return await _run_command(command, env=env, timeout_seconds=timeout_seconds)


async def _run_command(
    command: list[str],
    *,
    env: dict[str, str],
    timeout_seconds: float | None,
) -> CommandResult:
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    timed_out = False
    try:
        stdout_raw, stderr_raw = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_seconds,
        )
        returncode = proc.returncode or 0
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return CommandResult(
            ok=False,
            returncode=124,
            stdout="",
            stderr=f"command timed out after {timeout_seconds}s",
            data=None,
            timed_out=True,
        )

    stdout = stdout_raw.decode("utf-8", errors="replace").strip()
    stderr = stderr_raw.decode("utf-8", errors="replace").strip()
    data = _first_json(stdout, stderr)
    return CommandResult(
        ok=returncode == 0,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        data=data,
        timed_out=timed_out,
    )


def _needs_meegle_auth(command: list[str]) -> bool:
    if not command or command[0] != "meegle":
        return False
    if len(command) == 1:
        return False
    top = command[1]
    if top in {"auth", "config", "help", "--help", "version"}:
        return False
    if top == "url" and len(command) > 2 and command[2] == "decode":
        return False
    return True


def _meegle_host_from_env(env: dict[str, str]) -> str:
    return (env.get("MEEGLE_HOST") or "").strip()


def _meegle_auth_lock(host: str) -> asyncio.Lock:
    lock = _MEEGLE_AUTH_LOCKS.get(host)
    if lock is None:
        lock = asyncio.Lock()
        _MEEGLE_AUTH_LOCKS[host] = lock
    return lock


def _meegle_status_expired(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return True
    if payload.get("authenticated") is not True:
        return True
    expires_in = payload.get("expires_in_minutes")
    if isinstance(expires_in, (int, float)) and expires_in <= 0:
        return True
    return False


async def _ensure_meegle_authenticated(env: dict[str, str]) -> str | None:
    host = _meegle_host_from_env(env)
    if not host:
        return "meegle host 未配置，请先填写 config.toml 中的 meego_requirement_host。"
    async with _meegle_auth_lock(host):
        status = await _run_command(
            ["meegle", "auth", "status", "--format", "json"],
            env=env,
            timeout_seconds=15.0,
        )
        status_payload = status.data if isinstance(status.data, dict) else None
        if status.ok and not _meegle_status_expired(status_payload):
            return None

        logger.warning(
            "检测到 meegle 登录态不可用或已过期，准备重新登录 host=%s status=%s stderr=%s",
            host,
            status_payload,
            status.stderr,
        )
        login_rc = await _run_meegle_login(host=host, env=env)
        if login_rc != 0:
            return (
                f"meegle 登录已过期，自动重新登录失败（host={host}, returncode={login_rc}）。"
                f" 请执行 `meegle auth login --device-code --host {host}` 后重试。"
            )

        verify = await _run_command(
            ["meegle", "auth", "status", "--format", "json"],
            env=env,
            timeout_seconds=15.0,
        )
        verify_payload = verify.data if isinstance(verify.data, dict) else None
        if verify.ok and not _meegle_status_expired(verify_payload):
            logger.info("meegle 重新登录成功 host=%s expires_in_minutes=%s", host, verify_payload.get("expires_in_minutes"))
            return None
        return (
            f"meegle 登录状态校验失败（host={host}）。"
            f" 请执行 `meegle auth login --device-code --host {host}` 后重试。"
        )


async def _run_meegle_login(*, host: str, env: dict[str, str]) -> int:
    logger.warning("开始重新登录 meegle host=%s", host)
    proc = await asyncio.create_subprocess_exec(
        "meegle",
        "auth",
        "login",
        "--device-code",
        "--host",
        host,
        env=env,
    )
    return int(await proc.wait())


def _first_json(*candidates: str) -> Any | None:
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None
