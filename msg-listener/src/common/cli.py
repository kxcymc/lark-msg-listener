"""lark-cli 子进程通用封装。

约定：
- 默认带 `--format json --as <identity>` 调用，stdout 解析 JSON 后返回；
- stderr 仅作日志，错误信息上抛 CLIError。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .lark_app_config import lark_cli_subprocess_env

logger = logging.getLogger(__name__)


class CLIError(RuntimeError):
    """lark-cli 子进程异常。"""

    def __init__(self, message: str, *, returncode: int | None = None, stderr: str = "", stdout: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


async def run(
    args: list[str],
    *,
    identity: str | None = "user",
    parse_json: bool = True,
    auto_format: bool = True,
    timeout: float | None = 60.0,
    cwd: str | None = None,
) -> Any:
    """执行 `lark-cli <args>` 并返回 JSON 解析结果。

    Parameters
    ----------
    args:
        位于 `lark-cli` 之后的命令参数（不含 `--format json` / `--as`）
    identity:
        身份；None 表示不附加 `--as` 参数（如 `auth status` / `config show`）
    parse_json:
        是否解析 JSON；False 时返回原始 stdout 字符串
    auto_format:
        是否自动追加 `--format json`。对部分子命令（如 `auth status` /
        `config show`）lark-cli 未声明该 flag，cobra 会拒绝并返回非零；
        这类命令默认 stdout 已经是 JSON，调用时设为 False 即可。
    timeout:
        子进程超时秒数；None 表示不限时（仅供长任务使用）
    cwd:
        子进程工作目录；用于 lark-cli 要求文件参数为相对路径的场景。
    """
    cmd: list[str] = ["lark-cli", *args]
    added_format = False
    if parse_json and auto_format and "--format" not in args:
        cmd += ["--format", "json"]
        added_format = True
    if identity and "--as" not in args:
        cmd += ["--as", identity]

    async def exec_cmd(command: list[str]) -> tuple[int | None, str, str]:
        logger.debug("lark-cli exec: %s", " ".join(command))
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=lark_cli_subprocess_env(),
        )
        try:
            if timeout is None:
                stdout_b, stderr_b = await proc.communicate()
            else:
                stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise CLIError(f"lark-cli 子进程超时: {' '.join(command)}") from exc
        stdout_text = stdout_b.decode("utf-8", errors="replace")
        stderr_text = stderr_b.decode("utf-8", errors="replace")
        return proc.returncode, stdout_text, stderr_text

    returncode, stdout, stderr = await exec_cmd(cmd)
    if returncode != 0 and added_format and "unknown flag: --format" in stderr:
        fallback_cmd = cmd.copy()
        format_index = fallback_cmd.index("--format")
        del fallback_cmd[format_index : format_index + 2]
        logger.debug("lark-cli retry without --format: %s", " ".join(fallback_cmd))
        returncode, stdout, stderr = await exec_cmd(fallback_cmd)

    if returncode != 0:
        raise CLIError(
            f"lark-cli 失败 (rc={returncode}): {stderr.strip()}",
            returncode=returncode,
            stderr=stderr,
            stdout=stdout,
        )

    if not parse_json:
        return stdout

    stripped = stdout.strip()
    if not stripped:
        return {}
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise CLIError(
            f"lark-cli 输出无法解析为 JSON: {exc}",
            returncode=returncode,
            stderr=stderr,
            stdout=stdout,
        ) from exc
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        return data["data"]
    return data
