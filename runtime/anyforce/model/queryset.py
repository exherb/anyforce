from __future__ import annotations

from typing import Any


class ValuesWithoutGroupByQuery:
    def __init__(self, q: Any) -> None:
        self.q = q

    def __await__(self) -> Any:
        return self.q.__await__()
