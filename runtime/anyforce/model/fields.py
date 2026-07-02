from __future__ import annotations

from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import BigInteger, Numeric, Text
from sqlalchemy.types import TypeDecorator


class SplitCharType(TypeDecorator[list[str]]):
    impl = Text
    cache_ok: ClassVar[bool] = True

    def __init__(self, separator: str = ",") -> None:
        super().__init__()
        self.separator = separator

    def process_bind_param(
        self, value: list[str] | str | None, dialect: Any
    ) -> str | None:
        if value is None or isinstance(value, str):
            return value
        return self.separator.join(value)

    def process_result_value(self, value: str | None, dialect: Any) -> list[str] | None:
        if value is None:
            return None
        if value == "":
            return []
        return value.split(self.separator)


class CurrencyType(TypeDecorator[float]):
    impl = BigInteger
    cache_ok: ClassVar[bool] = True

    def __init__(self, multiply: float = 100.0) -> None:
        super().__init__()
        self.multiply = multiply

    def process_bind_param(self, value: float | None, dialect: Any) -> int | None:
        if value is None:
            return None
        return round(value * self.multiply)

    def process_result_value(self, value: int | None, dialect: Any) -> float | None:
        if value is None:
            return None
        return float(value) / self.multiply


class CurrencyDecimalType(TypeDecorator[float]):
    impl = Numeric
    cache_ok: ClassVar[bool] = True

    def __init__(
        self, multiply: float = 100.0, decimal_places: int = 1, max_digits: int = 12
    ) -> None:
        super().__init__()
        self.multiply = multiply
        self.decimal_places = decimal_places
        self.max_digits = max_digits

    def process_bind_param(self, value: float | None, dialect: Any) -> Decimal | None:
        if value is None:
            return None
        return Decimal(str(value * self.multiply))

    def process_result_value(self, value: Decimal | None, dialect: Any) -> float | None:
        if value is None:
            return None
        return float(value) / self.multiply
