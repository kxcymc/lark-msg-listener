"""SQLite-backed card instance state store."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .base import CardInstance


class CardStateStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS card_instances (
                  instance_id TEXT PRIMARY KEY,
                  scene_key TEXT NOT NULL,
                  owner_open_id TEXT NOT NULL,
                  message_id TEXT NOT NULL,
                  status TEXT NOT NULL,
                  context_json TEXT NOT NULL,
                  version INTEGER NOT NULL,
                  created_at INTEGER NOT NULL,
                  updated_at INTEGER NOT NULL
                )
                """
            )
            # 卡片活跃态属于运行时状态，按 instance_id 维度存放，让多张同 (owner,scene)
            # 卡片可以并行活跃；旧版本的 (owner,scene) 唯一表丢弃即可。
            conn.execute("DROP TABLE IF EXISTS active_cards")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS active_cards (
                  instance_id TEXT PRIMARY KEY,
                  owner_open_id TEXT NOT NULL,
                  scene_key TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_active_cards_owner_scene "
                "ON active_cards(owner_open_id, scene_key)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_card_instances_message_id "
                "ON card_instances(message_id)"
            )

    def insert_instance(self, instance: CardInstance) -> None:
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO card_instances (
                  instance_id, scene_key, owner_open_id, message_id, status,
                  context_json, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    instance.instance_id,
                    instance.scene_key,
                    instance.owner_open_id,
                    instance.message_id,
                    instance.status,
                    instance.context_json,
                    instance.version,
                    now,
                    now,
                ),
            )

    def activate(self, *, owner_open_id: str, scene_key: str, instance_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO active_cards(instance_id, owner_open_id, scene_key)
                VALUES (?, ?, ?)
                """,
                (instance_id, owner_open_id, scene_key),
            )

    def deactivate(
        self,
        *,
        owner_open_id: str,
        scene_key: str,
        instance_id: str,
    ) -> None:
        # 仅按 instance_id 精准下线；owner/scene 仅作为调试参数保留以便日志使用方一致。
        del owner_open_id, scene_key
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM active_cards WHERE instance_id = ?",
                (instance_id,),
            )

    def list_active_instance_ids(
        self,
        *,
        owner_open_id: str,
        scene_key: str,
    ) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT instance_id FROM active_cards
                WHERE owner_open_id = ? AND scene_key = ?
                """,
                (owner_open_id, scene_key),
            ).fetchall()
        return [str(row["instance_id"]) for row in rows]

    def is_active(self, instance: CardInstance) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM active_cards WHERE instance_id = ?",
                (instance.instance_id,),
            ).fetchone()
        return row is not None

    def get_by_message_id(self, message_id: str) -> CardInstance | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM card_instances
                WHERE message_id = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (message_id,),
            ).fetchone()
        return _row_to_instance(row)

    def get_by_instance_id(self, instance_id: str) -> CardInstance | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM card_instances WHERE instance_id = ?",
                (instance_id,),
            ).fetchone()
        return _row_to_instance(row)

    def update_status(self, *, instance_id: str, status: str) -> CardInstance | None:
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE card_instances
                SET status = ?, version = version + 1, updated_at = ?
                WHERE instance_id = ?
                """,
                (status, now, instance_id),
            )
            row = conn.execute(
                "SELECT * FROM card_instances WHERE instance_id = ?",
                (instance_id,),
            ).fetchone()
        return _row_to_instance(row)

    def update_instance(
        self,
        *,
        instance_id: str,
        status: str,
        context_json: str | None = None,
    ) -> CardInstance | None:
        now = int(time.time())
        with self._connect() as conn:
            if context_json is None:
                conn.execute(
                    """
                    UPDATE card_instances
                    SET status = ?, version = version + 1, updated_at = ?
                    WHERE instance_id = ?
                    """,
                    (status, now, instance_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE card_instances
                    SET status = ?, context_json = ?, version = version + 1, updated_at = ?
                    WHERE instance_id = ?
                    """,
                    (status, context_json, now, instance_id),
                )
            row = conn.execute(
                "SELECT * FROM card_instances WHERE instance_id = ?",
                (instance_id,),
            ).fetchone()
        return _row_to_instance(row)


def _row_to_instance(row: sqlite3.Row | None) -> CardInstance | None:
    if row is None:
        return None
    return CardInstance(
        instance_id=str(row["instance_id"]),
        scene_key=str(row["scene_key"]),
        owner_open_id=str(row["owner_open_id"]),
        message_id=str(row["message_id"]),
        status=str(row["status"]),
        context_json=str(row["context_json"]),
        version=int(row["version"]),
    )
