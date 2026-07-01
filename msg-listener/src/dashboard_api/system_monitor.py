"""进程探活与系统资源即时采样（仅标准库，跨平台）。

monitor 端点把采集逻辑下沉到这里，handlers/monitor.py 只做编排。
设计原则：
  - 自包含一份跨平台进程枚举，不 import src.main 的私有函数，避免与
    supervisor 模块产生耦合或触发其 import 副作用；
  - 任何平台分支都必须有降级路径——拿不到的指标返回 None、探活失败按
    alive=False 处理，绝不在某一端抛未捕获异常导致端点 500。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .config import LOGS_DIR, OUT_DIR, PROJECT_ROOT

# 子进程枚举超时；与 src/main.py 的探活实现保持一致的 5s 上限。
_PROC_TIMEOUT = 5

# 服务名 -> 命令行匹配正则。
# 用 `-m\s+模块名` 精确匹配 `python -m src.xxx` 形式：
#   - main 末尾加 (?:\s|$) 边界，避免误匹配 src.mywork.main / src.bot.main；
#   - bot / collector 模块名本身已足够独特。
_SERVICE_PATTERNS: dict[str, re.Pattern[str]] = {
    "main": re.compile(r"-m\s+src\.main(?:\s|$)"),
    "bot": re.compile(r"-m\s+src\.bot\.main(?:\s|$)"),
    "collector": re.compile(r"-m\s+src\.collector\.main(?:\s|$)"),
}


def enumerate_processes() -> list[tuple[int, str]]:
    """跨平台枚举 (pid, command_line)；任何失败均降级为空列表。

    与 src/main.py 思路一致：posix 用 `ps -axo pid=,command=`，
    nt 用 `wmic ... /FORMAT:CSV`。这里独立实现一份，保持 dashboard_api
    与 supervisor 解耦。
    """
    if os.name == "nt":
        return _enumerate_processes_windows()
    return _enumerate_processes_posix()


def _enumerate_processes_posix() -> list[tuple[int, str]]:
    try:
        res = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=_PROC_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    if res.returncode != 0:
        return []
    results: list[tuple[int, str]] = []
    for line in res.stdout.splitlines():
        text = line.strip()
        if not text:
            continue
        parts = text.split(maxsplit=1)
        try:
            pid = int(parts[0])
        except (ValueError, IndexError):
            continue
        command = parts[1] if len(parts) > 1 else ""
        results.append((pid, command))
    return results


def _enumerate_processes_windows() -> list[tuple[int, str]]:
    # wmic 在新版本 Windows 可能缺失；FileNotFoundError 时按枚举失败降级。
    try:
        res = subprocess.run(
            ["wmic", "process", "get", "ProcessId,CommandLine", "/FORMAT:CSV"],
            capture_output=True,
            text=True,
            timeout=_PROC_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    if res.returncode != 0:
        return []
    results: list[tuple[int, str]] = []
    for line in res.stdout.splitlines():
        text = line.strip()
        # CSV 表头行（Node,CommandLine,ProcessId）与空行跳过。
        if not text or text.lower().startswith("node"):
            continue
        parts = text.split(",")
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[-1].strip())
        except ValueError:
            continue
        # CommandLine 自身可能含逗号，取首列与末列之间的全部内容拼回。
        command = ",".join(parts[1:-1]).strip()
        results.append((pid, command))
    return results


def detect_services() -> list[dict[str, Any]]:
    """识别 main / bot / collector 三个服务是否存活。

    返回固定顺序的列表，每项 {name, alive, pids:[...]}。枚举失败时所有服务
    退化为 alive=False、pids=[]，不抛错。
    """
    processes = enumerate_processes()
    services: list[dict[str, Any]] = []
    for name, pattern in _SERVICE_PATTERNS.items():
        pids = sorted(pid for pid, command in processes if pattern.search(command))
        services.append({"name": name, "alive": bool(pids), "pids": pids})
    return services


def _dir_size_bytes(root: Path) -> int | None:
    """递归累加目录下所有文件大小（字节）。

    目录不存在返回 None；遍历期间遇到无权限 / 已消失的条目逐个跳过，
    保证长期运行的 out/logs 在并发写入下也能稳定采样而不报错。
    """
    if not root.exists():
        return None
    total = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            # 目录无权限或采样瞬间被删除：跳过该子树。
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                # 单个文件消失 / 无权限：跳过，不影响整体累加。
                continue
    return total


def _sample_cpu() -> dict[str, Any] | None:
    """CPU 负载采样：posix 用 os.getloadavg()，nt 无该函数时返回 None。"""
    getloadavg = getattr(os, "getloadavg", None)
    if getloadavg is None:
        return None
    try:
        load1, load5, load15 = getloadavg()
    except OSError:
        return None
    return {
        "load_avg_1m": round(load1, 2),
        "load_avg_5m": round(load5, 2),
        "load_avg_15m": round(load15, 2),
        "cpu_count": os.cpu_count(),
    }


def _read_meminfo_linux() -> dict[str, Any] | None:
    """Linux 读取 /proc/meminfo 拿总内存与可用内存（单位字节）。"""
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return None
    values: dict[str, int] = {}
    try:
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            key, _, rest = line.partition(":")
            kb = rest.strip().split(" ")[0]
            if kb.isdigit():
                values[key.strip()] = int(kb) * 1024  # /proc/meminfo 单位是 kB
    except (OSError, ValueError):
        return None
    total = values.get("MemTotal")
    if total is None:
        return None
    available = values.get("MemAvailable")
    used = total - available if available is not None else None
    return {"total_bytes": total, "available_bytes": available, "used_bytes": used}


def _sample_memory() -> dict[str, Any] | None:
    """内存采样：仅用 stdlib，逐平台降级。

    - Linux：/proc/meminfo（含可用内存）；
    - 其他 posix（含 macOS）：os.sysconf 算 SC_PAGE_SIZE * SC_PHYS_PAGES 得总内存，
      可用内存无 stdlib 来源故为 None；
    - Windows / 取不到时：整体返回 None，保证端点仍能 200。
    """
    linux = _read_meminfo_linux()
    if linux is not None:
        return linux
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        phys_pages = os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, ValueError, OSError):
        return None
    if page_size <= 0 or phys_pages <= 0:
        return None
    return {
        "total_bytes": page_size * phys_pages,
        "available_bytes": None,  # 无 stdlib 来源，明确置空而非臆造
        "used_bytes": None,
    }


def _sample_disk() -> dict[str, Any] | None:
    """磁盘采样：shutil.disk_usage 作用于项目根所在分区。"""
    try:
        usage = shutil.disk_usage(PROJECT_ROOT)
    except OSError:
        return None
    return {
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
    }


def sample_system() -> dict[str, Any]:
    """系统资源即时快照；任一指标取不到返回 None，整体始终可序列化。"""
    return {
        "cpu": _sample_cpu(),
        "memory": _sample_memory(),
        "disk": _sample_disk(),
        "out_dir_bytes": _dir_size_bytes(OUT_DIR),
        "logs_dir_bytes": _dir_size_bytes(LOGS_DIR),
    }
