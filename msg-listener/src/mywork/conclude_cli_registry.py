"""Conclusion CLI provider registry."""
from __future__ import annotations

import logging
from copy import deepcopy

from .conclude_cli_types import ConcludeCliCardSupport

logger = logging.getLogger(__name__)

CONCLUDE_CLI_kxcymc = "kxcymc_cli"
CONCLUDE_CLI_OPTIONS: tuple[str, ...] = (CONCLUDE_CLI_kxcymc,)
_CARD_SUPPORT_CACHE: dict[str, ConcludeCliCardSupport] = {}


def list_conclude_cli_options() -> tuple[str, ...]:
    return CONCLUDE_CLI_OPTIONS


def resolve_conclude_cli(cfg: dict) -> str:
    mywork_cfg = cfg.get("mywork") or {}
    selected = str(mywork_cfg.get("conclude_cli", CONCLUDE_CLI_kxcymc) or CONCLUDE_CLI_kxcymc)
    if selected not in CONCLUDE_CLI_OPTIONS:
        logger.warning("未知 mywork.conclude_cli=%s，回退为 %s", selected, CONCLUDE_CLI_kxcymc)
        return CONCLUDE_CLI_kxcymc
    return selected


async def build_conclude_cli_card_support(cfg: dict) -> ConcludeCliCardSupport:
    if not _CARD_SUPPORT_CACHE:
        await warmup_conclude_cli_cache(cfg)
    selected = resolve_conclude_cli(cfg)
    support = ConcludeCliCardSupport(
        current_option_values={"mywork.conclude_cli": selected},
        option_labels={
            "mywork.conclude_cli": {
                cli: _build_cli_option_label(cli, _CARD_SUPPORT_CACHE.get(cli))
                for cli in CONCLUDE_CLI_OPTIONS
            }
        },
    )
    cached = _CARD_SUPPORT_CACHE.get(selected)
    if cached is None:
        return support
    return _merge_card_support(support, cached)


async def warmup_conclude_cli_cache(cfg: dict) -> None:
    cache: dict[str, ConcludeCliCardSupport] = {}
    for selected in CONCLUDE_CLI_OPTIONS:
        provider = _load_provider(selected, strict=False)
        if provider is None:
            cache[selected] = ConcludeCliCardSupport(available=False)
            continue
        try:
            cache[selected] = await provider.build_card_support(cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("预热 %s 总结 CLI 缓存失败，将标记为暂不可用：%s", selected, exc)
            cache[selected] = ConcludeCliCardSupport(available=False)
    _CARD_SUPPORT_CACHE.clear()
    _CARD_SUPPORT_CACHE.update(cache)


async def ensure_selected_conclude_cli_ready(cfg: dict) -> None:
    selected = resolve_conclude_cli(cfg)
    provider = _load_provider(selected, strict=True)
    await provider.ensure_ready(cfg)


def build_selected_concluder(cfg: dict):
    selected = resolve_conclude_cli(cfg)
    provider = _load_provider(selected, strict=True)
    return provider.build_concluder(cfg)


def _load_provider(selected: str, *, strict: bool):
    if selected == CONCLUDE_CLI_kxcymc:
        try:
            from .conclude_cli_support import kxcymc_cli as provider
        except ImportError as exc:
            if strict:
                raise RuntimeError("已选择 mywork.conclude_cli=kxcymc_cli，但支持目录不可用。") from exc
            logger.warning("kxcymc_cli 总结支持目录不可用：%s", exc)
            return None
        return provider
    if strict:
        raise RuntimeError(f"暂不支持的 mywork.conclude_cli={selected}")
    return None


def _merge_card_support(
    base: ConcludeCliCardSupport,
    extra: ConcludeCliCardSupport,
) -> ConcludeCliCardSupport:
    return ConcludeCliCardSupport(
        available=base.available and extra.available,
        dynamic_select_options={**base.dynamic_select_options, **extra.dynamic_select_options},
        current_option_values={**base.current_option_values, **extra.current_option_values},
        option_labels={**deepcopy(base.option_labels), **deepcopy(extra.option_labels)},
        unavailable_option_values={
            **base.unavailable_option_values,
            **extra.unavailable_option_values,
        },
    )


def _build_cli_option_label(cli: str, support: ConcludeCliCardSupport | None) -> str:
    if support is not None and not support.available:
        return f"{cli}（暂不可用）"
    return cli

