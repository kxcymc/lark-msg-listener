"""Scene-neutral card lifecycle contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


CARD_STATUS_ACTIVE = "active"
CARD_STATUS_SUBMITTED = "submitted"
CARD_STATUS_VALIDATION_ERROR = "validation_error"
CARD_STATUS_CANCELLED = "cancelled"
CARD_STATUS_EXPIRED = "expired"
CARD_STATUS_CLOSED = "closed"

CARD_OP_SUCCESS = "success"
CARD_OP_VALIDATION_ERROR = "validation_error"
CARD_OP_CANCEL = "cancel"
CARD_OP_IGNORE = "ignore"


@dataclass(frozen=True)
class CardOpenContext:
    owner_open_id: str
    trigger_message_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CardInstance:
    instance_id: str
    scene_key: str
    owner_open_id: str
    message_id: str
    status: str
    context_json: str
    version: int


@dataclass(frozen=True)
class CardActionContext:
    instance: CardInstance
    action_name: str
    form_data: dict[str, str]
    raw_event: dict[str, Any]


@dataclass(frozen=True)
class CardActionResult:
    op: str
    next_status: str
    card: dict[str, Any] | None = None
    error_text: str | None = None
    context_json: str | None = None


class CardScene(Protocol):
    scene_key: str

    async def build_open_card(
        self,
        ctx: CardOpenContext,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return card payload and scene-specific context."""

    async def handle_action(self, ctx: CardActionContext) -> CardActionResult:
        """Handle an action routed to this scene."""

    async def build_expired_card(self, instance: CardInstance) -> dict[str, Any]:
        """Render an expired state for a replaced card."""
