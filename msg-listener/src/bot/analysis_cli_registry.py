"""分析 CLI 注册表：负责通用配置项与按需加载。"""
from __future__ import annotations

import logging
from copy import deepcopy

from .analysis_cli_types import AnalysisCliCardSupport

logger = logging.getLogger(__name__)

ANALYSIS_CLI_kxcymc = "kxcymc_cli"
ANALYSIS_CLI_CLAUDE_CODE = "claude_code_cli"
# TOML 默认可选择 Claude Code CLI，注册表同步开放，避免启动时回退到 kxcymc。
ANALYSIS_CLI_OPTIONS: tuple[str, ...] = (
    ANALYSIS_CLI_kxcymc,
    ANALYSIS_CLI_CLAUDE_CODE,
)
_CARD_SUPPORT_CACHE: dict[str, AnalysisCliCardSupport] = {}


def list_analysis_cli_options() -> tuple[str, ...]:
    return ANALYSIS_CLI_OPTIONS


def resolve_analysis_cli(cfg: dict) -> str:
    phase2_cfg = cfg.get("phase2") or {}
    selected = str(phase2_cfg.get("analysis_cli", ANALYSIS_CLI_kxcymc) or ANALYSIS_CLI_kxcymc)
    if selected not in ANALYSIS_CLI_OPTIONS:
        logger.warning("未知 analysis_cli=%s，回退为 %s", selected, ANALYSIS_CLI_kxcymc)
        return ANALYSIS_CLI_kxcymc
    return selected


async def build_analysis_cli_card_support(cfg: dict) -> AnalysisCliCardSupport:
    if not _CARD_SUPPORT_CACHE:
        await warmup_analysis_cli_cache(cfg)
    selected = resolve_analysis_cli(cfg)
    support = AnalysisCliCardSupport(
        current_option_values={"phase2.analysis_cli": selected},
        option_labels={
            "phase2.analysis_cli": {
                cli: _build_cli_option_label(cli, _CARD_SUPPORT_CACHE.get(cli))
                for cli in ANALYSIS_CLI_OPTIONS
            }
        },
    )
    cached = _CARD_SUPPORT_CACHE.get(selected)
    if cached is None:
        return support
    return _merge_card_support(support, cached)


async def warmup_analysis_cli_cache(cfg: dict) -> None:
    cache: dict[str, AnalysisCliCardSupport] = {}
    for selected in ANALYSIS_CLI_OPTIONS:
        provider = _load_provider(selected, strict=False)
        if provider is None:
            cache[selected] = AnalysisCliCardSupport(available=False)
            continue
        try:
            cache[selected] = await provider.build_card_support(cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("预热 %s 模型缓存失败，将标记为暂不可用：%s", selected, exc)
            cache[selected] = AnalysisCliCardSupport(available=False)
    _CARD_SUPPORT_CACHE.clear()
    _CARD_SUPPORT_CACHE.update(cache)


async def ensure_selected_cli_ready(cfg: dict) -> None:
    selected = resolve_analysis_cli(cfg)
    provider = _load_provider(selected, strict=True)
    await provider.ensure_ready(cfg)


def build_selected_analyzer(cfg: dict):
    selected = resolve_analysis_cli(cfg)
    provider = _load_provider(selected, strict=True)
    return provider.build_analyzer(cfg)


def _load_provider(selected: str, *, strict: bool):
    if selected == ANALYSIS_CLI_kxcymc:
        try:
            from .analysis_cli_support import kxcymc_cli as provider
        except ImportError as exc:
            if strict:
                raise RuntimeError(
                    "已选择 analysis_cli=kxcymc_cli，但 kxcymc_cli 支持目录不可用。"
                ) from exc
            logger.warning("kxcymc_cli 支持目录不可用，配置卡片将隐藏其专属字段：%s", exc)
            return None
        return provider
    if selected == ANALYSIS_CLI_CLAUDE_CODE:
        try:
            from .analysis_cli_support import claude_code_cli as provider
        except ImportError as exc:
            if strict:
                raise RuntimeError(
                    "已选择 analysis_cli=claude_code_cli，但 claude_code_cli 支持目录不可用。"
                ) from exc
            logger.warning(
                "claude_code_cli 支持目录不可用，配置卡片将隐藏其专属字段：%s",
                exc,
            )
            return None
        return provider
    if strict:
        raise RuntimeError(f"暂不支持的 analysis_cli={selected}")
    return None


def _merge_card_support(
    base: AnalysisCliCardSupport,
    extra: AnalysisCliCardSupport,
) -> AnalysisCliCardSupport:
    return AnalysisCliCardSupport(
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


def _build_cli_option_label(cli: str, support: AnalysisCliCardSupport | None) -> str:
    if support is not None and not support.available:
        return f"{cli}（暂不可用）"
    return cli
