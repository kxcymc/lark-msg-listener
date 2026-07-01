"""SQLite 索引读写。"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
  record_id    TEXT PRIMARY KEY,
  chat_type    TEXT NOT NULL,
  chat_id      TEXT NOT NULL,
  sender_name  TEXT NOT NULL,
  anchor_time  TEXT NOT NULL,
  record_path  TEXT NOT NULL,
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_anchor_time ON records(anchor_time);
CREATE INDEX IF NOT EXISTS idx_chat        ON records(chat_type, chat_id);

CREATE TABLE IF NOT EXISTS seen_messages (
  message_id       TEXT PRIMARY KEY,
  record_id        TEXT NOT NULL,
  position         TEXT NOT NULL,
  first_seen_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seen_record ON seen_messages(record_id);

CREATE TABLE IF NOT EXISTS meta (
  key    TEXT PRIMARY KEY,
  value  TEXT NOT NULL
);

-- 分析任务表
CREATE TABLE IF NOT EXISTS analysis_jobs (
  record_id       TEXT PRIMARY KEY,
  record_date     TEXT NOT NULL,
  record_path     TEXT NOT NULL,
  status          TEXT NOT NULL,
  attempts        INTEGER NOT NULL DEFAULT 0,
  analyzer        TEXT NOT NULL DEFAULT 'kxcymc',
  raw_output_json TEXT NOT NULL DEFAULT '',
  error           TEXT NOT NULL DEFAULT '',
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analysis_jobs_date
ON analysis_jobs(record_date, status);

-- 需求表
CREATE TABLE IF NOT EXISTS demands (
  demand_id        TEXT PRIMARY KEY,
  record_id        TEXT NOT NULL UNIQUE,
  record_date      TEXT NOT NULL,
  prompt           TEXT NOT NULL,
  summary          TEXT NOT NULL DEFAULT '',
  evidence_json    TEXT NOT NULL DEFAULT '',
  media_json       TEXT NOT NULL DEFAULT '',
  repo             TEXT NOT NULL DEFAULT '',
  branch           TEXT NOT NULL DEFAULT '',
  dispatch_status  TEXT NOT NULL DEFAULT '',
  current_task_id  TEXT NOT NULL DEFAULT '',
  mr_url           TEXT NOT NULL DEFAULT '',
  status           TEXT NOT NULL,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_demands_date
ON demands(record_date, status);

-- 卡片投递表
CREATE TABLE IF NOT EXISTS card_deliveries (
  demand_id         TEXT PRIMARY KEY,
  card_message_id   TEXT NOT NULL DEFAULT '',
  media_message_ids TEXT NOT NULL DEFAULT '',
  idempotency_key   TEXT NOT NULL,
  status            TEXT NOT NULL,
  error             TEXT NOT NULL DEFAULT '',
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repo_inventory (
  repo_id       TEXT PRIMARY KEY,
  label         TEXT NOT NULL,
  local_path    TEXT NOT NULL,
  remotes_json  TEXT NOT NULL DEFAULT '[]',
  branches_json TEXT NOT NULL DEFAULT '[]',
  scanned_at    TEXT NOT NULL
);

-- 开发任务表
CREATE TABLE IF NOT EXISTS dev_tasks (
  task_id           TEXT PRIMARY KEY,
  executor_task_id  TEXT NOT NULL DEFAULT '',
  demand_id         TEXT NOT NULL,
  card_instance_id  TEXT NOT NULL,
  repo_id           TEXT NOT NULL,
  base_branch       TEXT NOT NULL,
  work_branch       TEXT NOT NULL DEFAULT '',
  mr_url            TEXT NOT NULL DEFAULT '',
  commit_sha        TEXT NOT NULL DEFAULT '',
  status            TEXT NOT NULL,
  attempts          INTEGER NOT NULL DEFAULT 0,
  max_attempts      INTEGER NOT NULL DEFAULT 3,
  next_run_at       TEXT NOT NULL DEFAULT '',
  error             TEXT NOT NULL DEFAULT '',
  raw_snapshot_json TEXT NOT NULL DEFAULT '',
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dev_tasks_demand
ON dev_tasks(demand_id);
CREATE INDEX IF NOT EXISTS idx_dev_tasks_status
ON dev_tasks(status, next_run_at);

-- 气泡幂等表
CREATE TABLE IF NOT EXISTS bubbles (
  bubble_id   TEXT PRIMARY KEY,
  demand_id   TEXT NOT NULL,
  task_id     TEXT NOT NULL DEFAULT '',
  event       TEXT NOT NULL,
  message_id  TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bubbles_demand
ON bubbles(demand_id);
"""


def _connect(path: Path) -> sqlite3.Connection:
    """打开 SQLite 短连接，统一设置 busy_timeout 以应对多进程读写。"""
    conn = sqlite3.connect(path, timeout=5.0)
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


class IndexDB:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(_connect(self.path)) as conn:
            conn.executescript(_SCHEMA)
            conn.execute(
                """
                INSERT OR IGNORE INTO seen_messages(message_id, record_id, position, first_seen_at)
                SELECT record_id, record_id, 'anchor', created_at
                FROM records
                """
            )
            conn.commit()

    def connect(self) -> sqlite3.Connection:
        """暴露给 RecordRepository 等同库读写者的短连接工厂。"""
        return _connect(self.path)

    def upsert(
        self,
        *,
        record_id: str,
        chat_type: str,
        chat_id: str,
        sender_name: str,
        anchor_time: str,
        record_path: str,
    ) -> None:
        created_at = datetime.now().astimezone().isoformat(timespec="seconds")
        with closing(_connect(self.path)) as conn:
            conn.execute(
                """
                INSERT INTO records(record_id, chat_type, chat_id, sender_name, anchor_time, record_path, created_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(record_id) DO UPDATE SET
                  chat_type=excluded.chat_type,
                  chat_id=excluded.chat_id,
                  sender_name=excluded.sender_name,
                  anchor_time=excluded.anchor_time,
                  record_path=excluded.record_path,
                  created_at=excluded.created_at
                """,
                (record_id, chat_type, chat_id, sender_name, anchor_time, record_path, created_at),
            )
            conn.commit()

    def is_message_seen(self, message_id: str) -> bool:
        if not message_id:
            return False
        with closing(_connect(self.path)) as conn:
            row = conn.execute(
                "SELECT 1 FROM seen_messages WHERE message_id = ? LIMIT 1",
                (message_id,),
            ).fetchone()
        return row is not None

    def seen_message_ids(self, message_ids: list[str]) -> set[str]:
        ids = [mid for mid in message_ids if mid]
        if not ids:
            return set()
        placeholders = ",".join("?" for _ in ids)
        with closing(_connect(self.path)) as conn:
            rows = conn.execute(
                f"SELECT message_id FROM seen_messages WHERE message_id IN ({placeholders})",
                ids,
            ).fetchall()
        return {str(row[0]) for row in rows}

    def mark_messages_seen(self, *, record_id: str, messages: list[tuple[str, str]]) -> None:
        rows = [(mid, record_id, position) for mid, position in messages if mid]
        if not rows:
            return
        first_seen_at = datetime.now().astimezone().isoformat(timespec="seconds")
        with closing(_connect(self.path)) as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO seen_messages(message_id, record_id, position, first_seen_at)
                VALUES(?,?,?,?)
                """,
                [(mid, rid, position, first_seen_at) for mid, rid, position in rows],
            )
            conn.commit()

    def get_meta(self, key: str) -> str | None:
        with closing(_connect(self.path)) as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = ?",
                (key,),
            ).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with closing(_connect(self.path)) as conn:
            conn.execute(
                """
                INSERT INTO meta(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value),
            )
            conn.commit()
