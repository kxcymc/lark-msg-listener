"""清空 out_dir 并重建空 index.sqlite。

仅在 `/del-out confirm` 流程中调用。调用方必须在 `out_lock` 内执行。
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from ...common.index import IndexDB
from ...common.paths import index_db_path

logger = logging.getLogger(__name__)


@dataclass
class ResetReport:
    out_dir: str
    removed_entries: int
    rebuilt_index: bool


def reset_out_dir(out_dir: Path) -> ResetReport:
    """清空 `out_dir` 下所有内容并重建空的 index.sqlite。

    - `out_dir` 必须存在；不存在则只创建并重建 index。
    - 不会删除 `out_dir` 自身，只删除其子项。
    - 重建 index.sqlite 通过实例化 `IndexDB`，会自动按当前 schema 初始化所有表。
    """
    removed = 0
    out_dir.mkdir(parents=True, exist_ok=True)
    for entry in out_dir.iterdir():
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            removed += 1
        except OSError as exc:
            logger.warning("删除 %s 失败：%s", entry, exc)

    db_path = index_db_path(out_dir)
    if db_path.exists():
        try:
            db_path.unlink()
        except OSError as exc:
            logger.warning("删除 %s 失败：%s", db_path, exc)
    IndexDB(db_path)
    logger.info("out_dir 重置完成：%s removed=%d", out_dir, removed)

    return ResetReport(
        out_dir=str(out_dir),
        removed_entries=removed,
        rebuilt_index=True,
    )
