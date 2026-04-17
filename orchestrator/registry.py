from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from modules._base import BaseModule

_registry: dict[str, BaseModule] = {}


def register(module: BaseModule) -> None:
    _registry[module.name] = module


def get(name: str) -> BaseModule:
    if name not in _registry:
        raise KeyError(f"Module '{name}' not registered")
    return _registry[name]


def all_modules() -> dict[str, BaseModule]:
    return _registry.copy()
