"""启动前权限预检。

user 权限可以通过 `lark-cli auth login --scope ...` 增量授权。
"""
from __future__ import annotations

import asyncio
import json
import logging

from ..common import cli
from ..common.lark_app_config import lark_cli_subprocess_env

logger = logging.getLogger(__name__)


REQUIRED_USER_SCOPES = [
    "search:message",
    "im:message.group_msg:get_as_user",
    "im:message.p2p_msg:get_as_user",
    "im:message:readonly",
    "contact:user.basic_profile:readonly",
    "contact:user.base:readonly",
]


class PermissionCheckError(RuntimeError):
    """权限预检失败。"""


def _parse_json_object(text: str) -> dict:
    try:
        data = json.loads(text.strip() or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


async def ensure_permissions() -> None:
    """确保启动所需权限就绪；不能自动申请的权限会抛出可读错误。"""
    logger.info("开始权限预检")
    await _ensure_user_scopes()
    logger.info("权限预检通过")


async def _ensure_user_scopes() -> None:
    scopes = " ".join(REQUIRED_USER_SCOPES)
    retry_interval = 10.0
    while True:
        try:
            missing = await _check_user_scopes(scopes)
        except PermissionCheckError as exc:
            logger.warning("user 权限检查暂不可用：%s；%.0f 秒后继续重试", exc, retry_interval)
            await asyncio.sleep(retry_interval)
            continue
        if not missing:
            logger.info("user 权限已满足")
            return

        missing_scopes = " ".join(missing)
        logger.warning("缺少 user 权限：%s", missing_scopes)
        logger.warning("开始发起用户授权：lark-cli auth login --scope %r", missing_scopes)
        await _run_auth_login_until_success(missing_scopes)

        try:
            missing_after_login = await _check_user_scopes(scopes)
        except PermissionCheckError as exc:
            logger.warning("user 权限复检暂不可用：%s；%.0f 秒后继续重试", exc, retry_interval)
            await asyncio.sleep(retry_interval)
            continue
        if not missing_after_login:
            logger.info("user 权限授权完成")
            return
        logger.warning(
            "用户授权后仍缺少权限：%s。请确认应用后台已开通这些 scopes；%.0f 秒后继续重试。",
            " ".join(missing_after_login),
            retry_interval,
        )
        await asyncio.sleep(retry_interval)


async def _check_user_scopes(scopes: str) -> list[str]:
    try:
        data = await cli.run(
            ["auth", "check", "--scope", scopes],
            identity=None,
            auto_format=False,
        )
    except cli.CLIError as exc:
        data = _parse_json_object(exc.stdout)
        missing = data.get("missing")
        if isinstance(missing, list):
            return [str(s) for s in missing if s]
        raise PermissionCheckError(
            "无法检查 user 权限："
            f"{exc.stderr.strip() or exc.stdout.strip() or exc}"
        ) from exc

    missing = (data or {}).get("missing") if isinstance(data, dict) else []
    return [str(s) for s in missing if s] if isinstance(missing, list) else []


async def _run_auth_login_until_success(scopes: str) -> None:
    retry_interval = 5.0
    while True:
        rc = await _run_auth_login_once(scopes)
        if rc == 0:
            return
        logger.warning(
            "`lark-cli auth login` 未完成或已超时，退出码=%s；%.0f 秒后重新发起授权",
            rc,
            retry_interval,
        )
        await asyncio.sleep(retry_interval)


async def _run_auth_login_once(scopes: str) -> int:
    proc = await asyncio.create_subprocess_exec(
        "lark-cli",
        "auth",
        "login",
        "--scope",
        scopes,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=lark_cli_subprocess_env(),
    )
    stdout_task = asyncio.create_task(_log_stream(proc.stdout, logging.INFO))
    stderr_task = asyncio.create_task(_log_stream(proc.stderr, logging.WARNING))
    try:
        rc = await proc.wait()
    finally:
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
    return rc


async def _log_stream(stream: asyncio.StreamReader | None, level: int) -> None:
    if stream is None:
        return
    async for line in stream:
        text = line.decode("utf-8", errors="replace").rstrip()
        if text:
            logger.log(level, "%s", text)
