"""Scene-neutral card lifecycle service."""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from ..notifier import BotNotifier, NotifierError
from .base import (
    CARD_STATUS_ACTIVE,
    CARD_STATUS_EXPIRED,
    CardInstance,
    CardOpenContext,
)
from .registry import CardRegistry
from .state_store import CardStateStore

logger = logging.getLogger(__name__)


class CardService:
    def __init__(
        self,
        *,
        registry: CardRegistry,
        store: CardStateStore,
        notifier: BotNotifier,
    ) -> None:
        self.registry = registry
        self.store = store
        self.notifier = notifier

    async def open_scene(
        self,
        *,
        scene_key: str,
        open_context: CardOpenContext,
        expire_previous: bool = False,
    ) -> CardInstance:
        scene = self.registry.get(scene_key)
        logger.info(
            "打开卡片场景 scene=%s owner=%s trigger_message_id=%s expire_previous=%s",
            scene_key,
            open_context.owner_open_id,
            open_context.trigger_message_id,
            expire_previous,
        )
        if expire_previous:
            await self.expire_previous_instances(
                owner_open_id=open_context.owner_open_id,
                scene_key=scene_key,
            )
        instance_id = uuid.uuid4().hex
        card, scene_context = await scene.build_open_card(open_context)
        _inject_instance_id(card, instance_id)
        message_id = await self.notifier.send_card(
            card_json=json.dumps(card, ensure_ascii=False),
            idempotency_key=f"card-{scene_key}-{instance_id}",
        )
        instance = CardInstance(
            instance_id=instance_id,
            scene_key=scene_key,
            owner_open_id=open_context.owner_open_id,
            message_id=message_id,
            status=CARD_STATUS_ACTIVE,
            context_json=json.dumps(scene_context, ensure_ascii=False),
            version=1,
        )
        self.store.insert_instance(instance)
        self.store.activate(
            owner_open_id=open_context.owner_open_id,
            scene_key=scene_key,
            instance_id=instance_id,
        )
        logger.info(
            "卡片已发送 scene=%s instance=%s message_id=%s",
            scene_key,
            instance_id,
            message_id,
        )
        return instance

    async def expire_previous_instances(
        self,
        *,
        owner_open_id: str,
        scene_key: str,
    ) -> None:
        """对场景内所有当前活跃卡片下线并 PATCH 成过期卡。

        仅供需要"互斥"语义的场景显式调用（比如 /config 卡片）；普通独立卡
        片场景不要走这里，多张卡可以并行存活。
        """
        instance_ids = self.store.list_active_instance_ids(
            owner_open_id=owner_open_id,
            scene_key=scene_key,
        )
        for instance_id in instance_ids:
            instance = self.store.get_by_instance_id(instance_id)
            if instance is None:
                continue
            scene = self.registry.get(instance.scene_key)
            try:
                expired_card = await scene.build_expired_card(instance)
                await self.notifier.update_card(
                    message_id=instance.message_id,
                    card=expired_card,
                )
            except NotifierError as exc:
                logger.warning(
                    "更新过期卡片失败 instance=%s: %s", instance.instance_id, exc
                )
            finally:
                self.store.update_status(
                    instance_id=instance.instance_id,
                    status=CARD_STATUS_EXPIRED,
                )
                self.store.deactivate(
                    owner_open_id=owner_open_id,
                    scene_key=scene_key,
                    instance_id=instance.instance_id,
                )

    async def update_instance_card(
        self,
        *,
        instance: CardInstance,
        card: dict[str, Any],
        next_status: str,
        context_json: str | None = None,
        keep_active: bool = False,
        push_remote: bool = True,
    ) -> CardInstance | None:
        _inject_instance_id(card, instance.instance_id)
        logger.info(
            "更新卡片实例 instance=%s next_status=%s keep_active=%s push_remote=%s",
            instance.instance_id,
            next_status,
            keep_active,
            push_remote,
        )
        if push_remote:
            await self.notifier.update_card(message_id=instance.message_id, card=card)
            logger.debug(
                "远端卡片已更新 instance=%s message_id=%s",
                instance.instance_id,
                instance.message_id,
            )
        updated = self.store.update_instance(
            instance_id=instance.instance_id,
            status=next_status,
            context_json=context_json,
        )
        if not keep_active:
            self.store.deactivate(
                owner_open_id=instance.owner_open_id,
                scene_key=instance.scene_key,
                instance_id=instance.instance_id,
            )
        logger.info(
            "卡片实例状态已更新 instance=%s stored_status=%s active=%s",
            instance.instance_id,
            updated.status if updated is not None else next_status,
            keep_active,
        )
        return updated


def _inject_instance_id(card: dict[str, Any], instance_id: str) -> None:
    """Best-effort injection for interactive components carrying callback values."""

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            value = node.get("value")
            if isinstance(value, dict):
                value.setdefault("instance_id", instance_id)
            for child in node.values():
                walk(child)
            return
        if isinstance(node, list):
            for child in node:
                walk(child)

    walk(card)
