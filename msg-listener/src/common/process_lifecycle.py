from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import Iterable


DEFAULT_SIGNAL_SET = tuple(
    sig
    for sig in (
        getattr(signal, "SIGINT", None),
        getattr(signal, "SIGTERM", None),
        getattr(signal, "SIGHUP", None),
        getattr(signal, "SIGQUIT", None),
        getattr(signal, "SIGBREAK", None),  # Windows 上 Ctrl-Break / taskkill 触发
    )
    if sig is not None
)


def log_process_started(logger: logging.Logger, service_name: str) -> None:
    logger.info(
        "%s 启动，pid=%s ppid=%s",
        service_name,
        os.getpid(),
        os.getppid(),
    )


def parent_process_changed(initial_parent_pid: int) -> bool:
    if initial_parent_pid <= 1:
        return False
    current_ppid = os.getppid()
    if current_ppid != initial_parent_pid:
        return True
    # Windows 上 PPID 在父进程退出后仍保留原值（且可能被复用），
    # 因此再做一次"父进程是否还活着"的校验，避免漏掉孤儿场景。
    if os.name == "nt":
        return not _windows_pid_alive(current_ppid)
    return False


def _windows_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import subprocess

        res = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, Exception):  # noqa: BLE001
        # 探测失败时回退到 True（避免误杀）
        return True
    return str(pid) in (res.stdout or "")


def install_signal_handlers(
    signals: Iterable[signal.Signals],
    handler,
) -> None:
    for sig in signals:
        try:
            signal.signal(sig, handler)
        except (OSError, RuntimeError, ValueError):
            continue


def install_asyncio_shutdown_handlers(
    *,
    logger: logging.Logger,
    service_name: str,
    shutdown_event: asyncio.Event,
    signals: Iterable[signal.Signals] = DEFAULT_SIGNAL_SET,
) -> None:
    loop = asyncio.get_running_loop()

    def request_shutdown(sig: signal.Signals) -> None:
        if shutdown_event.is_set():
            return
        logger.info("%s 收到信号 %s，准备退出", service_name, sig)
        shutdown_event.set()

    for sig in signals:
        try:
            signal.signal(
                sig,
                lambda _signum, _frame, _sig=sig: _request_shutdown_threadsafe(
                    loop=loop,
                    request_shutdown=request_shutdown,
                    sig=_sig,
                ),
            )
        except (OSError, RuntimeError, ValueError):
            continue


def _request_shutdown_threadsafe(
    *,
    loop: asyncio.AbstractEventLoop,
    request_shutdown,
    sig: signal.Signals,
) -> None:
    try:
        loop.call_soon_threadsafe(request_shutdown, sig)
    except RuntimeError:
        request_shutdown(sig)


async def watch_parent_process(
    *,
    logger: logging.Logger,
    service_name: str,
    initial_parent_pid: int,
    shutdown_event: asyncio.Event,
    poll_interval: float = 1.0,
) -> None:
    if initial_parent_pid <= 1:
        return
    while not shutdown_event.is_set():
        await asyncio.sleep(poll_interval)
        current_parent_pid = os.getppid()
        if current_parent_pid == initial_parent_pid:
            continue
        logger.warning(
            "%s 检测到父进程已变更，old_ppid=%s current_ppid=%s，准备退出",
            service_name,
            initial_parent_pid,
            current_parent_pid,
        )
        shutdown_event.set()
        return
