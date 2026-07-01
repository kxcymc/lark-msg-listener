"""Conclusion CLI provider contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ConcludeCliInput:
    json_path: Path
    document_title: str
    prompt: str


@dataclass
class ConcludeCliOutput:
    provider_name: str
    document_title: str
    returncode: int
    stdout: str = ""
    stderr: str = ""
    document_url: str = ""
    document_token: str = ""

    @property
    def success(self) -> bool:
        return self.returncode == 0


@dataclass
class ConcludeCliCardSupport:
    available: bool = True
    dynamic_select_options: dict[str, tuple[str, ...]] = field(default_factory=dict)
    current_option_values: dict[str, str] = field(default_factory=dict)
    option_labels: dict[str, dict[str, str]] = field(default_factory=dict)
    unavailable_option_values: dict[str, tuple[str, ...]] = field(default_factory=dict)


class ConcludeCliProvider(Protocol):
    async def ensure_ready(self, cfg: dict) -> None:
        ...

    def build_concluder(self, cfg: dict):
        ...

    async def build_card_support(self, cfg: dict) -> ConcludeCliCardSupport:
        ...

