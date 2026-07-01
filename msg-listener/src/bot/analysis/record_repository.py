""" records / analysis_jobs / demands / card_deliveries 仓储层。

只暴露给 bot-agent 进程使用；collector 不依赖本模块。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ...common.index import IndexDB

logger = logging.getLogger(__name__)


# 分析任务状态
JOB_PENDING = "pending"
JOB_ANALYZING = "analyzing"
JOB_IGNORED = "ignored"
JOB_DEMAND = "demand"
JOB_FAILED = "failed"

# 需求状态
DEMAND_DETECTED = "detected"
DEMAND_CARD_SENDING = "card_sending"
DEMAND_CARD_SENT = "card_sent"
DEMAND_CARD_FAILED = "card_failed"
DEMAND_CANCELLED = "cancelled"
DEMAND_DEV_CONFIRMED = "dev_confirmed"

# 卡片投递状态
DELIVERY_PENDING = "pending"
DELIVERY_SENDING = "sending"
DELIVERY_SENT = "sent"
DELIVERY_FAILED = "failed"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_iso(text: str) -> datetime | None:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt


@dataclass
class RecordRef:
    """analysis_jobs 行的轻量视图。"""

    record_id: str
    record_date: str
    record_path: str
    status: str
    attempts: int
    analyzer: str
    raw_output_json: str
    error: str
    updated_at: str


@dataclass
class Demand:
    """demands 行的视图，外部按需写回。"""

    demand_id: str
    record_id: str
    record_date: str
    prompt: str
    summary: str = ""
    evidence: list[str] = field(default_factory=list)
    media: list[dict] = field(default_factory=list)
    repo: str = ""
    branch: str = ""
    status: str = DEMAND_DETECTED


class RecordRepository:
    """聚合 records、analysis_jobs、demands、card_deliveries 的读写操作。"""

    def __init__(self, index_db: IndexDB) -> None:
        self.index_db = index_db

    # ---------- records ----------

    def list_records_by_date(self, date: str) -> list[dict]:
        """返回指定日期下的所有 records 行（基于 anchor_time 前缀匹配）。"""
        with closing(self.index_db.connect()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT record_id, chat_type, chat_id, sender_name,
                       anchor_time, record_path, created_at
                FROM records
                WHERE substr(anchor_time, 1, 10) = ?
                ORDER BY anchor_time ASC
                """,
                (date,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_record_row(self, record_id: str) -> dict | None:
        with closing(self.index_db.connect()) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT record_id, chat_type, chat_id, sender_name,
                       anchor_time, record_path, created_at
                FROM records
                WHERE record_id = ?
                """,
                (record_id,),
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def load_record(record_path: str) -> dict | None:
        """读取 record.json；不存在或解析失败返回 None。"""
        path = Path(record_path) / "record.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("读取 record.json 失败 %s: %s", path, exc)
            return None

    # ---------- analysis_jobs ----------

    def ensure_pending_for_date(self, date: str) -> int:
        """把当日 records 中尚无 analysis_jobs 行的补成 pending；返回新增行数。"""
        now = _now_iso()
        with closing(self.index_db.connect()) as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO analysis_jobs(
                  record_id, record_date, record_path, status, attempts,
                  analyzer, raw_output_json, error, created_at, updated_at
                )
                SELECT r.record_id, ?, r.record_path, ?, 0,
                       'kxcymc', '', '', ?, ?
                FROM records r
                WHERE substr(r.anchor_time, 1, 10) = ?
                """,
                (date, JOB_PENDING, now, now, date),
            )
            conn.commit()
            return cursor.rowcount or 0

    def claim_pending(self, limit: int) -> list[RecordRef]:
        """把 pending 任务原子地占坑成 analyzing 并返回。"""
        if limit <= 0:
            return []
        now = _now_iso()
        with closing(self.index_db.connect()) as conn:
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("BEGIN IMMEDIATE")
                rows = conn.execute(
                    """
                    SELECT record_id, record_date, record_path, status, attempts,
                           analyzer, raw_output_json, error, updated_at
                    FROM analysis_jobs
                    WHERE status = ?
                    ORDER BY updated_at ASC
                    LIMIT ?
                    """,
                    (JOB_PENDING, limit),
                ).fetchall()
                if not rows:
                    conn.commit()
                    return []
                ids = [r["record_id"] for r in rows]
                placeholders = ",".join("?" for _ in ids)
                conn.execute(
                    f"""
                    UPDATE analysis_jobs
                    SET status = ?, attempts = attempts + 1, updated_at = ?
                    WHERE record_id IN ({placeholders})
                    """,
                    [JOB_ANALYZING, now, *ids],
                )
                conn.commit()
            except sqlite3.Error:
                conn.rollback()
                raise
            return [
                RecordRef(
                    record_id=r["record_id"],
                    record_date=r["record_date"],
                    record_path=r["record_path"],
                    status=JOB_ANALYZING,
                    attempts=int(r["attempts"]) + 1,
                    analyzer=r["analyzer"],
                    raw_output_json=r["raw_output_json"],
                    error=r["error"],
                    updated_at=now,
                )
                for r in rows
            ]

    def mark_pending(self, record_id: str, *, error: str = "") -> None:
        """把任务恢复到 pending（用于限流退避或临时失败）。"""
        with closing(self.index_db.connect()) as conn:
            conn.execute(
                """
                UPDATE analysis_jobs
                SET status = ?, error = ?, updated_at = ?
                WHERE record_id = ?
                """,
                (JOB_PENDING, error, _now_iso(), record_id),
            )
            conn.commit()

    def mark_ignored(self, record_id: str, raw_output: str) -> None:
        with closing(self.index_db.connect()) as conn:
            conn.execute(
                """
                UPDATE analysis_jobs
                SET status = ?, raw_output_json = ?, error = '', updated_at = ?
                WHERE record_id = ?
                """,
                (JOB_IGNORED, raw_output, _now_iso(), record_id),
            )
            conn.commit()

    def mark_demand(self, record_id: str, raw_output: str) -> None:
        with closing(self.index_db.connect()) as conn:
            conn.execute(
                """
                UPDATE analysis_jobs
                SET status = ?, raw_output_json = ?, error = '', updated_at = ?
                WHERE record_id = ?
                """,
                (JOB_DEMAND, raw_output, _now_iso(), record_id),
            )
            conn.commit()

    def mark_failed(self, record_id: str, error: str, raw_output: str = "") -> None:
        with closing(self.index_db.connect()) as conn:
            conn.execute(
                """
                UPDATE analysis_jobs
                SET status = ?, error = ?, raw_output_json = ?, updated_at = ?
                WHERE record_id = ?
                """,
                (JOB_FAILED, error[:4000], raw_output, _now_iso(), record_id),
            )
            conn.commit()

    def get_attempts(self, record_id: str) -> int:
        with closing(self.index_db.connect()) as conn:
            row = conn.execute(
                "SELECT attempts FROM analysis_jobs WHERE record_id = ?",
                (record_id,),
            ).fetchone()
        return int(row[0]) if row else 0

    def recover_stuck_analyzing(self, timeout_seconds: float, max_attempts: int) -> int:
        """启动时把超时的 analyzing 按 attempts 恢复为 pending 或 failed；返回处理行数。"""
        with closing(self.index_db.connect()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT record_id, attempts, updated_at
                FROM analysis_jobs
                WHERE status = ?
                """,
                (JOB_ANALYZING,),
            ).fetchall()
        if not rows:
            return 0
        deadline = datetime.now().astimezone() - timedelta(seconds=timeout_seconds)
        recovered = 0
        for row in rows:
            updated = _parse_iso(row["updated_at"])
            if updated is None or updated > deadline:
                continue
            attempts = int(row["attempts"])
            if attempts >= max_attempts:
                self.mark_failed(
                    row["record_id"],
                    f"analysis stuck > {timeout_seconds:.0f}s, attempts={attempts}",
                )
            else:
                self.mark_pending(
                    row["record_id"],
                    error=f"recovered from stuck analyzing (attempts={attempts})",
                )
            recovered += 1
        return recovered

    # ---------- demands ----------

    def upsert_demand(self, demand: Demand) -> Demand:
        """根据 record_id 唯一约束，写入或更新 demands；返回带 demand_id 的对象。"""
        if not demand.demand_id:
            demand.demand_id = f"d_{uuid.uuid4().hex[:16]}"
        now = _now_iso()
        with closing(self.index_db.connect()) as conn:
            conn.row_factory = sqlite3.Row
            existing = conn.execute(
                "SELECT demand_id FROM demands WHERE record_id = ?",
                (demand.record_id,),
            ).fetchone()
            if existing:
                demand.demand_id = existing["demand_id"]
                conn.execute(
                    """
                    UPDATE demands
                    SET prompt = ?, summary = ?, evidence_json = ?, media_json = ?,
                        repo = ?, branch = ?, status = ?, updated_at = ?
                    WHERE demand_id = ?
                    """,
                    (
                        demand.prompt,
                        demand.summary,
                        json.dumps(demand.evidence, ensure_ascii=False),
                        json.dumps(demand.media, ensure_ascii=False),
                        demand.repo,
                        demand.branch,
                        demand.status,
                        now,
                        demand.demand_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO demands(
                      demand_id, record_id, record_date, prompt, summary,
                      evidence_json, media_json, repo, branch, status,
                      created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        demand.demand_id,
                        demand.record_id,
                        demand.record_date,
                        demand.prompt,
                        demand.summary,
                        json.dumps(demand.evidence, ensure_ascii=False),
                        json.dumps(demand.media, ensure_ascii=False),
                        demand.repo,
                        demand.branch,
                        demand.status,
                        now,
                        now,
                    ),
                )
            conn.commit()
        return demand

    def update_demand_status(self, demand_id: str, status: str) -> None:
        with closing(self.index_db.connect()) as conn:
            conn.execute(
                "UPDATE demands SET status = ?, updated_at = ? WHERE demand_id = ?",
                (status, _now_iso(), demand_id),
            )
            conn.commit()

    def update_demand_selection(
        self,
        *,
        demand_id: str,
        repo: str,
        branch: str,
        status: str,
    ) -> None:
        with closing(self.index_db.connect()) as conn:
            conn.execute(
                """
                UPDATE demands
                SET repo = ?, branch = ?, status = ?, updated_at = ?
                WHERE demand_id = ?
                """,
                (repo, branch, status, _now_iso(), demand_id),
            )
            conn.commit()

    def update_demand_inputs(
        self,
        *,
        demand_id: str,
        prompt: str,
        media: list[dict],
    ) -> None:
        """写入卡片上用户确认的 prompt 与勾选的媒体（覆盖原 media_json）。"""
        with closing(self.index_db.connect()) as conn:
            conn.execute(
                """
                UPDATE demands
                SET prompt = ?, media_json = ?, updated_at = ?
                WHERE demand_id = ?
                """,
                (
                    prompt,
                    json.dumps(media, ensure_ascii=False),
                    _now_iso(),
                    demand_id,
                ),
            )
            conn.commit()

    def get_demand(self, demand_id: str) -> dict | None:
        with closing(self.index_db.connect()) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM demands WHERE demand_id = ?",
                (demand_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_demands_pending_card(self) -> list[dict]:
        """返回需要继续推送卡片的需求（detected / card_failed）。"""
        with closing(self.index_db.connect()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM demands
                WHERE status IN (?, ?, ?)
                ORDER BY created_at ASC
                """,
                (DEMAND_DETECTED, DEMAND_CARD_SENDING, DEMAND_CARD_FAILED),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- card_deliveries ----------

    def upsert_delivery(
        self,
        *,
        demand_id: str,
        idempotency_key: str,
        status: str,
        card_message_id: str = "",
        media_message_ids: list[str] | None = None,
        error: str = "",
    ) -> None:
        media_str = ",".join(media_message_ids or [])
        now = _now_iso()
        with closing(self.index_db.connect()) as conn:
            conn.execute(
                """
                INSERT INTO card_deliveries(
                  demand_id, card_message_id, media_message_ids, idempotency_key,
                  status, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(demand_id) DO UPDATE SET
                  card_message_id = excluded.card_message_id,
                  media_message_ids = excluded.media_message_ids,
                  idempotency_key = excluded.idempotency_key,
                  status = excluded.status,
                  error = excluded.error,
                  updated_at = excluded.updated_at
                """,
                (
                    demand_id,
                    card_message_id,
                    media_str,
                    idempotency_key,
                    status,
                    error[:4000],
                    now,
                    now,
                ),
            )
            conn.commit()

    def get_delivery(self, demand_id: str) -> dict | None:
        with closing(self.index_db.connect()) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM card_deliveries WHERE demand_id = ?",
                (demand_id,),
            ).fetchone()
        return dict(row) if row else None

    # ---------- 通用工具 ----------

    @staticmethod
    def to_record_date(record: dict | None, fallback_path: str = "") -> str:
        """从 record.anchor.create_time 推断日期；解析失败回退到 record_path 第一段。"""
        if record:
            anchor = record.get("anchor") or {}
            create = anchor.get("create_time") or ""
            dt = _parse_iso(create) if isinstance(create, str) else None
            if dt:
                return dt.astimezone().strftime("%Y-%m-%d")
        if fallback_path:
            try:
                rel = Path(fallback_path)
                # paths.record_dir 形如 .../out/YYYY-MM-DD/<chat_type>/<chat_id>/<msg_id>
                for part in rel.parts:
                    if len(part) == 10 and part[4] == "-" and part[7] == "-":
                        return part
            except (ValueError, OSError):
                pass
        return ""

    def collect_pending_records(self, date: str, limit: int) -> list[RecordRef]:
        """补 pending → 占坑 → 返回。"""
        added = self.ensure_pending_for_date(date)
        if added:
            logger.info("当日新增 %d 条 pending 分析任务", added)
        return self.claim_pending(limit)
