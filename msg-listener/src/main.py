"""统一启动入口：拉起 collector 和 bot 两个子进程。"""
from __future__ import annotations

import atexit
import asyncio
import logging
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from .common.process_lifecycle import (
    DEFAULT_SIGNAL_SET,
    install_signal_handlers,
    log_process_started,
    parent_process_changed,
)
from .common.logging_setup import configure_logging
from .common.filter import OwnerCache
from .common.lark_app_config import lark_cli_subprocess_env

PROJECT_ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger("msg-listener-main")

# 单聊服务就绪标志的路径，与 src/bot/main.py 中 BOT_READY_STAMP_PATH 保持一致。
BOT_READY_STAMP_PATH = PROJECT_ROOT / "cache" / "bot.ready"
# 等待单聊服务就绪的最大时长（秒）；超时仅警告并继续启动 collector，避免完全卡死。
BOT_READY_WAIT_TIMEOUT = 120.0
BOT_READY_POLL_INTERVAL = 0.2

# 由 bot 进程在 /config 写回成功后写入；supervisor 主循环检测到即触发原地重启。
# 跨平台触发：仅依赖文件存在性，无需信号。
RESTART_REQUEST_PATH = PROJECT_ROOT / "cache" / "restart.request"

# mywork 内置周调度：周五 17:30 由 supervisor 拉起一次一次性任务。
MYWORK_SCHEDULE_WEEKDAY = 4
MYWORK_SCHEDULE_HOUR = 17
MYWORK_SCHEDULE_MINUTE = 30


def _clear_bot_ready_stamp() -> None:
    try:
        BOT_READY_STAMP_PATH.unlink(missing_ok=True)
    except OSError as exc:  # pragma: no cover
        logger.debug("清理 bot.ready 失败：%s", exc)


def _clear_restart_request() -> None:
    try:
        RESTART_REQUEST_PATH.unlink(missing_ok=True)
    except OSError as exc:  # pragma: no cover
        logger.debug("清理 restart.request 失败：%s", exc)


def _wait_for_bot_ready(bot_proc: subprocess.Popen, timeout: float) -> bool:
    """轮询等待 bot 写入 ready stamp；若 bot 中途退出则返回 False。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if bot_proc.poll() is not None:
            logger.error("单聊服务在就绪前已退出，exit_code=%s", bot_proc.returncode)
            return False
        if BOT_READY_STAMP_PATH.exists():
            return True
        time.sleep(BOT_READY_POLL_INTERVAL)
    return BOT_READY_STAMP_PATH.exists()


async def _resolve_owner_env(cache_dir: Path) -> dict[str, str]:
    owner = OwnerCache(cache_dir)
    owner_open_id = await owner.load_or_fetch()
    logger.info("owner_open_id = %s", owner_open_id)
    env = {"MSG_LISTENER_OWNER_OPEN_ID": owner_open_id}
    if owner.name:
        env["MSG_LISTENER_OWNER_NAME"] = owner.name
    return env


def _enumerate_processes() -> list[tuple[int, str]]:
    """跨平台枚举 (pid, command_line)。"""
    if os.name == "nt":
        # 使用 wmic 拉取完整命令行（CSV 输出）。
        try:
            res = subprocess.run(
                ["wmic", "process", "get", "ProcessId,CommandLine", "/FORMAT:CSV"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return []
        if res.returncode != 0:
            return []
        results: list[tuple[int, str]] = []
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line or line.lower().startswith("node"):
                continue
            # CSV: Node,CommandLine,ProcessId
            parts = line.split(",")
            if len(parts) < 3:
                continue
            pid_str = parts[-1].strip()
            try:
                pid = int(pid_str)
            except ValueError:
                continue
            command = ",".join(parts[1:-1]).strip()
            results.append((pid, command))
        return results
    try:
        res = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    if res.returncode != 0:
        return []
    results = []
    for line in res.stdout.splitlines():
        text = line.strip()
        if not text:
            continue
        parts = text.split(maxsplit=1)
        if not parts:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        command = parts[1] if len(parts) > 1 else ""
        results.append((pid, command))
    return results


def _process_alive(pid: int) -> bool:
    """跨平台探活：返回 True 表示进程仍存活。"""
    if os.name == "nt":
        try:
            res = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False
        return str(pid) in (res.stdout or "")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        # 权限不足等情况按存活处理。
        return True
    return True


def _terminate_pid(pid: int, *, force: bool = False) -> None:
    """跨平台终止指定 PID。"""
    if os.name == "nt":
        cmd = ["taskkill", "/PID", str(pid)]
        if force:
            cmd.insert(1, "/F")
        try:
            subprocess.run(cmd, capture_output=True, timeout=5)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return
    sig = getattr(signal, "SIGKILL", signal.SIGTERM) if force else signal.SIGTERM
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass
    except OSError:
        pass


def _kill_stale_processes(patterns: list[str], timeout: float = 5.0) -> None:
    """Terminate old service subprocesses so card callbacks have a single owner."""
    current_pid = os.getpid()
    parent_pid = os.getppid()
    pids: set[int] = set()
    compiled = [re.compile(pattern) for pattern in patterns if pattern]
    for pid, command in _enumerate_processes():
        if pid in {current_pid, parent_pid}:
            continue
        if any(pattern.search(command) for pattern in compiled):
            pids.add(pid)
    if not pids:
        return
    print(f"[main] 清理历史服务进程：{sorted(pids)}", flush=True)
    for pid in sorted(pids):
        _terminate_pid(pid, force=False)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        alive = [pid for pid in pids if _process_alive(pid)]
        if not alive:
            return
        time.sleep(0.2)
    for pid in sorted(pids):
        _terminate_pid(pid, force=True)
    deadline = time.monotonic() + max(1.0, timeout / 2)
    while time.monotonic() < deadline:
        alive = [pid for pid in pids if _process_alive(pid)]
        if not alive:
            return
        time.sleep(0.1)


def _stop_processes(processes: list[subprocess.Popen], timeout: float = 10.0) -> None:
    running = [proc for proc in processes if proc.poll() is None]
    for proc in running:
        proc.terminate()

    deadline = time.monotonic() + timeout
    for proc in running:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                logger.error("子进程强杀后仍未退出 pid=%s", proc.pid)


def _next_mywork_run_after(now: datetime) -> datetime:
    target = now.replace(
        hour=MYWORK_SCHEDULE_HOUR,
        minute=MYWORK_SCHEDULE_MINUTE,
        second=0,
        microsecond=0,
    )
    days = (MYWORK_SCHEDULE_WEEKDAY - now.weekday()) % 7
    target += timedelta(days=days)
    if target <= now:
        target += timedelta(days=7)
    return target


def _start_mywork(env: dict[str, str], cmd: list[str]) -> subprocess.Popen:
    logger.info("启动 mywork 周任务：%s", " ".join(cmd))
    return subprocess.Popen(cmd, env=env)


def _start_services(
    env: dict[str, str],
    bot_cmd: list[str],
    collector_cmd: list[str],
) -> tuple[subprocess.Popen, subprocess.Popen]:
    """按 run 模式顺序启动 bot+collector，初次启动与重启共用。

    - 单聊服务（bot）先启动并等待 ready stamp；
    - 若 bot 在就绪前已退出，抛 RuntimeError 由调用方决定 cleanup；
    - 否则启动收集服务（collector），返回两个 Popen 句柄。
    """
    _clear_bot_ready_stamp()
    logger.info("启动单聊服务：%s", " ".join(bot_cmd))
    bot_proc = subprocess.Popen(bot_cmd, env=env)
    logger.info("等待单聊服务就绪（最长 %.0fs）", BOT_READY_WAIT_TIMEOUT)
    if not _wait_for_bot_ready(bot_proc, BOT_READY_WAIT_TIMEOUT):
        if bot_proc.poll() is not None:
            raise RuntimeError(
                f"单聊服务在就绪前已退出，exit_code={bot_proc.returncode}"
            )
        logger.warning(
            "单聊服务在 %.0fs 内未就绪，仍继续启动收集服务（可能影响首批消息处理）",
            BOT_READY_WAIT_TIMEOUT,
        )
    else:
        logger.info("单聊服务已就绪，启动收集服务")
    logger.info("启动收集服务：%s", " ".join(collector_cmd))
    collector_proc = subprocess.Popen(collector_cmd, env=env)
    return bot_proc, collector_proc


def _perform_restart(
    processes: list[subprocess.Popen],
    env: dict[str, str],
    bot_cmd: list[str],
    collector_cmd: list[str],
) -> tuple[subprocess.Popen, subprocess.Popen]:
    logger.info("收到重启请求，正在重启 bot+collector")
    _stop_processes(processes)
    return _start_services(env, bot_cmd, collector_cmd)


def main() -> None:
    configure_logging(service_name="main", level=os.environ.get("MSG_LISTENER_LOG_LEVEL", "INFO"))
    log_process_started(logger, "服务编排入口")
    env = lark_cli_subprocess_env(os.environ.copy())
    py_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{PROJECT_ROOT}{os.pathsep}{py_path}" if py_path else str(PROJECT_ROOT)
    try:
        env.update(asyncio.run(_resolve_owner_env(PROJECT_ROOT / "cache")))
    except Exception as exc:  # noqa: BLE001
        logger.error("获取 owner open_id 失败：%s", exc)
        raise SystemExit(1) from exc
    os.chdir(PROJECT_ROOT)
    initial_parent_pid = os.getppid()

    bot_args = sys.argv[1:]
    collector_cmd = [sys.executable, "-m", "src.collector.main"]
    bot_cmd = [sys.executable, "-m", "src.bot.main", *bot_args]
    mywork_cmd = [sys.executable, "-m", "src.mywork.main"]
    _kill_stale_processes(
        [
            r"\S*python(?:\d+(?:\.\d+)*)?\s+-m\s+src\.bot\.main(?:\s|$)",
            r"\S*python(?:\d+(?:\.\d+)*)?\s+-m\s+src\.collector\.main(?:\s|$)",
            r"\S*python(?:\d+(?:\.\d+)*)?\s+-m\s+src\.mywork\.main(?:\s|$)",
        ]
    )
    # 清理上一进程残留的重启请求文件，避免 supervisor 一启动就误触发重启。
    _clear_restart_request()

    processes: list[subprocess.Popen] = []
    mywork_proc: subprocess.Popen | None = None
    next_mywork_run = _next_mywork_run_after(datetime.now().astimezone())
    logger.info("mywork 周任务下次触发时间：%s", next_mywork_run.isoformat())
    stopped = False

    def stop_children() -> None:
        nonlocal stopped, mywork_proc
        if stopped:
            return
        stopped = True
        active = [*processes]
        if mywork_proc is not None:
            active.append(mywork_proc)
        _stop_processes(active)
        mywork_proc = None
        _clear_bot_ready_stamp()
        _clear_restart_request()

    atexit.register(stop_children)

    def handle_signal(signum: int, _: object) -> None:
        logger.info("收到信号 %s，正在停止服务", signum)
        stop_children()
        raise SystemExit(128 + signum)

    install_signal_handlers(DEFAULT_SIGNAL_SET, handle_signal)

    try:
        bot_proc, collector_proc = _start_services(env, bot_cmd, collector_cmd)
    except RuntimeError as exc:
        logger.error("%s", exc)
        stop_children()
        raise SystemExit(1) from exc
    processes.extend([bot_proc, collector_proc])

    while True:
        now = datetime.now().astimezone()
        if mywork_proc is not None:
            mywork_code = mywork_proc.poll()
            if mywork_code is not None:
                logger.info("mywork 周任务已退出，exit_code=%s", mywork_code)
                mywork_proc = None
        if mywork_proc is None and now >= next_mywork_run:
            mywork_proc = _start_mywork(env, mywork_cmd)
            next_mywork_run = _next_mywork_run_after(now + timedelta(seconds=1))
            logger.info("mywork 周任务下次触发时间：%s", next_mywork_run.isoformat())
        if RESTART_REQUEST_PATH.exists():
            _clear_restart_request()
            try:
                bot_proc, collector_proc = _perform_restart(
                    processes, env, bot_cmd, collector_cmd
                )
            except RuntimeError as exc:
                logger.error("重启失败：%s", exc)
                stop_children()
                raise SystemExit(1) from exc
            processes.clear()
            processes.extend([bot_proc, collector_proc])
            continue
        if parent_process_changed(initial_parent_pid):
            logger.warning(
                "检测到父进程已变更，old_ppid=%s current_ppid=%s，正在停止服务",
                initial_parent_pid,
                os.getppid(),
            )
            stop_children()
            raise SystemExit(1)
        for name, proc in (("收集服务", collector_proc), ("单聊服务", bot_proc)):
            code = proc.poll()
            if code is not None:
                logger.info("%s已退出，exit_code=%s，正在停止其他服务", name, code)
                stop_children()
                raise SystemExit(code)
        time.sleep(1)


if __name__ == "__main__":
    main()
