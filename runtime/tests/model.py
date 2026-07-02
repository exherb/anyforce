from datetime import date, datetime, timedelta
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import JSON, Column, Interval, LargeBinary, Text
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, Relationship, SQLModel

from anyforce.model import BaseUpdateModel, StrEnum


class CharEnum(StrEnum):
    a = "a", "a"
    b = "b", "b"


class User(BaseUpdateModel):
    email: str = Field(
        default="", max_length=64, index=True, nullable=False, description="邮箱"
    )
    hashed_password: str = Field(default="", max_length=128, nullable=False)


class Model1(BaseUpdateModel):
    name: str = Field(max_length=32)


class ArticleTag(SQLModel, table=True):
    article_id: int | None = Field(
        default=None, foreign_key="article.id", primary_key=True
    )
    tag_id: int | None = Field(default=None, foreign_key="tag.id", primary_key=True)


class Tag(BaseUpdateModel):
    name: str = Field(max_length=32)
    articles: list["Article"] = Relationship(
        back_populates="tags", link_model=ArticleTag
    )


class Article(BaseUpdateModel):
    title: str = Field(max_length=64)
    tags: list[Tag] = Relationship(back_populates="articles", link_model=ArticleTag)


class Model2(BaseUpdateModel):
    title: ClassVar[str] = "测试"
    __auto_now_fields__: ClassVar[tuple[str, ...]] = (
        *BaseUpdateModel.__auto_now_fields__,
        "auto_now_field",
    )
    __auto_now_add_fields__: ClassVar[tuple[str, ...]] = (
        *BaseUpdateModel.__auto_now_add_fields__,
        "auto_now_add_field",
    )

    int_field: int = Field(title="数字字段")
    bigint_field: int
    char_enum_field: CharEnum = Field(sa_column=Column(SAEnum(CharEnum)))
    nullable_char_field: str | None = Field(
        default=None, max_length=32, unique=True, description="唯一字段"
    )
    default_char_field: str = Field(default="", max_length=32, nullable=False)
    required_char_field: str = Field(
        max_length=32, nullable=False, description="必填字段"
    )
    text_field: str = Field(default="", sa_type=Text)
    uuid_field: UUID | None = Field(default=None)
    float_field: float = 0
    date_field: date | None = Field(default=None)
    datetime_field: datetime | None = Field(default=None)
    auto_now_field: datetime | None = Field(default=None)
    auto_now_add_field: datetime | None = Field(default=None)
    timedelta_field: timedelta | None = Field(default=None, sa_type=Interval)
    json_field: Any = Field(default_factory=dict, sa_type=JSON)
    binary_field: bytes | None = Field(default=None, sa_type=LargeBinary)
    model1_field_id: int | None = Field(default=None, foreign_key="model1.id")
    model1_field: Model1 | None = Relationship()

    class PydanticMeta(BaseUpdateModel.PydanticMeta):
        computed = (
            "int_field_plus_bigint_field",
            "async_int_field_plus_bigint_field",
        )

    class FormPydanticMeta(BaseUpdateModel.FormPydanticMeta):
        computed = ("int_field_plus_bigint_field",)

    def int_field_plus_bigint_field(self) -> int:
        return self.int_field + self.bigint_field

    def set_int_field_plus_bigint_field(self, value: int) -> None:
        self.bigint_field = value - self.int_field

    async def async_int_field_plus_bigint_field(self) -> int | None:
        return self.int_field + self.bigint_field


name = __name__

__all__ = ["name"]
