"""AnalyzerLoop：当日扫描 + 占坑 + 并发分析 + 卡片推送编排。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from loguru import logger as _logger

from .analyzers import (
    AnalyzerError,
    AnalyzerRateLimited,
    AnalyzerResult,
    CATEGORY_REQUIREMENT,
    normalize_record_to_input,
)
from .card_builder import CardContext, build_card_json
from ..cards.base import CardOpenContext
from ..cards.service import CardService
from ..notifier import BotNotifier, NotifierError
from .record_repository import (
    DELIVERY_FAILED,
    DELIVERY_SENDING,
    DELIVERY_SENT,
    DEMAND_CARD_FAILED,
    DEMAND_CARD_SENDING,
    DEMAND_CARD_SENT,
    DEMAND_DETECTED,
    Demand,
    RecordRef,
    RecordRepository,
)

logger = logging.getLogger(__name__)


# 飞书 im/v1/messages 的 uuid（这里由 lark-cli 的 --idempotency-key 映射而来）
# 用于 1 小时内请求去重，平台最大长度为 50 字符；超过时接口只返回
# HTTP 400 / field validation failed，日志会误导你。实际问题是uuid过长。
#
# 媒体消息原先使用 `demand-media-{demand_id}-{idx}`，当 demand_id 较长时会超过接口限制。
# 因此这里固定生成短 key：保留稳定输入（card 幂等 key + media + idx）做 sha1，
# 再加短前缀，既保证同一附件重试时幂等值不变，也避免受 demand_id 长度影响。
# 这里取 35 是为了给后续可能的前缀/平台兼容留余量，不贴近 50 的硬上限。
_IM_MESSAGE_UUID_MAX_LENGTH = 35


def _short_message_uuid(*parts: object) -> str:
    raw = "-".join(str(part) for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    key = f"mla-{digest}"
    if len(key) > _IM_MESSAGE_UUID_MAX_LENGTH:
        return key[:_IM_MESSAGE_UUID_MAX_LENGTH]
    return key


class _Analyzer(Protocol):
    name: str

    async def analyze(self, payload: Any) -> AnalyzerResult: ...


@dataclass
class CardConfig:
    send_media_messages: bool = True
    card_send_max_concurrency: int = 1
    default_branch: str = ""
    meego_requirement_host: str = ""
    meego_prompt_delivery: str = "bubble"
    meego_prompt_content: str = ""


@dataclass
class AnalyzerLoopConfig:
    analysis_interval_seconds: float = 60.0
    analysis_batch_size: int = 20
    analysis_max_concurrency: int = 1
    analysis_timeout_seconds: float = 240.0
    analysis_max_attempts: int = 2
    process_current_date_only: bool = True
    backoff_base_seconds: float = 30.0
    backoff_max_seconds: float = 600.0


class AnalyzerLoop:
    """当日扫描 + 分析 + 卡片推送。

    与 CommandServer 共享一把 `out_lock`：每条 record 的"读取 + 分析 + 落库"
    边界获取锁，确保 `/del-out confirm` 时不会出现读写冲突。
    """

    def __init__(
        self,
        *,
        repo: RecordRepository,
        analyzer: _Analyzer,
        notifier: BotNotifier,
        loop_config: AnalyzerLoopConfig,
        card_config: CardConfig,
        out_lock: asyncio.Lock,
        card_service: CardService | None = None,
        owner_open_id: str = "",
    ) -> None:
        self.repo = repo
        self.analyzer = analyzer
        self.notifier = notifier
        self.loop_config = loop_config
        self.card_config = card_config
        self.out_lock = out_lock
        self.card_service = card_service
        self.owner_open_id = owner_open_id

        self._stop = asyncio.Event()
        self._current_backoff = 0.0
        self._consecutive_rate_limited = 0
        self._semaphore = asyncio.Semaphore(max(1, loop_config.analysis_max_concurrency))
        self._card_semaphore = asyncio.Semaphore(max(1, card_config.card_send_max_concurrency))

    async def stop(self) -> None:
        self._stop.set()

    @staticmethod
    def _today() -> str:
        return datetime.now().astimezone().strftime("%Y-%m-%d")

    async def run_once(self) -> dict:
        """跑一轮扫描；返回 {analyzed, demands, cards_sent}。"""
        # 启动时把超时的 analyzing 恢复
        recovered = self.repo.recover_stuck_analyzing(
            timeout_seconds=self.loop_config.analysis_timeout_seconds * 2,
            max_attempts=self.loop_config.analysis_max_attempts,
        )
        if recovered:
            logger.info("启动恢复 %d 条卡死的 analyzing 任务", recovered)

        date = self._today()
        claimed = self.repo.collect_pending_records(
            date=date,
            limit=self.loop_config.analysis_batch_size,
        )
        if claimed:
            logger.info("本轮占坑 %d 条 record 进行分析", len(claimed))

        demand_results: list[tuple[RecordRef, AnalyzerResult, dict]] = []
        if claimed:
            tasks = [self._analyze_one(ref) for ref in claimed]
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            for ref, outcome in zip(claimed, outcomes):
                if isinstance(outcome, Exception):
                    logger.exception("分析任务异常 record=%s: %s", ref.record_id, outcome)
                    continue
                if outcome is None:
                    continue
                result, record = outcome
                demand_results.append((ref, result, record))

        # 处理已检测出的需求（包括之前 card_failed 的）
        cards_sent = await self._dispatch_cards(demand_results)

        return {
            "analyzed": len(claimed),
            "demands": len(demand_results),
            "cards_sent": cards_sent,
        }

    async def run_forever(self) -> None:
        logger.info(
            "AnalyzerLoop 启动：interval=%.1fs batch=%d concurrency=%d",
            self.loop_config.analysis_interval_seconds,
            self.loop_config.analysis_batch_size,
            self.loop_config.analysis_max_concurrency,
        )
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception as exc:  # noqa: BLE001
                logger.exception("AnalyzerLoop 轮次异常: %s", exc)
            sleep_for = self.loop_config.analysis_interval_seconds + self._current_backoff
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=sleep_for)
            except asyncio.TimeoutError:
                pass

    async def _analyze_one(
        self, ref: RecordRef
    ) -> tuple[AnalyzerResult, dict] | None:
        """分析单条 record；返回 (AnalyzerResult, record_dict) 或 None。

        被 dispatch_cards 用于后续动作。
        """
        async with self._semaphore:
            return await self._analyze_under_lock(ref)

    async def _analyze_under_lock(
        self, ref: RecordRef
    ) -> tuple[AnalyzerResult, dict] | None:
        bound = _logger.bind(record_id=ref.record_id)
        async with self.out_lock:
            record = self.repo.load_record(ref.record_path)
            if record is None:
                self.repo.mark_failed(ref.record_id, "record.json 不存在或解析失败")
                return None

        # analyze 不持锁（子进程长时间运行）
        try:
            payload = normalize_record_to_input(record, ref.record_path)
            result = await self.analyzer.analyze(payload)
        except AnalyzerRateLimited as exc:
            self._on_rate_limited(ref, exc)
            return None
        except AnalyzerError as exc:
            await self._on_analyzer_error(ref, exc)
            return None
        except Exception as exc:  # noqa: BLE001
            bound.exception("分析未捕获异常 record=%s: %s", ref.record_id, exc)
            self.repo.mark_failed(ref.record_id, f"unexpected: {exc}")
            return None

        # 限流计数复位
        self._consecutive_rate_limited = 0
        self._current_backoff = 0.0

        async with self.out_lock:
            return await self._on_analyzer_result(ref, result, record)

    def _on_rate_limited(self, ref: RecordRef, exc: AnalyzerRateLimited) -> None:
        attempts = self.repo.get_attempts(ref.record_id)
        self._consecutive_rate_limited += 1
        backoff = min(
            self.loop_config.backoff_max_seconds,
            self.loop_config.backoff_base_seconds * (2 ** min(self._consecutive_rate_limited, 6)),
        )
        self._current_backoff = backoff
        logger.warning(
            "分析限流 record=%s attempts=%d backoff=%.1fs: %s",
            ref.record_id,
            attempts,
            backoff,
            exc,
        )
        # 限流：恢复 pending，下一轮重试（不计入失败上限）
        self.repo.mark_pending(ref.record_id, error=f"rate_limited: {exc}")

    async def _on_analyzer_error(self, ref: RecordRef, exc: AnalyzerError) -> None:
        attempts = self.repo.get_attempts(ref.record_id)
        if self._stop.is_set():
            # 进程正在关停：在途分析被信号打断（子进程随之退出、输出残缺）不应判永久失败，
            # 回退为 pending，下次启动重试。
            self.repo.mark_pending(
                ref.record_id,
                error=f"interrupted by shutdown (attempts={attempts}): {exc}",
            )
            logger.warning(
                "分析被关停打断，回退 pending record=%s attempts=%d: %s",
                ref.record_id,
                attempts,
                exc,
            )
            return
        if exc.retriable and attempts < self.loop_config.analysis_max_attempts:
            self.repo.mark_pending(
                ref.record_id,
                error=f"retriable error (attempts={attempts}): {exc}",
            )
            logger.warning(
                "分析可重试错误 record=%s attempts=%d: %s",
                ref.record_id,
                attempts,
                exc,
            )
            return
        raw = (exc.raw_stdout or "")[:4000]
        self.repo.mark_failed(ref.record_id, str(exc), raw_output=raw)
        logger.error("分析失败 record=%s attempts=%d: %s", ref.record_id, attempts, exc)

    async def _on_analyzer_result(
        self,
        ref: RecordRef,
        result: AnalyzerResult,
        record: dict,
    ) -> tuple[AnalyzerResult, dict] | None:
        raw_output = json.dumps(result.raw or {}, ensure_ascii=False)
        if result.category != CATEGORY_REQUIREMENT:
            self.repo.mark_ignored(ref.record_id, raw_output)
            logger.info(
                "record=%s 非需求 category=%s",
                ref.record_id,
                result.category,
            )
            return None
        # 命中需求：写 demands + 标记 analysis_jobs 为 demand
        self.repo.mark_demand(ref.record_id, raw_output)
        record_date = self.repo.to_record_date(record, ref.record_path) or ref.record_date
        demand = Demand(
            demand_id="",
            record_id=ref.record_id,
            record_date=record_date,
            prompt=result.prompt or "",
            summary=result.summary or "",
            evidence=list(result.evidence or []),
            media=_extract_media_meta(record),
            repo="",
            branch="",
            status=DEMAND_DETECTED,
        )
        demand = self.repo.upsert_demand(demand)
        logger.info(
            "命中需求 record=%s demand_id=%s",
            ref.record_id,
            demand.demand_id,
        )
        return result, record

    async def _dispatch_cards(
        self,
        results: list[tuple[RecordRef, AnalyzerResult, dict]],
    ) -> int:
        """串行推送本轮命中的需求 + 兜底重发之前 card_failed 的需求。"""
        # 本轮新命中（如果存在）
        sent = 0
        seen_ids: set[str] = set()
        for ref, _result, record in results:
            demand_row = self.repo.get_demand_by_record(ref.record_id) if hasattr(
                self.repo, "get_demand_by_record"
            ) else None
            # 简化：从 repo 查最新 demand
            if demand_row is None:
                demand_row = _fetch_demand_by_record(self.repo, ref.record_id)
            if not demand_row:
                continue
            seen_ids.add(demand_row["demand_id"])
            ok = await self._send_card_for(demand_row, record)
            if ok:
                sent += 1

        # 兜底：补发未发出的 demands
        pending = self.repo.list_demands_pending_card()
        for demand_row in pending:
            did = demand_row["demand_id"]
            if did in seen_ids:
                continue
            record = None
            ref_path = self._find_record_path(demand_row["record_id"])
            if ref_path:
                record = self.repo.load_record(ref_path)
            if record is None:
                logger.warning(
                    "demand=%s 关联的 record.json 不存在，跳过补发", did
                )
                continue
            ok = await self._send_card_for(demand_row, record)
            if ok:
                sent += 1
        return sent

    def _find_record_path(self, record_id: str) -> str:
        from contextlib import closing
        with closing(self.repo.index_db.connect()) as conn:
            row = conn.execute(
                "SELECT record_path FROM records WHERE record_id = ?",
                (record_id,),
            ).fetchone()
        return row[0] if row else ""

    async def _send_card_for(self, demand_row: dict, record: dict) -> bool:
        async with self._card_semaphore:
            return await self._do_send_card(demand_row, record)

    async def _do_send_card(self, demand_row: dict, record: dict) -> bool:
        demand_id = demand_row["demand_id"]
        record_id = demand_row["record_id"]
        idempotency_key = f"demand-card-{demand_id}"

        # 标记 sending
        self.repo.update_demand_status(demand_id, DEMAND_CARD_SENDING)
        self.repo.upsert_delivery(
            demand_id=demand_id,
            idempotency_key=idempotency_key,
            status=DELIVERY_SENDING,
        )

        try:
            if self.card_service is not None and self.owner_open_id:
                instance = await self.card_service.open_scene(
                    scene_key="requirement",
                    open_context=CardOpenContext(
                        owner_open_id=self.owner_open_id,
                        extra={"demand_row": demand_row, "record": record},
                    ),
                )
                card_message_id = instance.message_id
            else:
                ctx = _build_card_context(demand_row, record, self.card_config)
                card_json = build_card_json(ctx)
                card_message_id = await self.notifier.send_card(
                    card_json=card_json,
                    idempotency_key=idempotency_key,
                )
        except NotifierError as exc:
            logger.warning(
                "卡片发送失败 demand=%s record=%s: %s", demand_id, record_id, exc
            )
            self.repo.update_demand_status(demand_id, DEMAND_CARD_FAILED)
            self.repo.upsert_delivery(
                demand_id=demand_id,
                idempotency_key=idempotency_key,
                status=DELIVERY_FAILED,
                error=str(exc),
            )
            return False

        media_message_ids: list[str] = []
        if self.card_config.send_media_messages:
            media_message_ids = await self._send_media_messages(
                demand_id, record, idempotency_key
            )

        self.repo.upsert_delivery(
            demand_id=demand_id,
            idempotency_key=idempotency_key,
            status=DELIVERY_SENT,
            card_message_id=card_message_id,
            media_message_ids=media_message_ids,
        )
        self.repo.update_demand_status(demand_id, DEMAND_CARD_SENT)
        logger.info(
            "卡片已送达 demand=%s record=%s card_message_id=%s media=%d",
            demand_id,
            record_id,
            card_message_id,
            len(media_message_ids),
        )
        return True

    async def _send_media_messages(
        self,
        demand_id: str,
        record: dict,
        base_idempotency_key: str,
    ) -> list[str]:
        ids: list[str] = []
        record_root = self._find_record_path_dir(record)
        for idx, m in enumerate(_extract_media_meta(record)):
            local_rel = m.get("local_path") or ""
            if not local_rel:
                continue
            local_path = (record_root / local_rel).resolve() if record_root else Path(local_rel)
            if not local_path.exists():
                logger.warning("媒体文件缺失，跳过: %s", local_path)
                continue
            kind = m.get("kind") or "media"
            if kind != "image":
                logger.debug("跳过非 image 媒体 (%s) 的附加消息：%s", kind, local_path)
                continue
            logger.debug(
                "准备发送需求图片附件 demand=%s idx=%d file=%s",
                demand_id,
                idx,
                local_path,
            )
            try:
                msg_id = await self.notifier.send_image(
                    local_path=local_path,
                    idempotency_key=_short_message_uuid(base_idempotency_key, "media", idx),
                )
            except NotifierError as exc:
                logger.warning("发送媒体消息失败 demand=%s file=%s: %s", demand_id, local_path, exc)
                continue
            if msg_id:
                logger.debug(
                    "需求图片附件已发送 demand=%s idx=%d message_id=%s",
                    demand_id,
                    idx,
                    msg_id,
                )
                ids.append(msg_id)
        return ids

    def _find_record_path_dir(self, record: dict) -> Path | None:
        record_id = record.get("record_id") or ""
        if not record_id:
            return None
        path = self._find_record_path(record_id)
        return Path(path) if path else None


def _fetch_demand_by_record(repo: RecordRepository, record_id: str) -> dict | None:
    """按 record_id 取最近一条 demand 行。"""
    import sqlite3
    from contextlib import closing

    with closing(repo.index_db.connect()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM demands WHERE record_id = ?",
            (record_id,),
        ).fetchone()
    return dict(row) if row else None


def _extract_media_meta(record: dict) -> list[dict]:
    items: list[dict] = []
    _append_media_meta(items, record.get("anchor") or {})
    for msg in record.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        _append_media_meta(items, msg)
    return items


def _append_media_meta(items: list[dict], msg: dict) -> None:
    explicit = msg.get("media")
    if isinstance(explicit, list):
        for item in explicit:
            if not isinstance(item, dict):
                continue
            kind = item.get("kind") or ""
            local_path = item.get("local_path") or ""
            if kind and local_path:
                _append_media_item(items, kind=kind, local_path=local_path)
        return
    msg_type = msg.get("type") or ""
    local_path = msg.get("local_path") or ""
    if msg_type == "image" and local_path:
        _append_media_item(items, kind="image", local_path=local_path)
    elif msg_type in {"video", "media"} and local_path:
        _append_media_item(items, kind="video", local_path=local_path)


def _append_media_item(items: list[dict], *, kind: str, local_path: str) -> None:
    item = {
        "kind": kind,
        "local_path": local_path,
        "name": Path(local_path).name,
    }
    if item not in items:
        items.append(item)


def _build_card_context(
    demand_row: dict,
    record: dict,
    card_config: CardConfig,
) -> CardContext:
    anchor = record.get("anchor") or {}
    before_excerpt = []
    after_excerpt = []
    for msg in record.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        text = (msg.get("text") or "").strip()
        if not text:
            continue
        sender = msg.get("sender_name") or "-"
        line = f"[{sender}] {text}"
        if msg.get("position") == "before":
            before_excerpt.append(line)
        elif msg.get("position") == "after":
            after_excerpt.append(line)
    media = _extract_media_meta(record)
    return CardContext(
        demand_id=demand_row["demand_id"],
        record_id=demand_row["record_id"],
        record_date=demand_row.get("record_date") or "",
        prompt=demand_row.get("prompt") or "",
        summary=demand_row.get("summary") or "",
        chat_type=record.get("chat_type") or "",
        sender_name=anchor.get("sender_name") or "",
        create_time=anchor.get("create_time") or "",
        anchor_text=anchor.get("text") or "",
        before_excerpt=before_excerpt,
        after_excerpt=after_excerpt,
        media_files=media,
        repo_options=[],
        default_branch=card_config.default_branch,
    )
