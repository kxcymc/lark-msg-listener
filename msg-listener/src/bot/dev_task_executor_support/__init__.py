"""Dev task 执行器注册表。"""
from __future__ import annotations

import logging
from copy import deepcopy

from .types import DevTaskExecutor, DevTaskExecutorCardSupport

logger = logging.getLogger(__name__)

EXECUTOR_kxcymc_OPENAPI = "kxcymc_openapi"
EXECUTOR_CLAUDE_CODE_CLI = "claude_code_cli"
EXECUTOR_TRAE_SIDE_CHAT = "trae_side_chat"
# TOML 默认可选择 claude_code_cli，注册表同步开放，避免启动解析拒绝该值。
EXECUTOR_OPTIONS: tuple[str, ...] = (
    EXECUTOR_kxcymc_OPENAPI,
    EXECUTOR_CLAUDE_CODE_CLI,
    EXECUTOR_TRAE_SIDE_CHAT,
)
EXECUTOR_FIELD_KEY = "phase3.dev_task.executor"

_CARD_SUPPORT_CACHE: dict[str, DevTaskExecutorCardSupport] = {}


def list_executor_options() -> tuple[str, ...]:
    return EXECUTOR_OPTIONS


def resolve_executor(cfg: dict) -> str:
    section = ((cfg.get("phase3") or {}).get("dev_task") or {})
    selected = str(section.get("executor", "") or "").strip()
    if not selected:
        raise RuntimeError(
            f"phase3.dev_task.executor 未配置。合法候选值：{', '.join(EXECUTOR_OPTIONS)}"
        )
    if selected not in EXECUTOR_OPTIONS:
        raise RuntimeError(
            f"未知 phase3.dev_task.executor={selected}。合法候选值："
            f"{', '.join(EXECUTOR_OPTIONS)}"
        )
    return selected


async def warmup_executor_cache(cfg: dict) -> None:
    cache: dict[str, DevTaskExecutorCardSupport] = {}
    for selected in EXECUTOR_OPTIONS:
        provider = _load_provider(selected, strict=False)
        if provider is None:
            cache[selected] = DevTaskExecutorCardSupport(available=False)
            continue
        try:
            cache[selected] = await provider.build_card_support(cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("预热 %s 执行器缓存失败，标记为暂不可用：%s", selected, exc)
            cache[selected] = DevTaskExecutorCardSupport(available=False)
    _CARD_SUPPORT_CACHE.clear()
    _CARD_SUPPORT_CACHE.update(cache)


async def build_dev_task_executor_card_support(cfg: dict) -> DevTaskExecutorCardSupport:
    if not _CARD_SUPPORT_CACHE:
        await warmup_executor_cache(cfg)
    selected = _resolve_executor_safe(cfg)
    support = DevTaskExecutorCardSupport(
        current_option_values={EXECUTOR_FIELD_KEY: selected},
        option_labels={
            EXECUTOR_FIELD_KEY: {
                key: _build_executor_option_label(key, _CARD_SUPPORT_CACHE.get(key))
                for key in EXECUTOR_OPTIONS
            }
        },
    )
    cached = _CARD_SUPPORT_CACHE.get(selected)
    if cached is None:
        return support
    return _merge_card_support(support, cached)


async def ensure_selected_executor_ready(cfg: dict) -> None:
    selected = resolve_executor(cfg)
    provider = _load_provider(selected, strict=True)
    await provider.ensure_ready(cfg)


def build_selected_executor(cfg: dict) -> DevTaskExecutor:
    selected = resolve_executor(cfg)
    provider = _load_provider(selected, strict=True)
    return provider.build_executor(cfg)


def _resolve_executor_safe(cfg: dict) -> str:
    try:
        return resolve_executor(cfg)
    except RuntimeError:
        return EXECUTOR_OPTIONS[0]


def _load_provider(selected: str, *, strict: bool):
    if selected == EXECUTOR_kxcymc_OPENAPI:
        try:
            from . import kxcymc_openapi as provider
        except ImportError as exc:
            if strict:
                raise RuntimeError(
                    "已选择 executor=kxcymc_openapi，但 kxcymc_openapi 支持目录不可用。"
                ) from exc
            logger.warning("kxcymc_openapi 支持目录不可用：%s", exc)
            return None
        return provider
    if selected == EXECUTOR_CLAUDE_CODE_CLI:
        try:
            from . import claude_code_cli as provider
        except ImportError as exc:
            if strict:
                raise RuntimeError(
                    "已选择 executor=claude_code_cli，但 claude_code_cli 支持目录不可用。"
                ) from exc
            logger.warning("claude_code_cli 支持目录不可用：%s", exc)
            return None
        return provider
    if selected == EXECUTOR_TRAE_SIDE_CHAT:
        try:
            from . import trae_side_chat as provider
        except ImportError as exc:
            if strict:
                raise RuntimeError(
                    "已选择 executor=trae_side_chat，但 trae_side_chat 支持目录不可用。"
                ) from exc
            logger.warning("trae_side_chat 支持目录不可用：%s", exc)
            return None
        return provider
    if strict:
        raise RuntimeError(f"暂不支持的 executor={selected}")
    return None


def _merge_card_support(
    base: DevTaskExecutorCardSupport,
    extra: DevTaskExecutorCardSupport,
) -> DevTaskExecutorCardSupport:
    return DevTaskExecutorCardSupport(
        available=base.available and extra.available,
        dynamic_select_options={
            **base.dynamic_select_options,
            **extra.dynamic_select_options,
        },
        current_option_values={
            **base.current_option_values,
            **extra.current_option_values,
        },
        option_labels={
            **deepcopy(base.option_labels),
            **deepcopy(extra.option_labels),
        },
        unavailable_option_values={
            **base.unavailable_option_values,
            **extra.unavailable_option_values,
        },
    )


def _build_executor_option_label(
    key: str,
    support: DevTaskExecutorCardSupport | None,
) -> str:
    if support is not None and not support.available:
        return f"{key}（暂不可用）"
    return key
