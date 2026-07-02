from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlmodel import Field

from .base import BaseUpdateModel


class RecoverableModel(BaseUpdateModel):
    is_deleted: bool = Field(default=False, nullable=False, description="是否已经删除")
    delete_or_recover_at: datetime | None = Field(default=None, description="删除时间")

    class Meta:
        abstract = True

    async def update(self, input: Any) -> None:
        dic = (
            cast(dict[str, Any], input)
            if isinstance(input, dict)
            else input.model_dump(exclude_unset=True)
        )
        is_deleted = dic.get("is_deleted")
        if is_deleted is not None and self.is_deleted != is_deleted:
            dic["delete_or_recover_at"] = datetime.now()
        await super().update(dic)

    async def delete(self, using_db: Any | None = None) -> None:
        await self.update(
            {
                "is_deleted": True,
                "delete_or_recover_at": datetime.now(),
            }
        )
