"""读取 [dashboard_api] 配置段，并统一暴露项目关键路径。

复用 collector 的 config.toml 读取约定（tomllib + 项目根 config.toml），
不引入额外依赖，三端一致。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib  # py311+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

# src/dashboard_api/config.py -> 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.toml"
OUT_DIR = PROJECT_ROOT / "out"
LOGS_DIR = PROJECT_ROOT / "logs"
CACHE_DIR = PROJECT_ROOT / "cache"
MYWORK_DIR = OUT_DIR / "mywork"


def index_db_path() -> Path:
    """index.sqlite 路径；与 src/common/paths.index_db_path 保持一致。"""
    return OUT_DIR / "index.sqlite"


def load_raw_config() -> dict:
    """读取项目根 config.toml；不存在时返回空 dict。"""
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("rb") as f:
        return tomllib.load(f)


@dataclass(frozen=True)
class DashboardConfig:
    """[dashboard_api] 配置段的解析结果。"""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 4317
    max_json_preview_bytes: int = 200000
    tail_log_lines: int = 800

    @classmethod
    def load(cls) -> "DashboardConfig":
        raw = load_raw_config()
        section = raw.get("dashboard_api") or {}
        if not isinstance(section, dict):
            section = {}
        return cls(
            enabled=_as_bool(section.get("enabled"), False),
            host=str(section.get("host") or "127.0.0.1"),
            port=int(section.get("port") or 4317),
            max_json_preview_bytes=int(section.get("max_json_preview_bytes") or 200000),
            tail_log_lines=int(section.get("tail_log_lines") or 800),
        )


def _as_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
