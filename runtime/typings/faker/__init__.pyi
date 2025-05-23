from typing import Any, Type

class Faker(object):
    def name(self) -> str: ...
    def pyint(
        self,
        min_value: int | None = ...,
        max_value: int | None = ...,
        step: int | None = ...,
    ) -> int: ...
    def pystr(
        self, min_chars: int | None = ..., max_chars: int | None = ...
    ) -> str: ...
    def pytuple(
        self,
        nb_elements: int | None = ...,
        variable_nb_elements: bool | None = ...,
        value_types: Type[Any] | None = ...,
        *allowed_types: Type[Any] | None,
    ) -> tuple[Any, ...]: ...
    def url(self) -> str: ...
    def email(self) -> str: ...
