"""Trae side chat launcher shared by card scenes and dev task executors."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import quote, unquote

from .notifier import NotifierError

logger = logging.getLogger(__name__)

TRAE_SIDE_CHAT_URL_PREFIX = "trae-cn://trae.ai-ide/side-chat?query="
# Avoid passing huge prompt text through the protocol URL, which may be truncated.
TRAE_SIDE_CHAT_URL_QUERY_LIMIT = 1800
CLIPBOARD_FALLBACK_TEXT = "需求 prompt 已复制到剪贴板，如打开失败，可自行粘贴。"
# 打开新工作区窗口后、投递侧聊 query 前的等待秒数。
# side-chat 协议不携带窗口标识，会落到当前聚焦窗口；等待不足会导致 query
# 被旧窗口接走，必须留足新窗口加载并聚焦的时间。可经配置调大。
DEFAULT_WORKSPACE_OPEN_WAIT_SECONDS = 3.0
_workspace_open_wait_seconds = DEFAULT_WORKSPACE_OPEN_WAIT_SECONDS


def set_workspace_open_wait_seconds(seconds: float) -> None:
    """Override the wait between opening a workspace window and seeding side chat."""
    global _workspace_open_wait_seconds
    if seconds > 0:
        _workspace_open_wait_seconds = float(seconds)


async def open_trae_side_chat(prompt: str, *, repo_path: str = "") -> None:
    """Open Trae side chat for an optional workspace and seed it with prompt text."""
    logger.debug(
        "open_trae_side_chat repo_path=%r prompt_len=%d prompt_repr=%r",
        repo_path,
        len(prompt),
        prompt,
    )
    if repo_path:
        await copy_to_local_clipboard(prompt)
        await open_workspace(repo_path)
        await asyncio.sleep(_workspace_open_wait_seconds)
        await open_url(build_trae_side_chat_url(prompt))
        return

    if sys.platform == "darwin":
        await copy_to_local_clipboard(prompt)
        side_chat_url = build_trae_side_chat_url(prompt)
        if len(side_chat_url) <= TRAE_SIDE_CHAT_URL_QUERY_LIMIT:
            await open_url(side_chat_url)
            return
        await open_url(build_trae_side_chat_url(CLIPBOARD_FALLBACK_TEXT))
        return

    await open_url(build_trae_side_chat_url(prompt))


async def copy_to_local_clipboard(text: str) -> None:
    if sys.platform == "darwin":
        await _copy_to_macos_clipboard(text)
        return
    if os.name == "nt":
        await _copy_to_windows_clipboard(text)
        return
    await _copy_to_linux_clipboard(text)


async def open_workspace(repo_path: str) -> None:
    path = Path(repo_path).expanduser()
    if not path.is_dir():
        raise NotifierError(f"所选本地仓库不存在：{repo_path}")
    await open_url(build_trae_workspace_url(repo_path))


async def open_url(url: str) -> None:
    if os.name == "nt":
        startfile = getattr(os, "startfile", None)
        if not callable(startfile):
            raise NotifierError("当前 Windows 环境不支持 os.startfile 打开 Trae URL")
        await asyncio.to_thread(startfile, url)
        return

    command = ["open", url] if sys.platform == "darwin" else ["xdg-open", url]
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise NotifierError(f"无法打开 Trae URL，缺少命令：{command[0]}") from exc
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        raise NotifierError(f"打开 Trae URL 失败：{err or proc.returncode}")


def build_trae_workspace_url(repo_path: str, *, query: str = "") -> str:
    path = Path(repo_path).expanduser()
    if not path.is_dir():
        raise NotifierError(f"所选本地仓库不存在：{repo_path}")
    path_text = str(path.resolve())
    if os.name == "nt":
        path_text = path_text.replace("\\", "/")
    quoted_path = quote(path_text, safe="/:")
    url = f"trae-cn://file/{quoted_path}?windowId=_blank"
    if query:
        url += f"&query={quote(query, safe='')}"
    return url


def build_trae_side_chat_url(prompt: str) -> str:
    # Trae 解析 side-chat URL 时，会先把整个 query 值做一次 URL 解码，再按 `&` 切分参数。
    # 若 prompt 中含 `&`（如 figma 链接的 `?node-id=...&t=...`），经 quote 编码成 `%26`、
    # 被 Trae 解码还原成 `&` 后，会被当作参数分隔符，导致 `&` 之后的内容（含结尾固定文案）
    # 被整体丢弃。为此先把 `&` 手动转义为 `%26`，再整体 quote（`%`→`%25`，即 `&`→`%2526`），
    # 这样 Trae 解码一次后得到的是 `%26` 而非 `&`，不会触发参数切分，prompt 才能完整带入。
    escaped = prompt.replace("&", "%26")
    url = TRAE_SIDE_CHAT_URL_PREFIX + quote(escaped, safe="")
    logger.debug(
        "build_trae_side_chat_url url_len=%d query_decoded=%r",
        len(url),
        unquote(url[len(TRAE_SIDE_CHAT_URL_PREFIX):]),
    )
    return url


async def _copy_to_macos_clipboard(text: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "pbcopy",
        stdin=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(proc.communicate(text.encode("utf-8")), timeout=10.0)
    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        raise NotifierError(f"写入 macOS 剪贴板失败：{err or proc.returncode}")


async def _copy_to_windows_clipboard(text: str) -> None:
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Set-Clipboard -Value ([Console]::In.ReadToEnd())",
    ]
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(proc.communicate(text.encode("utf-8")), timeout=10.0)
    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        raise NotifierError(f"写入 Windows 剪贴板失败：{err or proc.returncode}")


async def _copy_to_linux_clipboard(text: str) -> None:
    candidates = [
        ("wl-copy", []),
        ("xclip", ["-selection", "clipboard"]),
        ("xsel", ["--clipboard", "--input"]),
    ]
    for command, args in candidates:
        if not shutil.which(command):
            continue
        proc = await asyncio.create_subprocess_exec(
            command,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(text.encode("utf-8")), timeout=10.0)
        if proc.returncode == 0:
            return
        err = stderr.decode("utf-8", errors="replace").strip()
        logger.warning("写入 Linux 剪贴板失败 command=%s error=%s", command, err or proc.returncode)
    raise NotifierError("当前 Linux 环境缺少剪贴板命令：wl-copy / xclip / xsel")
