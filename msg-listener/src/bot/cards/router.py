"""Route raw card action events to registered scenes."""
from __future__ import annotations

import logging
from typing import Any

from .base import CardActionContext, CardActionResult, CardInstance
from .registry import CardRegistry

logger = logging.getLogger(__name__)


class CardRouteError(ValueError):
    pass


class CardRouter:
    def __init__(self, *, registry: CardRegistry) -> None:
        self.registry = registry

    async def route(
        self,
        *,
        instance: CardInstance,
        raw_event: dict[str, Any],
    ) -> CardActionResult:
        action = _extract_action(raw_event)
        value = action.get("value") if isinstance(action.get("value"), dict) else {}
        form_data = _extract_form_data(raw_event, action)
        action_name = str(
            value.get("action")
            or action.get("name")
            or raw_event.get("action_name")
            or raw_event.get("name")
            or _infer_action_name(action=action, form_data=form_data)
            or ""
        )
        if not action_name:
            raise CardRouteError("卡片回调缺少 action_name")
        logger.debug(
            "路由卡片回调 instance=%s scene=%s action=%s form_keys=%s value_keys=%s",
            instance.instance_id,
            instance.scene_key,
            action_name,
            sorted(form_data.keys()),
            sorted(value.keys()),
        )
        ctx = CardActionContext(
            instance=instance,
            action_name=action_name,
            form_data=form_data,
            raw_event=raw_event,
        )
        scene = self.registry.get(instance.scene_key)
        return await scene.handle_action(ctx)


def _extract_action(event: dict[str, Any]) -> dict[str, Any]:
    action = event.get("action")
    if isinstance(action, dict):
        return action
    event_obj = event.get("event")
    if isinstance(event_obj, dict) and isinstance(event_obj.get("action"), dict):
        return event_obj["action"]
    return {}


def _extract_form_data(
    event: dict[str, Any],
    action: dict[str, Any],
) -> dict[str, str]:
    raw: dict[str, Any] = {}
    for source in (
        event.get("form_data"),
        event.get("form_value"),
        event.get("form_values"),
        action.get("form_data"),
        action.get("form_value"),
        action.get("form_values"),
        event.get("input_value"),
    ):
        if isinstance(source, dict):
            raw.update(source)
    form: dict[str, str] = {}
    for key, value in raw.items():
        if value is None:
            form[str(key)] = ""
        elif isinstance(value, (str, int, float, bool)):
            form[str(key)] = str(value)
        elif isinstance(value, dict):
            form[str(key)] = str(value.get("value") or value.get("text") or "")
        else:
            form[str(key)] = str(value)
    return form


def _infer_action_name(*, action: dict[str, Any], form_data: dict[str, str]) -> str:
    """兼容 select_static 回调缺少 name/value.action 的场景。"""
    if str(action.get("tag") or "") != "select_static":
        return ""

    option = action.get("option")
    option_value = str(option or "")
    if option_value:
        if form_data.get("repo") == option_value:
            return "repo_changed"
        if form_data.get("branch") == option_value:
            return "branch_changed"

    has_repo = bool(form_data.get("repo"))
    has_branch = bool(form_data.get("branch"))
    if has_repo and not has_branch:
        return "repo_changed"
    if has_branch and not has_repo:
        return "branch_changed"
    return ""
