from __future__ import annotations

from typing import Any, cast

from .base import (
    BaseModel,
    BaseUpdateModel,
    close_engine,
    configure_engine,
    create_all,
    init_models,
)
from .enum import IntEnum, StrEnum
from .recoverable import RecoverableModel


async def init(config: dict[str, Any]) -> None:
    db_url = str(config.get("db_url") or "")
    if not db_url:
        connections = cast(dict[str, Any], config.get("connections", {}))
        default = connections.get("default")
        db_url = str(default or "sqlite://:memory:")
    configure_engine(db_url)
    apps = cast(dict[str, dict[str, Any]], config.get("apps", {}))
    for name, info in apps.items():
        init_models(cast(list[str], info.get("models", [])), name)


def generate_schemas(safe: bool = True) -> None:
    create_all()


async def close_connections() -> None:
    close_engine()


__all__ = [
    "StrEnum",
    "IntEnum",
    "BaseModel",
    "BaseUpdateModel",
    "RecoverableModel",
    "init",
    "init_models",
    "generate_schemas",
    "close_connections",
]
