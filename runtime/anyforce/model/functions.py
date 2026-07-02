from __future__ import annotations

from typing import Any

from sqlalchemy import case as sa_case
from sqlalchemy.sql.elements import ColumnElement

from .base import Transaction


def in_transaction(app: str | None = None) -> Transaction:
    return Transaction()


def case(
    *args: Any,
    default: str | float | ColumnElement[Any] | None = None,
) -> ColumnElement[Any]:
    return sa_case(*args, else_=default)
