"""日志文件尾部高效读取。

GET /api/logs 只需要最近 N 行，对动辄数 MB 的日志整文件载入既慢又费内存。
这里从文件末尾按块回退读取，凑够 N 行（含换行）即停，保证大文件也是常数级
内存占用。读取失败 / 文件缺失统一返回空列表，由 handler 决定如何呈现。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from .config import LOGS_DIR

# 已知服务名；service 缺省时遍历这些日志（与 logging_setup 的 sink 命名对应）。
KNOWN_SERVICES = ("main", "bot", "collector", "dashboard-api", "mywork", "system")

# 单次回退读取的块大小（字节）。日志多为短行，64KB 足以一次覆盖数百行。
_CHUNK_SIZE = 64 * 1024


def today_dir_name() -> str:
    """当日日志子目录名；与 logging_setup 的日期目录口径（本地日期）一致。"""
    return dt.datetime.now().astimezone().strftime("%Y-%m-%d")


def log_path(service: str, date: str | None = None) -> Path:
    """拼接 logs/<日期>/<service>.log 路径；date 缺省取当日。"""
    return LOGS_DIR / (date or today_dir_name()) / f"{service}.log"


def tail_lines(path: Path, lines: int) -> list[str]:
    """读取文件末尾 lines 行（不含行尾换行符）。

    实现：以二进制从尾部按 _CHUNK_SIZE 回退，累计换行数达到目标即停，
    最后只解码必要的尾部片段。文件不存在或读失败返回空列表。
    """
    if lines <= 0 or not path.is_file():
        return []
    try:
        with path.open("rb") as f:
            f.seek(0, 2)  # 定位到文件末尾
            file_size = f.tell()
            if file_size == 0:
                return []
            blocks: list[bytes] = []
            newline_count = 0
            pos = file_size
            # 多读一行：用换行符切分时首段可能是被截断的半行，需丢弃。
            while pos > 0 and newline_count <= lines:
                read_size = min(_CHUNK_SIZE, pos)
                pos -= read_size
                f.seek(pos)
                chunk = f.read(read_size)
                blocks.append(chunk)
                newline_count += chunk.count(b"\n")
            data = b"".join(reversed(blocks))
    except OSError:
        return []

    # 解码时容错坏字节，避免单条非 UTF-8 日志中断整个尾部读取。
    text = data.decode("utf-8", errors="replace")
    all_lines = text.splitlines()
    return all_lines[-lines:]
