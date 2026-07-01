from __future__ import annotations

import datetime as dt
import logging
import os
import sys
from pathlib import Path

from loguru import logger

_configured = False

_CONSOLE_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>{extra} - <level>{message}</level>"
)

_FILE_FORMAT = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}{extra} - {message}"


def _format_extra(record: dict) -> str:
    extra = record.get("extra") or {}
    if not extra:
        record["extra"]["_rendered"] = ""
        return ""
    rendered = " | " + " ".join(f"{key}={value}" for key, value in extra.items())
    record["extra"]["_rendered"] = rendered
    return rendered


class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).bind(
            name=record.name
        ).log(level, record.getMessage())


def _log_base_dir() -> Path:
    """日志根目录（不含日期层）；可由 MSG_LISTENER_LOG_DIR 覆盖。"""
    configured = os.environ.get("MSG_LISTENER_LOG_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "logs"


def _date_dir_name() -> str:
    """当日日期目录名（本地时区）；与 dashboard_api 读取侧的日期口径一致。"""
    return dt.datetime.now().astimezone().strftime("%Y-%m-%d")


# system.log 句柄状态：按当日日期目录缓存，跨天后重开到新目录（见 _system_log_handle）。
_system_log_state: dict[str, object] = {"date": None, "file": None}


def _system_log_handle():
    """返回当日 logs/<日期>/system.log 的追加句柄，跨天自动切到新目录。

    与 loguru 文件 sink 的 {time} 目录模板一致：日期目录在写入时按当天解析，
    避免把所有天的标准输出堆在进程启动日目录。stdout / stderr 两路 tee 共享
    同一句柄，保证按写入顺序追加。
    """
    date = _date_dir_name()
    if date != _system_log_state["date"] or _system_log_state["file"] is None:
        old = _system_log_state["file"]
        if old is not None:
            try:
                old.close()  # type: ignore[union-attr]
            except Exception:
                pass
        log_dir = _log_base_dir() / date
        log_dir.mkdir(parents=True, exist_ok=True)
        _system_log_state["file"] = (log_dir / "system.log").open("a", encoding="utf-8")
        _system_log_state["date"] = date
    return _system_log_state["file"]


class _TeeStream:
    """把写入原始 stream 的内容同时镜像到 system.log。

    用于捕获各 mode 下直接 print 的输出（这些输出不经过 loguru，
    因此原本不会落盘）。每次写入按当天解析 system.log 句柄（_system_log_handle），
    跨天自动切到新日期目录；stdout/stderr 两路共享同一句柄，保证按写入顺序追加。
    """

    def __init__(self, original) -> None:
        self._original = original

    def write(self, text: str) -> int:
        written = self._original.write(text)
        try:
            file = _system_log_handle()
            file.write(text)  # type: ignore[union-attr]
            file.flush()  # type: ignore[union-attr]
        except Exception:
            pass
        return written

    def flush(self) -> None:
        self._original.flush()
        try:
            file = _system_log_state["file"]
            if file is not None:
                file.flush()  # type: ignore[union-attr]
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._original, name)


_system_tee_installed = False


def install_system_log_tee() -> None:
    """把 stdout / stderr 镜像到 logs/<日期>/system.log。

    幂等：重复调用只生效一次。需在尽早的位置调用（如启动脚本入口），
    以便捕获后续所有 print 输出。日期目录在每次写入时按当天解析，跨天自动新建。
    """
    global _system_tee_installed
    if _system_tee_installed:
        return

    sys.stdout = _TeeStream(sys.stdout)
    sys.stderr = _TeeStream(sys.stderr)
    _system_tee_installed = True


def _install_exception_hooks() -> None:
    def _excepthook(exc_type, exc_value, exc_traceback) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.opt(exception=(exc_type, exc_value, exc_traceback)).critical(
            "未捕获异常"
        )

    def _unraisablehook(args: sys.UnraisableHookArgs) -> None:
        logger.opt(
            exception=(args.exc_type, args.exc_value, args.exc_traceback)
        ).error(
            "{}: {!r}",
            args.err_msg or "Exception ignored in",
            args.object,
        )

    sys.excepthook = _excepthook
    sys.unraisablehook = _unraisablehook


def configure_logging(service_name: str, level: str) -> None:
    global _configured
    if _configured:
        return

    logger.remove()

    def _patch(record: dict) -> None:
        _format_extra(record)

    logger.configure(patcher=_patch)

    console_fmt = _CONSOLE_FORMAT.replace("{extra}", "{extra[_rendered]}")
    file_fmt = _FILE_FORMAT.replace("{extra}", "{extra[_rendered]}")

    logger.add(
        sys.stderr,
        level=level,
        format=console_fmt,
    )

    log_base = _log_base_dir()
    log_base.mkdir(parents=True, exist_ok=True)

    diagnose = os.environ.get("MSG_LISTENER_LOG_DIAGNOSE", "0") == "1"

    # 日期目录用 loguru 的 {time} 模板，而非进程启动时固定的字符串：
    # loguru 在每次轮转时会重新求值该路径（_create_path），跨午夜后 00:00 轮转
    # 会解析出新日期目录并在其中新建文件，从而严格按天分目录（与 out/ 一致），
    # 而不是把后续所有天都续写进启动日目录。
    sink_path = log_base / "{time:YYYY-MM-DD}" / f"{service_name}.log"

    logger.add(
        str(sink_path),
        level=level,
        format=file_fmt,
        rotation="00:00",
        retention="14 days",
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=diagnose,
    )

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    _install_exception_hooks()

    _configured = True
