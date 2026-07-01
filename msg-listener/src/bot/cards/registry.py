"""Card scene registry."""
from __future__ import annotations

from .base import CardScene


class CardRegistryError(ValueError):
    pass


class CardRegistry:
    def __init__(self) -> None:
        self._scenes: dict[str, CardScene] = {}

    def register(self, scene: CardScene) -> None:
        scene_key = scene.scene_key.strip()
        if not scene_key:
            raise CardRegistryError("scene_key 不能为空")
        if scene_key in self._scenes:
            raise CardRegistryError(f"重复注册 card scene: {scene_key}")
        self._scenes[scene_key] = scene

    def get(self, scene_key: str) -> CardScene:
        try:
            return self._scenes[scene_key]
        except KeyError as exc:
            raise CardRegistryError(f"未注册 card scene: {scene_key}") from exc

    def has(self, scene_key: str) -> bool:
        return scene_key in self._scenes
