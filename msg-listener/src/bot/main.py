"""bot-agent 进程入口：分析循环与卡片/命令 WebSocket worker。

用法：
  python -m src.bot.main           # 常驻 loop 模式
  python -m src.bot.main --once    # 跑一轮 AnalyzerLoop 后退出（不启动 WebSocket worker）
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

try:
    import tomllib  # py311+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

from .analysis.analyzer_loop import (
    AnalyzerLoop,
    AnalyzerLoopConfig,
    CardConfig,
)
from .analysis_cli_registry import (
    build_selected_analyzer,
    ensure_selected_cli_ready,
    resolve_analysis_cli,
    warmup_analysis_cli_cache,
)
from .cards.registry import CardRegistry
from .cards.router import CardRouter
from .cards.scenes import DevTaskCardScene, MeegoCardScene, RequirementCardScene
from .cards.service import CardService
from .cards.state_store import CardStateStore
from .cards.worker import CardActionWorker
from .commands.config_card_scene import ConfigCardScene
from .commands.command_server import CommandServer, CommandServerConfig
from .dev_task_executor_support import (
    build_selected_executor,
    ensure_selected_executor_ready,
    resolve_executor,
    warmup_executor_cache,
)
from .dev_tasks import (
    BubbleRepository,
    DevTaskOrchestrator,
    DevTaskOrchestratorConfig,
    DevTaskRepository,
)
from .git_inventory import GitInventoryService, RepoScanner
from .git_inventory.repository import RepoInventoryRepository
from ..common.filter import OwnerCache
from ..common.index import IndexDB
from .notifications import BubbleNotifier
from .notifier import BotNotifier
from .trae_side_chat import set_workspace_open_wait_seconds
from ..common.paths import index_db_path
from .analysis.record_repository import RecordRepository
from ..common.process_lifecycle import (
    install_asyncio_shutdown_handlers,
    log_process_started,
    watch_parent_process,
)
from ..common.logging_setup import configure_logging

logger = logging.getLogger("msg-listener-bot")


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.toml"

# 单聊服务就绪标志：bot 在 card+analyzer+欢迎语全部就绪后写入，
# 由 src/main.py 据此控制 collector 的启动时机。
BOT_READY_STAMP_PATH = PROJECT_ROOT / "cache" / "bot.ready"


def _write_bot_ready_stamp() -> None:
    try:
        BOT_READY_STAMP_PATH.parent.mkdir(parents=True, exist_ok=True)
        BOT_READY_STAMP_PATH.write_text(str(os.getpid()), encoding="utf-8")
    except OSError as exc:  # pragma: no cover - 仅记录，不影响主流程
        logger.warning("写入 bot ready stamp 失败：%s", exc)


def _clear_bot_ready_stamp() -> None:
    try:
        BOT_READY_STAMP_PATH.unlink(missing_ok=True)
    except OSError as exc:  # pragma: no cover
        logger.debug("清理 bot ready stamp 失败：%s", exc)


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("rb") as f:
        return tomllib.load(f)


def setup_logging(level: str) -> None:
    configure_logging(service_name="bot", level=level)


def _build_loop_config(cfg: dict) -> AnalyzerLoopConfig:
    p = cfg.get("phase2") or {}
    return AnalyzerLoopConfig(
        analysis_interval_seconds=float(p.get("analysis_interval_seconds", 60)),
        analysis_batch_size=int(p.get("analysis_batch_size", 20)),
        analysis_max_concurrency=int(p.get("analysis_max_concurrency", 1)),
        analysis_timeout_seconds=float(p.get("analysis_timeout_seconds", 240)),
        analysis_max_attempts=int(p.get("analysis_max_attempts", 2)),
        process_current_date_only=bool(p.get("process_current_date_only", True)),
    )


def _build_card_config(cfg: dict) -> CardConfig:
    p = (cfg.get("phase2") or {}).get("card") or {}
    return CardConfig(
        send_media_messages=bool(p.get("send_media_messages", True)),
        card_send_max_concurrency=int(p.get("card_send_max_concurrency", 1)),
        meego_requirement_host=str(p.get("meego_requirement_host", "") or ""),
        meego_prompt_delivery=str(p.get("meego_prompt_delivery", "bubble")) or "bubble",
        meego_prompt_content=str(p.get("meego_prompt_content", "") or ""),
    )


def _build_command_config(cfg: dict) -> CommandServerConfig:
    c = cfg.get("commands") or {}
    return CommandServerConfig(
        prefix=str(c.get("prefix", "/")) or "/",
        del_out_confirm_ttl_seconds=float(c.get("del_out_confirm_ttl_seconds", 120)),
    )


def _build_dev_task_config(cfg: dict) -> DevTaskOrchestratorConfig:
    section = ((cfg.get("phase3") or {}).get("dev_task") or {})
    return DevTaskOrchestratorConfig(
        max_concurrency=int(section.get("max_concurrency", 3)),
        max_attempts=int(section.get("max_attempts", 3)),
        max_wait_minutes=float(section.get("max_wait_minutes", 60)),
        retry_backoff_base_seconds=float(section.get("retry_backoff_base_seconds", 5)),
        startup_submit_timeout_seconds=float(section.get("startup_submit_timeout_seconds", 30)),
    )


def _build_git_inventory_service(
    *,
    cfg: dict,
    repository: RepoInventoryRepository,
) -> GitInventoryService:
    git_cfg = ((cfg.get("phase3") or {}).get("git") or {})
    roots_raw = git_cfg.get("scan_roots") or ["~"]
    if not isinstance(roots_raw, list):
        roots_raw = [part.strip() for part in str(roots_raw).split(",") if part.strip()]
    ignore_raw = git_cfg.get("ignore_substrings") or [
        "/.Trash/",
        "/.Trash-",
        "/Library/",
        "/AppData/",
        "/$Recycle.Bin/",
        "/node_modules/",
        "/.venv/",
        "/.cache/",
        "/.git/objects/",
    ]
    if not isinstance(ignore_raw, list):
        ignore_raw = []
    scanner = RepoScanner(
        roots=[str(item) for item in roots_raw if item],
        ignore_substrings=[str(item) for item in ignore_raw if item],
        max_depth=int(git_cfg.get("max_depth", 5)),
    )
    return GitInventoryService(
        repository=repository,
        scanner=scanner,
        ttl_seconds=int(git_cfg.get("inventory_ttl_seconds", 600)),
    )


async def amain() -> None:
    startup_parent_pid = os.getppid()
    parser = argparse.ArgumentParser(prog="bot-agent")
    parser.add_argument(
        "--once",
        action="store_true",
        help="跑一轮 AnalyzerLoop 后退出，不启动 WebSocket worker",
    )
    args = parser.parse_args()

    cfg = load_config()
    setup_logging(str(cfg.get("log_level", "INFO")))
    log_process_started(logger, "bot")
    # 启动伊始先清掉上一次进程残留的 ready stamp，确保父进程不会误判成"已就绪"。
    _clear_bot_ready_stamp()
    shutdown_event = asyncio.Event()
    install_asyncio_shutdown_handlers(
        logger=logger,
        service_name="bot",
        shutdown_event=shutdown_event,
    )

    out_dir = (PROJECT_ROOT / "out").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = PROJECT_ROOT / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # owner open_id：复用 collector 的 cache/owner.json；若不存在则按 user 身份补一次。
    owner = OwnerCache(cache_dir)
    try:
        owner_open_id = await owner.load_or_fetch()
    except Exception as exc:  # noqa: BLE001
        logger.error("获取 owner open_id 失败：%s", exc)
        sys.exit(1)
    if not os.environ.get("MSG_LISTENER_OWNER_OPEN_ID"):
        logger.info("owner_open_id = %s", owner_open_id)

    index_db = IndexDB(index_db_path(out_dir))
    repo = RecordRepository(index_db)
    notifier = BotNotifier(owner_open_id=owner_open_id)
    out_lock = asyncio.Lock()

    loop_cfg = _build_loop_config(cfg)
    card_cfg = _build_card_config(cfg)
    cmd_cfg = _build_command_config(cfg)
    dev_task_cfg = _build_dev_task_config(cfg)
    set_workspace_open_wait_seconds(
        float(
            ((cfg.get("phase2") or {}).get("card") or {}).get(
                "trae_side_chat_open_wait_seconds", 6.0
            )
        )
    )
    try:
        analysis_cli = resolve_analysis_cli(cfg)
        logger.info("当前需求判断 CLI：%s", analysis_cli)
        await ensure_selected_cli_ready(cfg)
        executor_key = resolve_executor(cfg)
        logger.info("当前开发任务执行器：%s", executor_key)
        await ensure_selected_executor_ready(cfg)
        await warmup_analysis_cli_cache(cfg)
        await warmup_executor_cache(cfg)
        analyzer = build_selected_analyzer(cfg)
        dev_task_executor = build_selected_executor(cfg)
    except RuntimeError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    card_store = CardStateStore(index_db_path(out_dir))
    card_registry = CardRegistry()
    repo_inventory_repository = RepoInventoryRepository(index_db)
    git_inventory_service = _build_git_inventory_service(
        cfg=cfg,
        repository=repo_inventory_repository,
    )
    dev_task_repository = DevTaskRepository(index_db)
    bubble_repository = BubbleRepository(index_db)
    bubble_notifier = BubbleNotifier(
        bot_notifier=notifier,
        bubble_repository=bubble_repository,
    )
    dev_task_orchestrator = DevTaskOrchestrator(
        executor=dev_task_executor,
        record_repository=repo,
        dev_task_repository=dev_task_repository,
        bubble_notifier=bubble_notifier,
        config=dev_task_cfg,
        repo_inventory_repository=repo_inventory_repository,
    )
    card_registry.register(
        ConfigCardScene(
            config_path=CONFIG_PATH,
            project_root=PROJECT_ROOT,
        )
    )
    card_registry.register(
        RequirementCardScene(
            inventory_service=git_inventory_service,
            record_repository=repo,
            dev_task_repository=dev_task_repository,
            dev_task_orchestrator=dev_task_orchestrator,
            bubble_notifier=bubble_notifier,
            default_branch=card_cfg.default_branch,
            requires_branch=not dev_task_orchestrator.is_local_executor,
        )
    )
    card_registry.register(
        MeegoCardScene(
            inventory_service=git_inventory_service,
            bot_notifier=notifier,
            requirement_host=card_cfg.meego_requirement_host,
            prompt_delivery=card_cfg.meego_prompt_delivery,
            prompt_content=card_cfg.meego_prompt_content,
        )
    )
    card_registry.register(
        DevTaskCardScene(
            inventory_service=git_inventory_service,
            record_repository=repo,
            dev_task_repository=dev_task_repository,
            dev_task_orchestrator=dev_task_orchestrator,
            bubble_notifier=bubble_notifier,
            default_branch=card_cfg.default_branch,
            requires_branch=not dev_task_orchestrator.is_local_executor,
        )
    )
    card_service = CardService(
        registry=card_registry,
        store=card_store,
        notifier=notifier,
    )
    card_router = CardRouter(registry=card_registry)
    card_connected_event = asyncio.Event()
    card_worker = CardActionWorker(
        owner_open_id=owner_open_id,
        store=card_store,
        router=card_router,
        service=card_service,
        connected_event=card_connected_event,
    )

    analyzer_loop = AnalyzerLoop(
        repo=repo,
        analyzer=analyzer,
        notifier=notifier,
        loop_config=loop_cfg,
        card_config=card_cfg,
        out_lock=out_lock,
        card_service=card_service,
        owner_open_id=owner_open_id,
    )

    if args.once:
        report = await analyzer_loop.run_once()
        logger.info("AnalyzerLoop 单次运行结果：%s", report)
        return

    cmd_server = CommandServer(
        owner_open_id=owner_open_id,
        notifier=notifier,
        out_lock=out_lock,
        out_dir=out_dir,
        project_root=PROJECT_ROOT,
        config_path=CONFIG_PATH,
        cmd_config=cmd_cfg,
        card_service=card_service,
    )
    card_worker.command_server = cmd_server

    try:
        resumed = await dev_task_orchestrator.resume_pending_tasks()
        if resumed:
            logger.info("已恢复 %d 条 pending dev task", resumed)
        reconciled = await dev_task_orchestrator.reconcile_terminal_notifications()
        if reconciled:
            logger.info("已补偿 %d 条终态 dev task 气泡", reconciled)
    except Exception as exc:  # noqa: BLE001
        logger.exception("恢复/补偿 dev task 失败：%s", exc)

    # 启动顺序约定（用于让 collector 在单聊服务真正就绪之后才启动）：
    #   1) CardActionWorker：先建立卡片回调 / 命令消息的 WebSocket 通道。
    #   2) AnalyzerLoop：在卡片通道就绪后再开始分析循环，避免分析产物找不到回调。
    #   3) 发送启动欢迎语，标志单聊服务对 owner 可见。
    #   4) 写 ready stamp，src/main.py 据此放行 collector。
    card_task = asyncio.create_task(card_worker.run_forever(), name="card-worker")
    shutdown_task = asyncio.create_task(shutdown_event.wait(), name="bot-shutdown")
    parent_task = asyncio.create_task(
        watch_parent_process(
            logger=logger,
            service_name="bot",
            initial_parent_pid=startup_parent_pid,
            shutdown_event=shutdown_event,
        ),
        name="bot-parent-watch",
    )

    # 等待 CardActionWorker 的 WebSocket 真正连接成功，再继续后续启动步骤。
    card_connected_wait = asyncio.create_task(
        card_connected_event.wait(), name="card-connected-wait"
    )
    try:
        await asyncio.wait(
            {card_connected_wait, card_task, shutdown_task, parent_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        if not card_connected_wait.done():
            card_connected_wait.cancel()

    if shutdown_event.is_set() or card_task.done():
        # 卡片通道异常或已收到退出信号，跳过后续启动，进入收尾流程。
        analyzer_task: asyncio.Task | None = None
    else:
        logger.info("CardActionWorker WebSocket 已连接，启动 AnalyzerLoop")
        analyzer_task = asyncio.create_task(
            analyzer_loop.run_forever(), name="analyzer-loop"
        )
        try:
            await cmd_server.send_startup_welcome()
        except Exception as exc:  # noqa: BLE001
            logger.exception("发送启动欢迎语失败：%s", exc)
        _write_bot_ready_stamp()
        logger.info("单聊服务启动完成（card+analyzer+welcome 就绪）")

    try:
        wait_set: set[asyncio.Task] = {card_task, shutdown_task, parent_task}
        if analyzer_task is not None:
            wait_set.add(analyzer_task)
        # 任一 task 异常退出都视为致命错误，整体退出
        done, _ = await asyncio.wait(wait_set, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task in {shutdown_task, parent_task}:
                continue
            if task.exception() is not None:
                logger.exception("子任务异常退出 %s: %s", task.get_name(), task.exception())
    finally:
        shutdown_event.set()
        await analyzer_loop.stop()
        await cmd_server.stop()
        await card_worker.stop()
        await dev_task_orchestrator.stop()
        all_tasks: list[asyncio.Task] = [card_task, shutdown_task, parent_task]
        if analyzer_task is not None:
            all_tasks.append(analyzer_task)
        for task in all_tasks:
            if not task.done():
                task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(*all_tasks, return_exceptions=True),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            logger.error("bot 退出超时，强制终止进程 pid=%s", os.getpid())
            os._exit(1)
        _clear_bot_ready_stamp()


def main() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
