"""只读访问 out/index.sqlite 的仓储基座。

所有 handler 经此模块查询业务事实表，不直接打开数据库。
连接以只读 URI（mode=ro）短连接打开，保证 API 运行期不写入 index.sqlite。
聚合（overview/timeseries）与列表/详情各 handler 在本模块内按需新增方法。
"""
from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import index_db_path


@contextmanager
def read_connection(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """打开只读短连接；行以 sqlite3.Row 返回，便于按列名取值。

    使用 file: URI + mode=ro 强制只读，从根上保证不写库。
    数据库文件尚未生成时抛 sqlite3.OperationalError，由调用方决定降级。
    """
    path = db_path or index_db_path()
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        yield conn
    finally:
        conn.close()


def query_all(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """执行查询，返回 dict 列表；数据库不存在时返回空列表。"""
    try:
        with read_connection() as conn, closing(conn.execute(sql, params)) as cur:
            return [dict(row) for row in cur.fetchall()]
    except sqlite3.OperationalError:
        return []


def query_one(sql: str, params: tuple = ()) -> dict[str, Any] | None:
    """执行查询，返回首行 dict 或 None。"""
    try:
        with read_connection() as conn, closing(conn.execute(sql, params)) as cur:
            row = cur.fetchone()
            return dict(row) if row else None
    except sqlite3.OperationalError:
        return None


def query_scalar(sql: str, params: tuple = ()) -> Any:
    """执行查询，返回首行首列标量或 None。"""
    try:
        with read_connection() as conn, closing(conn.execute(sql, params)) as cur:
            row = cur.fetchone()
            return row[0] if row else None
    except sqlite3.OperationalError:
        return None


def table_exists(name: str) -> bool:
    """判断业务表是否存在，避免对未初始化的库做无意义查询。"""
    row = query_one(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    )
    return row is not None
