"""Generic card action callback worker."""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import threading
from typing import TYPE_CHECKING
from typing import Any

from loguru import logger as _logger

from ...common.tls import ensure_system_tls_trust
from .base import (
    CARD_OP_IGNORE,
    CARD_STATUS_ACTIVE,
    CARD_STATUS_VALIDATION_ERROR,
)
from .lark_config import load_lark_app_credentials
from .router import CardRouter
from .service import CardService
from .state_store import CardStateStore

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..commands.command_server import CommandServer


class CardActionWorker:
    def __init__(
        self,
        *,
        owner_open_id: str,
        store: CardStateStore,
        router: CardRouter,
        service: CardService,
        command_server: "CommandServer | None" = None,
        connected_event: asyncio.Event | None = None,
    ) -> None:
        self.owner_open_id = owner_open_id
        self.store = store
        self.router = router
        self.service = service
        self.command_server = command_server
        self._stop = asyncio.Event()
        self._sdk_client: Any | None = None
        self._sdk_done: asyncio.Future[None] | None = None
        self._sdk_thread: threading.Thread | None = None
        # 暴露给 amain 的"WebSocket 已连接"信号；由 amain 决定连接成功后的后续编排。
        self._connected_event = connected_event

    async def stop(self) -> None:
        self._stop.set()
        client = self._sdk_client
        if client is not None:
            await _shutdown_sdk_client(client)
        thread = self._sdk_thread
        if thread is not None and thread.is_alive():
            await asyncio.to_thread(thread.join, 0.2)

    async def run_forever(self) -> None:
        loop = asyncio.get_running_loop()
        done = loop.create_future()
        self._sdk_done = done

        def runner() -> None:
            try:
                self._run_sdk_client(loop)
            except asyncio.CancelledError:
                loop.call_soon_threadsafe(_resolve_sdk_future, done, None)
            except BaseException as exc:  # noqa: BLE001
                if self._stop.is_set():
                    loop.call_soon_threadsafe(_resolve_sdk_future, done, None)
                else:
                    loop.call_soon_threadsafe(_resolve_sdk_future, done, exc)
            else:
                loop.call_soon_threadsafe(_resolve_sdk_future, done, None)

        thread = threading.Thread(
            target=runner,
            name="card-sdk-client",
            daemon=True,
        )
        self._sdk_thread = thread
        thread.start()
        try:
            await done
        finally:
            self._sdk_done = None
            self._sdk_thread = None

    def _run_sdk_client(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._stop.is_set():
            return
        ensure_system_tls_trust()
        lark = _import_lark_oapi()
        from lark_oapi.event.callback.model.p2_card_action_trigger import (  # type: ignore[import-not-found]
            P2CardActionTrigger,
            P2CardActionTriggerResponse,
        )

        if self._stop.is_set():
            return
        credentials = load_lark_app_credentials()
        if self._stop.is_set():
            return
        logger.info("CardActionWorker 使用 SDK WebSocket 接收命令消息与卡片回调")

        def ignore_event(_: Any) -> None:
            return None

        def on_message_receive(data: Any) -> None:
            event = json.loads(lark.JSON.marshal(data))
            future = asyncio.run_coroutine_threadsafe(
                self._dispatch_command_event(event),
                loop,
            )
            future.add_done_callback(_log_message_future)

        def on_bot_menu(data: Any) -> None:
            event = json.loads(lark.JSON.marshal(data))
            future = asyncio.run_coroutine_threadsafe(
                self._dispatch_bot_menu_event(event),
                loop,
            )
            future.add_done_callback(_log_bot_menu_future)

        def on_card_action(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
            event = json.loads(lark.JSON.marshal(data))
            logger.debug(
                "收到卡片回调 action=%s",
                _extract_action(event).get("name") or _extract_action(event).get("value"),
            )
            logger.debug("卡片回调原始事件=%s", json.dumps(event, ensure_ascii=False)[:4000])
            future = asyncio.run_coroutine_threadsafe(self._dispatch(event), loop)
            try:
                response = future.result(timeout=2.8)
                return P2CardActionTriggerResponse(response or {})
            except concurrent.futures.TimeoutError:
                future.add_done_callback(_log_card_action_future)
                logger.info("卡片回调处理超过同步等待窗口，已转入后台继续处理")
                return P2CardActionTriggerResponse(
                    {"toast": {"type": "info", "content": "操作处理中，请稍候"}}
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("卡片回调处理失败: %s", exc)
                return P2CardActionTriggerResponse(
                    {"toast": {"type": "error", "content": f"操作失败：{exc}"}}
                )

        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(on_message_receive)
            .register_p2_im_message_message_read_v1(ignore_event)
            .register_p2_card_action_trigger(on_card_action)
            .register_p2_application_bot_menu_v6(on_bot_menu)
            .build()
        )
        self._sdk_client = lark.ws.Client(
            credentials.app_id,
            credentials.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )
        original_connect = self._sdk_client._connect

        async def connect_and_notify_ready() -> None:
            if self._stop.is_set():
                return
            await original_connect()
            if self._stop.is_set():
                return
            # 仅负责广播"已连接"事件，欢迎语等后续编排交给 amain 控制顺序。
            event = self._connected_event
            if event is not None and not event.is_set():
                loop.call_soon_threadsafe(event.set)

        self._sdk_client._connect = connect_and_notify_ready
        if self._stop.is_set():
            return
        _install_ws_loop_exception_handler()
        try:
            self._sdk_client.start()
        except asyncio.CancelledError:
            logger.debug("CardActionWorker SDK loop 已在退出过程中被取消")
        except RuntimeError as exc:
            if self._stop.is_set() and "Event loop stopped before Future completed" in str(exc):
                logger.debug("CardActionWorker SDK loop stopped during shutdown")
            else:
                raise
        finally:
            # SDK 的 `loop.run_until_complete(_select())` 一旦 `_select` 被 cancel
            # 就会立刻返回，但 `_ping_loop` / `_receive_message_loop`
            # / `ExpiringCache._start_clear_cron` 等后台 task 仍 pending。
            # 这里在 SDK 主线程显式把它们 cancel + gather，
            # 避免 “Task was destroyed but it is pending!”
            # 与 “Task exception was never retrieved” 噪声错误。
            _drain_ws_loop_tasks()

    async def _dispatch_command_event(self, event: dict[str, Any]) -> None:
        if self.command_server is None:
            return
        try:
            await self.command_server.handle_message_event(event)
        except Exception as exc:  # noqa: BLE001
            logger.exception("命令事件处理失败: %s", exc)

    async def _dispatch_bot_menu_event(self, event: dict[str, Any]) -> None:
        if self.command_server is None:
            return
        try:
            await self.command_server.handle_bot_menu_event(event)
        except Exception as exc:  # noqa: BLE001
            logger.exception("机器人菜单事件处理失败: %s", exc)

    async def _dispatch(self, event: dict[str, Any]) -> dict[str, Any]:
        sender_id = _extract_sender_id(event)
        if sender_id and sender_id != self.owner_open_id:
            return {"toast": {"type": "info", "content": "当前操作者无权限执行该操作"}}
        instance = self._find_instance(event)
        if instance is None:
            logger.debug("未找到卡片实例，忽略事件")
            return {"toast": {"type": "warning", "content": "未找到卡片实例，请重新打开卡片"}}
        if instance.owner_open_id != self.owner_open_id:
            return {"toast": {"type": "info", "content": "当前操作者无权限执行该操作"}}
        action = _extract_action(event)
        action_value = action.get("name") or action.get("value")
        bound = _logger.bind(instance_id=instance.instance_id, action=action_value)
        if not self.store.is_active(instance):
            bound.debug("卡片实例已非激活态，忽略 instance=%s", instance.instance_id)
            return {"toast": {"type": "warning", "content": "卡片已失效，请使用最新卡片"}}
        result = await self.router.route(instance=instance, raw_event=event)
        bound.info(
            "卡片回调处理完成 instance=%s op=%s next_status=%s has_card=%s",
            instance.instance_id,
            result.op,
            result.next_status,
            result.card is not None,
        )
        if result.op == CARD_OP_IGNORE:
            if result.error_text:
                return {"toast": {"type": "warning", "content": result.error_text}}
            return {}
        if result.card is None:
            bound.warning("卡片处理结果缺少 card instance=%s", instance.instance_id)
            return {"toast": {"type": "error", "content": "操作失败，请稍后重试"}}
        keep_active = result.next_status in {
            CARD_STATUS_ACTIVE,
            CARD_STATUS_VALIDATION_ERROR,
        }
        await self.service.update_instance_card(
            instance=instance,
            card=result.card,
            next_status=result.next_status,
            context_json=result.context_json,
            keep_active=keep_active,
            push_remote=True,
        )
        return {"card": {"type": "raw", "data": result.card}}

    def _find_instance(self, event: dict[str, Any]):
        instance_id = _extract_instance_id(event)
        if instance_id:
            instance = self.store.get_by_instance_id(instance_id)
            if instance is not None:
                return instance
        message_id = _extract_message_id(event)
        if not message_id:
            return None
        return self.store.get_by_message_id(message_id)


def _event_obj(event: dict[str, Any]) -> dict[str, Any]:
    nested = event.get("event")
    return nested if isinstance(nested, dict) else event


def _extract_action(event: dict[str, Any]) -> dict[str, Any]:
    obj = _event_obj(event)
    action = obj.get("action")
    return action if isinstance(action, dict) else {}


def _extract_instance_id(event: dict[str, Any]) -> str:
    action = _extract_action(event)
    value = action.get("value")
    if isinstance(value, dict):
        return str(value.get("instance_id") or "")
    return ""


def _extract_message_id(event: dict[str, Any]) -> str:
    obj = _event_obj(event)
    for key in ("message_id", "open_message_id", "open_id"):
        value = obj.get(key)
        if value:
            return str(value)
    message = obj.get("message")
    if isinstance(message, dict):
        return str(message.get("message_id") or message.get("open_message_id") or "")
    context = obj.get("context")
    if isinstance(context, dict):
        return str(context.get("open_message_id") or context.get("message_id") or "")
    return ""


def _extract_sender_id(event: dict[str, Any]) -> str:
    obj = _event_obj(event)
    for key in ("sender_id", "operator_id", "open_id"):
        value = obj.get(key)
        if value:
            return str(value)
    operator = obj.get("operator")
    if isinstance(operator, dict):
        return str(operator.get("open_id") or operator.get("user_id") or "")
    return ""


def _import_lark_oapi():
    try:
        import lark_oapi as lark  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "缺少依赖 `lark_oapi`。请先在当前 Python 3.11 虚拟环境中执行 "
            "`python -m pip install -U lark-oapi` 或 `python -m pip install -r requirements.txt`。"
        ) from exc
    return lark


def _resolve_sdk_future(done: asyncio.Future[None], exc: Exception | None) -> None:
    if done.done():
        return
    if exc is None:
        done.set_result(None)
        return
    done.set_exception(exc)


async def _shutdown_sdk_client(client: Any) -> None:
    try:
        client._auto_reconnect = False
    except Exception:  # noqa: BLE001
        logger.debug("CardActionWorker 无法关闭 SDK 自动重连", exc_info=True)

    ws_loop = None
    try:
        import lark_oapi.ws.client as ws_client  # type: ignore[import-not-found]

        ws_loop = getattr(ws_client, "loop", None)
    except Exception:  # noqa: BLE001
        logger.debug("CardActionWorker 无法获取 SDK loop", exc_info=True)

    # SDK 在子线程跑 `loop.run_until_complete(_select())`，
    # 一旦 `_select` 被 cancel，loop 会立即 stop，
    # 因此不能在 SDK loop 上跑“一边 disconnect 一边 cancel”的 coroutine：
    # cancel 自身会让 loop 停止，await 永远拿不到结果（死锁）。
    # 正确顺序：1) 先在 SDK loop 上跑 _disconnect（带 timeout）；
    # 2) 然后 schedule cancel 所有 pending task 让 SDK loop 自然退出；
    # 3) 最后兜底 loop.stop。
    if ws_loop is None or not getattr(ws_loop, "is_running", lambda: False)():
        try:
            await client._disconnect()
        except Exception:  # noqa: BLE001
            logger.debug("CardActionWorker SDK disconnect failed", exc_info=True)
        return

    try:
        disconnect_future = asyncio.run_coroutine_threadsafe(
            client._disconnect(),
            ws_loop,
        )
    except Exception:  # noqa: BLE001
        logger.debug("CardActionWorker SDK disconnect schedule failed", exc_info=True)
    else:
        try:
            await asyncio.wait_for(
                asyncio.wrap_future(disconnect_future),
                timeout=2.0,
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            logger.warning("CardActionWorker SDK disconnect 超时，继续强制取消 SDK loop")
            disconnect_future.cancel()
        except Exception:  # noqa: BLE001
            logger.debug("CardActionWorker SDK disconnect failed", exc_info=True)

    # 让 SDK 主线程的 `run_until_complete(_select())` 立刻返回；
    # 剩余 pending task 由 SDK 主线程在 `_drain_ws_loop_tasks` 中收尾。
    try:
        ws_loop.call_soon_threadsafe(ws_loop.stop)
    except Exception:  # noqa: BLE001
        logger.debug("CardActionWorker SDK loop stop failed", exc_info=True)


def _log_message_future(future: concurrent.futures.Future[Any]) -> None:
    try:
        future.result()
    except Exception as exc:  # noqa: BLE001
        logger.exception("消息事件异步处理失败: %s", exc)


def _log_bot_menu_future(future: concurrent.futures.Future[Any]) -> None:
    try:
        future.result()
    except Exception as exc:  # noqa: BLE001
        logger.exception("机器人菜单事件异步处理失败: %s", exc)


def _log_card_action_future(future: concurrent.futures.Future[Any]) -> None:
    try:
        future.result()
    except Exception as exc:  # noqa: BLE001
        logger.exception("卡片回调后台处理失败: %s", exc)


def _drain_ws_loop_tasks() -> None:
    """SDK loop stop 后，在 SDK 主线程内把剩余 pending task cancel 并 gather。

    必须在 `Client.start()` 返回的同一个线程（即 SDK 主线程）调用，
    因为 `lark_oapi.ws.client.loop` 与该线程绑定。
    """
    try:
        import lark_oapi.ws.client as ws_client  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        return
    ws_loop = getattr(ws_client, "loop", None)
    if ws_loop is None or getattr(ws_loop, "is_closed", lambda: True)():
        return
    if getattr(ws_loop, "is_running", lambda: False)():
        # 仍在跑（异常路径），交给外部 _shutdown_sdk_client 处理。
        return
    try:
        pending = [task for task in asyncio.all_tasks(ws_loop) if not task.done()]
    except Exception:  # noqa: BLE001
        pending = []
    try:
        if pending:
            for task in pending:
                task.cancel()
            ws_loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True)
            )
        # 给已关闭的 pipe / transport 一次派发 connection_lost 的机会，
        # 避免 loop 关闭后才在 __del__ 里触发 "Event loop is closed"。
        ws_loop.run_until_complete(asyncio.sleep(0))
    except Exception:  # noqa: BLE001
        logger.debug("CardActionWorker SDK pending tasks drain failed", exc_info=True)


_WS_LOOP_HANDLER_INSTALLED = False


def _install_ws_loop_exception_handler() -> None:
    """给 SDK ws_loop 装一个 exception handler，
    吞掉关闭连接时 `_receive_message_loop` 抛出的预期异常
    （ConnectionClosed* / CancelledError），避免日志噪声。"""
    global _WS_LOOP_HANDLER_INSTALLED
    if _WS_LOOP_HANDLER_INSTALLED:
        return
    try:
        import lark_oapi.ws.client as ws_client  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        return
    ws_loop = getattr(ws_client, "loop", None)
    if ws_loop is None:
        return

    def _handle(_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exception = context.get("exception")
        if isinstance(exception, asyncio.CancelledError):
            return
        try:
            from websockets.exceptions import ConnectionClosed  # type: ignore[import-not-found]
        except Exception:  # noqa: BLE001
            ConnectionClosed = ()  # type: ignore[assignment]
        if ConnectionClosed and isinstance(exception, ConnectionClosed):
            return
        message = context.get("message") or ""
        if isinstance(message, str) and "Task was destroyed but it is pending" in message:
            return
        # 其它异常仍走默认行为，避免吞掉真实问题。
        _loop.default_exception_handler(context)

    try:
        ws_loop.set_exception_handler(_handle)
        _WS_LOOP_HANDLER_INSTALLED = True
    except Exception:  # noqa: BLE001
        logger.debug("CardActionWorker 安装 ws_loop exception handler 失败", exc_info=True)
