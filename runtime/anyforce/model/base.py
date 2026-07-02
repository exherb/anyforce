from __future__ import annotations

import importlib
import inspect
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
from types import TracebackType
from typing import (
    Annotated,
    Any,
    Callable,
    ClassVar,
    Generic,
    Iterable,
    Iterator,
    Optional,
    TypeVar,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

from pydantic import BaseModel as PydanticModel
from pydantic import ConfigDict, create_model
from pydantic import Field as PydanticField
from pydantic.functional_serializers import PlainSerializer
from pydantic.functional_validators import BeforeValidator
from pydantic_core import PydanticUndefined
from sqlalchemy import (
    Engine,
    Select,
    and_,
    create_engine,
    desc,
    func,
)
from sqlalchemy import (
    inspect as sa_inspect,
)
from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import Field, SQLModel, select
from sqlmodel.main import SQLModelMetaclass

ModelT = TypeVar("ModelT", bound="BaseModel")
Condition = ColumnElement[bool]

_engine: Engine | None = None
_session: ContextVar[Session | None] = ContextVar("anyforce_session", default=None)
_apps: dict[str, dict[str, type["BaseModel"]]] = {}
_model_classes: set[type["BaseModel"]] = set()


@dataclass(frozen=True)
class ModelField:
    name: str
    python_type: Any
    nullable: bool = False
    default: Any = PydanticUndefined
    default_factory: Callable[[], Any] | None = None
    description: str | None = None
    max_length: int | None = None
    model: type["BaseModel"] | None = None
    is_relation: bool = False
    is_many: bool = False


def configure_engine(db_url: str) -> None:
    global _engine
    if db_url == "sqlite://:memory:":
        _engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        return
    if db_url.startswith("sqlite://"):
        _engine = create_engine(db_url, connect_args={"check_same_thread": False})
        return
    _engine = create_engine(db_url)


def engine() -> Engine:
    if _engine is None:
        raise RuntimeError("anyforce.model.init() must be called before database use")
    return _engine


def create_all() -> None:
    SQLModel.metadata.create_all(engine())


def close_engine() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _apps.clear()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = _session.get()
    if session is not None:
        yield session
        return
    with Session(engine()) as owned:
        yield owned


class Transaction:
    def __init__(self) -> None:
        self.session = Session(engine())
        self.token: Any | None = None

    async def __aenter__(self) -> Session:
        self.token = _session.set(self.session)
        return self.session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if exc_type is None:
                self.session.commit()
            else:
                self.session.rollback()
        finally:
            if self.token is not None:
                _session.reset(self.token)
            self.session.close()


class AnyforceModelMetaclass(SQLModelMetaclass):
    def __new__(
        cls,
        name: str,
        bases: tuple[type[Any], ...],
        class_dict: dict[str, Any],
        **kwargs: Any,
    ) -> Any:
        meta = class_dict.get("Meta")
        abstract = bool(getattr(meta, "abstract", False))
        is_model = any(getattr(base, "__anyforce_model__", False) for base in bases)
        if is_model and not abstract:
            kwargs.setdefault("table", True)
        new_cls = super().__new__(cls, name, bases, class_dict, **kwargs)
        new_cls.__anyforce_model__ = True
        if is_model and not abstract:
            _model_classes.add(new_cls)
        return new_cls


class Query(Generic[ModelT]):
    OPERATORS: ClassVar[set[str]] = {
        "not",
        "in",
        "not_in",
        "contains",
        "icontains",
        "startswith",
        "istartswith",
        "endswith",
        "iendswith",
        "gt",
        "gte",
        "lt",
        "lte",
        "isnull",
        "range",
    }

    def __init__(
        self,
        model: type[ModelT],
        conditions: tuple[Condition, ...] = (),
        joins: tuple[tuple[type[BaseModel], str], ...] = (),
        orderings: tuple[Any, ...] = (),
        offset_value: int | None = None,
        limit_value: int | None = None,
        selected: tuple[str, ...] = (),
        annotations: dict[str, Any] | None = None,
        group_bys: tuple[str, ...] = (),
    ) -> None:
        self.model = model
        self.conditions = conditions
        self.joins = joins
        self.orderings = orderings
        self.offset_value = offset_value
        self.limit_value = limit_value
        self.selected = selected
        self.annotations = annotations or {}
        self.group_bys = group_bys

    def clone(self, **kwargs: Any) -> "Query[ModelT]":
        return Query(
            model=cast(type[ModelT], kwargs.get("model", self.model)),
            conditions=cast(
                tuple[Condition, ...], kwargs.get("conditions", self.conditions)
            ),
            joins=cast(
                tuple[tuple[type[BaseModel], str], ...],
                kwargs.get("joins", self.joins),
            ),
            orderings=cast(tuple[Any, ...], kwargs.get("orderings", self.orderings)),
            offset_value=cast(
                int | None, kwargs.get("offset_value", self.offset_value)
            ),
            limit_value=cast(int | None, kwargs.get("limit_value", self.limit_value)),
            selected=cast(tuple[str, ...], kwargs.get("selected", self.selected)),
            annotations=cast(
                dict[str, Any], kwargs.get("annotations", self.annotations)
            ),
            group_bys=cast(tuple[str, ...], kwargs.get("group_bys", self.group_bys)),
        )

    def filter(self, *conditions: Condition, **kwargs: Any) -> "Query[ModelT]":
        query = self
        built: list[Condition] = list(conditions)
        for key, value in kwargs.items():
            condition, joins = self.condition_from_key(key, value)
            built.append(condition)
            query = query.with_joins(joins)
        return query.clone(conditions=query.conditions + tuple(built))

    def with_joins(
        self, joins: Iterable[tuple[type["BaseModel"], str]]
    ) -> "Query[ModelT]":
        merged = list(self.joins)
        for join in joins:
            if join not in merged:
                merged.append(join)
        return self.clone(joins=tuple(merged))

    def only(self, *fields: str) -> "Query[ModelT]":
        return self.clone(selected=tuple(fields))

    def offset(self, offset: int) -> "Query[ModelT]":
        return self.clone(offset_value=offset)

    def limit(self, limit: int) -> "Query[ModelT]":
        return self.clone(limit_value=limit)

    def order_by(self, *orderings: str) -> "Query[ModelT]":
        resolved: list[Any] = []
        query: Query[ModelT] = self
        for ordering in orderings:
            descending = ordering.startswith("-")
            field_name = ordering[1:] if descending else ordering
            if field_name in self.annotations:
                column = self.annotations[field_name]
            else:
                column, joins = self.resolve_column(field_name)
                query = query.with_joins(joins)
            resolved.append(desc(column) if descending else column)
        return query.clone(orderings=tuple(resolved))

    def annotate(self, **annotations: Any) -> "Query[ModelT]":
        return self.clone(annotations={**self.annotations, **annotations})

    def group_by(self, *fields: str) -> "Query[ModelT]":
        return self.clone(group_bys=tuple(fields))

    def statement(self, *columns: Any) -> Select[Any]:
        select_fn = cast(Any, select)
        stmt: Any = (
            select_fn(*columns).select_from(self.model)
            if columns
            else select_fn(self.model)
        )
        if not columns:
            for relation_name in self.model.relationship_fields():
                stmt = stmt.options(selectinload(getattr(self.model, relation_name)))
        for source, relation_name in self.joins:
            stmt = stmt.join(getattr(source, relation_name))
        if self.conditions:
            stmt = stmt.where(and_(*self.conditions))
        if self.orderings:
            stmt = stmt.order_by(*self.orderings)
        if self.offset_value is not None:
            stmt = stmt.offset(self.offset_value)
        if self.limit_value is not None:
            stmt = stmt.limit(self.limit_value)
        return cast(Select[Any], stmt)

    async def all(self) -> list[ModelT]:
        with session_scope() as session:
            return list(session.scalars(self.statement()).all())

    async def first(self) -> ModelT | None:
        with session_scope() as session:
            return session.scalars(self.statement().limit(1)).first()

    async def count(self) -> int:
        stmt = self.statement(func.count()).order_by(None).offset(None).limit(None)
        with session_scope() as session:
            return int(session.scalar(stmt) or 0)

    def __await__(self) -> Any:
        return self.all().__await__()

    async def values(self, *fields: str) -> list[dict[str, Any]]:
        columns = [self.resolve_column(field)[0].label(field) for field in fields]
        stmt = self.statement(*columns)
        with session_scope() as session:
            rows = cast(Iterable[Any], session.execute(stmt).mappings().all())
            return [dict(row) for row in rows]

    async def grouped_models(self) -> list[ModelT]:
        columns: list[Any] = []
        group_columns: list[Any] = []
        for field in self.group_bys:
            column, _ = self.resolve_column(field)
            columns.append(column.label(field))
            group_columns.append(column)
        for name, expression in self.annotations.items():
            columns.append(expression.label(name))
        if not columns:
            return await self.all()

        stmt = self.statement(*columns).order_by(None).offset(None).limit(None)
        if group_columns:
            stmt = stmt.group_by(*group_columns)
        if self.orderings:
            stmt = stmt.order_by(*self.orderings)
        if self.offset_value is not None:
            stmt = stmt.offset(self.offset_value)
        if self.limit_value is not None:
            stmt = stmt.limit(self.limit_value)

        with session_scope() as session:
            rows = cast(Iterable[Any], session.execute(stmt).mappings().all())
        objs: list[ModelT] = []
        for row in rows:
            obj = self.model()
            for key, value in row.items():
                target = key[1:-1] if key.startswith("_") and key.endswith("_") else key
                setattr(obj, target, value)
            objs.append(obj)
        return objs

    async def grouped_count(self) -> int:
        if not self.group_bys:
            return await self.count()
        columns = [self.resolve_column(field)[0] for field in self.group_bys]
        stmt = (
            self.statement(*columns)
            .order_by(None)
            .offset(None)
            .limit(None)
            .group_by(*columns)
        )
        with session_scope() as session:
            return len(session.execute(stmt).all())

    def resolve_column(
        self, field_path: str
    ) -> tuple[Any, tuple[tuple[type["BaseModel"], str], ...]]:
        parts = field_path.replace(".", "__").split("__")
        model: type[BaseModel] = self.model
        joins: list[tuple[type[BaseModel], str]] = []
        while len(parts) > 1 and parts[0] in model.relationship_fields():
            relation_name = parts.pop(0)
            relation = model.relationship_fields()[relation_name]
            if relation.model is None:
                raise NoResultFound(relation_name)
            joins.append((model, relation_name))
            model = relation.model
        field_name = parts[0]
        return getattr(model, field_name), tuple(joins)

    def condition_from_key(
        self, key: str, value: Any
    ) -> tuple[Condition, tuple[tuple[type["BaseModel"], str], ...]]:
        parts = key.replace(".", "__").split("__")
        op = "exact"
        if parts[-1] in self.OPERATORS:
            op = parts.pop()
        column, joins = self.resolve_column("__".join(parts))
        model = (
            joins[-1][0].relationship_fields()[joins[-1][1]].model
            if joins
            else self.model
        )
        if model is None:
            model = self.model
        field = model.field_definition(parts[-1])
        value = self.normalize_value(field, value)

        if op == "exact":
            condition = column == value
        elif op == "not":
            condition = column != value
        elif op == "in":
            condition = column.in_(value)
        elif op == "not_in":
            condition = ~column.in_(value)
        elif op == "contains":
            condition = column.contains(value)
        elif op == "icontains":
            condition = func.lower(column).contains(str(value).lower())
        elif op == "startswith":
            condition = column.startswith(value)
        elif op == "istartswith":
            condition = func.lower(column).startswith(str(value).lower())
        elif op == "endswith":
            condition = column.endswith(value)
        elif op == "iendswith":
            condition = func.lower(column).endswith(str(value).lower())
        elif op == "gt":
            condition = column > value
        elif op == "gte":
            condition = column >= value
        elif op == "lt":
            condition = column < value
        elif op == "lte":
            condition = column <= value
        elif op == "isnull":
            condition = column.is_(None) if value else column.is_not(None)
        elif op == "range":
            start, end = cast(tuple[Any, Any], tuple(value))
            condition = column.between(start, end)
        else:
            condition = column == value
        return cast(Condition, condition), joins

    @staticmethod
    def normalize_value(field: ModelField, value: Any) -> Any:
        typ = field.python_type
        if (
            inspect.isclass(typ)
            and issubclass(typ, (int, str))
            and hasattr(typ, "_missing_")
        ):
            if isinstance(value, list):
                return [typ(item) for item in cast(list[Any], value)]
            return typ(value)
        return value


class BaseModel(SQLModel, metaclass=AnyforceModelMetaclass):
    __anyforce_model__: ClassVar[bool] = True
    __auto_now_fields__: ClassVar[tuple[str, ...]] = tuple()
    __auto_now_add_fields__: ClassVar[tuple[str, ...]] = ("created_at",)

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(
        default_factory=datetime.now, nullable=False, index=True
    )

    class Meta:
        abstract = True

    class PydanticMeta:
        config: ClassVar[ConfigDict] = ConfigDict(
            from_attributes=True, arbitrary_types_allowed=True
        )
        validators: ClassVar[dict[str, Callable[..., Any]] | None] = None
        include: ClassVar[tuple[str, ...]] = tuple()
        exclude: ClassVar[tuple[str, ...]] = tuple()
        computed: ClassVar[tuple[str, ...]] = tuple()
        form_exclude: ClassVar[tuple[str, ...]] = tuple()
        list_exclude: ClassVar[tuple[str, ...]] = tuple()
        max_recursion: ClassVar[int] = 1

    class FormPydanticMeta(PydanticMeta):
        computed: ClassVar[tuple[str, ...]] = tuple()

    def dict(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        by_alias: bool = False,
        exclude_unset: bool = False,
        exclude_defaults: bool = False,
        exclude_none: bool = False,
    ) -> dict[str, Any]:
        return (
            self.detail()
            .model_validate(self)
            .model_dump(
                include=include,
                exclude=exclude,
                by_alias=by_alias,
                exclude_unset=exclude_unset,
                exclude_defaults=exclude_defaults,
                exclude_none=exclude_none,
            )
        )

    @classmethod
    async def update_or_create(
        cls: type[ModelT],
        defaults: dict[str, Any] | None = None,
        using_db: Any | None = None,
        **kwargs: Any,
    ) -> tuple[ModelT, bool]:
        obj = await cls.filter(**kwargs).first()
        created = obj is None
        if obj is None:
            obj = cls(**kwargs)
        if defaults:
            for key, value in defaults.items():
                setattr(obj, key, value)
        await obj.save()
        return obj, created

    @classmethod
    def fields_map(cls) -> dict[str, ModelField]:
        return {**cls.column_fields(), **cls.relationship_fields()}

    @classmethod
    def fields_db_projection(cls) -> dict[str, str]:
        return {name: name for name in cls.column_fields()}

    @classmethod
    def column_fields(cls) -> dict[str, ModelField]:
        result: dict[str, ModelField] = {}
        table = getattr(cls, "__table__", None)
        columns = getattr(table, "columns", {})
        for name, info in cls.model_fields.items():
            if name.startswith("_"):
                continue
            column = columns.get(name) if hasattr(columns, "get") else None
            result[name] = ModelField(
                name=name,
                python_type=info.annotation,
                nullable=bool(getattr(column, "nullable", False)),
                default=info.default,
                default_factory=cast(Callable[[], Any] | None, info.default_factory),
                description=info.description,
                max_length=field_max_length(info),
            )
        return result

    @classmethod
    def relationship_fields(cls) -> dict[str, ModelField]:
        try:
            relationships = cast(Any, sa_inspect(cls)).relationships
        except Exception:
            return {}
        result: dict[str, ModelField] = {}
        for relationship in relationships:
            related = cast(type[BaseModel], relationship.mapper.class_)
            result[relationship.key] = ModelField(
                name=relationship.key,
                python_type=list[related] if relationship.uselist else related,
                nullable=not relationship.uselist,
                default=None,
                model=related,
                is_relation=True,
                is_many=bool(relationship.uselist),
            )
        return result

    @classmethod
    def field_definition(cls, field_name: str) -> ModelField:
        field = cls.fields_map().get(field_name)
        if field is None:
            raise KeyError(field_name)
        return field

    @classmethod
    @lru_cache
    def list(cls) -> type[PydanticModel]:
        return cls.make_pydantic(
            name="list", required_override=False, exclude=cls.PydanticMeta.list_exclude
        )

    @classmethod
    @lru_cache
    def detail(
        cls,
        required_override: bool | None = False,
        from_models: tuple[str, ...] = (),
    ) -> type[PydanticModel]:
        return cls.make_pydantic(
            name="detail",
            required_override=required_override,
            from_models=from_models,
        )

    @classmethod
    @lru_cache
    def form(
        cls,
        required_override: bool | None = None,
        from_models: tuple[str, ...] = (),
    ) -> type[PydanticModel]:
        return cls.make_pydantic(
            name="form",
            exclude=(
                *cls.PydanticMeta.form_exclude,
                *(
                    ["created_at", "updated_at"]
                    if required_override is None
                    else ["created_at"]
                ),
                *([] if from_models else ["id"]),
            ),
            required_override=required_override,
            from_models=from_models,
            is_form=True,
        )

    @classmethod
    def make_pydantic(
        cls,
        name: str,
        include: tuple[str, ...] | None = None,
        exclude: tuple[str, ...] | None = None,
        required_override: bool | None = None,
        from_models: tuple[str, ...] = (),
        max_recursion: int | None = None,
        is_form: bool = False,
    ) -> type[PydanticModel]:
        parts = [cls.__module__, cls.__qualname__, name]
        if from_models:
            parts.append("in")
            parts += list(from_models)
        if required_override is not None:
            parts.append("required" if required_override else "optional")

        meta = cls.FormPydanticMeta if is_form else cls.PydanticMeta
        max_recursion = (
            max_recursion if max_recursion is not None else meta.max_recursion
        )
        from_models = (*from_models, cls.__qualname__)
        include = include if include is not None else meta.include
        exclude = exclude if exclude is not None else meta.exclude

        model_fields: dict[str, tuple[Any, Any]] = {}
        for field_name, field in cls.fields_map().items():
            if include and field_name not in include:
                continue
            if field_name in exclude:
                continue
            if field.is_relation:
                if len(from_models) > max_recursion or field.model is None:
                    continue
                related_pydantic = (
                    field.model.form(from_models=from_models, required_override=False)
                    if is_form
                    else field.model.detail(
                        from_models=from_models,
                        required_override=required_override or True,
                    )
                )
                relation_type = (
                    list[related_pydantic] | None
                    if field.is_many
                    else related_pydantic | None
                )
                model_fields[field_name] = (
                    relation_type,
                    PydanticField(default=None, description=field.description),
                )
                continue

            field_default = cls.pydantic_default(field, required_override)
            type_hint = field.python_type
            if field.nullable or required_override is False:
                type_hint = cls.optional_type(type_hint)
            model_fields[field_name] = (
                type_hint,
                cls.pydantic_field(
                    field_default,
                    description=field.description,
                    max_length=field.max_length,
                ),
            )

        for computed_name in meta.computed:
            f = getattr(cls, computed_name, None)
            if not f or not callable(f):
                continue
            return_type = get_type_hints(f).get("return", Any)
            model_fields[computed_name] = (
                Annotated[
                    cls.optional_type(return_type),
                    BeforeValidator(BaseModel.validate_computed),
                    PlainSerializer(BaseModel.serialize_computed),
                ],
                PydanticField(
                    description=inspect.cleandoc(f.__doc__ or ""),
                    default=None,
                ),
            )

        return create_model(
            ".".join(parts),
            __config__=meta.config,
            __validators__=meta.validators,
            **cast(Any, model_fields),
        )

    @staticmethod
    def optional_type(type_hint: Any) -> Any:
        origin = get_origin(type_hint)
        if origin in (Union, types_union()):
            args = get_args(type_hint)
            if type(None) in args:
                return type_hint
        return Optional[type_hint]

    @staticmethod
    def pydantic_field(
        default: Any,
        *,
        description: str | None = None,
        max_length: int | None = None,
    ) -> Any:
        field_factory = cast(Callable[..., Any], PydanticField)
        if callable(default):
            return field_factory(
                default_factory=cast(Callable[[], Any], default),
                description=description,
                max_length=max_length,
            )
        return field_factory(
            default=... if default is PydanticUndefined else default,
            description=description,
            max_length=max_length,
        )

    @staticmethod
    def pydantic_default(field: ModelField, required_override: bool | None) -> Any:
        if required_override is True:
            return PydanticUndefined
        if required_override is False:
            return None if field.default is PydanticUndefined else field.default
        if field.default is not PydanticUndefined:
            return field.default
        if field.default_factory is not None:
            return field.default_factory
        if field.nullable:
            return None
        return PydanticUndefined

    @staticmethod
    def ensure_time_utc_timezone(v: Any) -> Any:
        if not isinstance(v, time):
            if isinstance(v, str):
                v = time.fromisoformat(v)
            elif isinstance(v, timedelta):
                v = (datetime.min + v).time()
            else:
                return v
        if v.tzinfo is None:
            return v
        return datetime.combine(datetime.min, v).astimezone(timezone.utc).time()

    @staticmethod
    def validate_computed(v: Any) -> Any:
        if callable(v):
            return None
        return v

    @staticmethod
    def serialize_computed(v: Any) -> Any:
        if isinstance(v, BaseModel):
            v = v.list().model_validate(v)
        if isinstance(v, list):
            v = [
                e.list().model_validate(e) if isinstance(e, BaseModel) else e
                for e in cast(list[Any], v)
            ]
        return v

    @staticmethod
    def validate_relation(v: Any) -> Any:
        return v

    @staticmethod
    def validate_relations(v: Any) -> Any:
        return v

    @classmethod
    def process(
        cls, input: Any
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        dic = (
            cast(dict[str, Any], input)
            if isinstance(input, dict)
            else input.model_dump(exclude_unset=True)
        )
        for key, value in list(dic.items()):
            field = cls.column_fields().get(key)
            if field and field.python_type is str and value is not None:
                dic[key] = str(value)

        computed: dict[str, Any] = {}
        for field in cls.FormPydanticMeta.computed:
            if field in dic:
                computed[field] = dic.pop(field)

        m2ms: dict[str, Any] = {}
        for field_name, field in cls.relationship_fields().items():
            if not field.is_many:
                continue
            values = dic.pop(field_name, None)
            if values is not None:
                m2ms[field_name] = values
        return dic, computed, m2ms

    async def update_computed(self, computed: dict[str, Any]) -> None:
        for field, value in computed.items():
            setter = getattr(self, f"set_{field}", None)
            if setter is None:
                continue
            if inspect.iscoroutinefunction(setter):
                await setter(value)
            else:
                setter(value)

    async def update(self, input: Any) -> None:
        dic, computed, m2ms = self.process(input)
        await self.update_computed(computed)
        for key, value in dic.items():
            setattr(self, key, value)
        await self.save_m2ms(m2ms)
        await self.save()

    async def save_m2ms(self, m2ms: dict[str, Any]) -> None:
        if not m2ms:
            return
        active = _session.get()
        if active is not None:
            self._save_m2ms(active, m2ms)
            return
        with Session(engine()) as session:
            self._save_m2ms(session, m2ms)
            session.commit()

    def _save_m2ms(self, session: Session, m2ms: dict[str, Any]) -> None:
        owner = session.merge(self)
        for relation_name, values in m2ms.items():
            field = self.relationship_fields().get(relation_name)
            if field is None or not field.is_many or field.model is None:
                continue
            related_items: list[BaseModel] = []
            for raw in cast(list[Any], values):
                if isinstance(raw, BaseModel):
                    related_items.append(session.merge(raw))
                elif isinstance(raw, dict):
                    raw_dict = cast(dict[str, Any], raw)
                    raw_id = raw_dict.get("id")
                    if raw_id:
                        related = session.get(cast(Any, field.model), raw_id)
                        if related is None:
                            raise NoResultFound(raw_id)
                        related_items.append(related)
                    else:
                        related = field.model(**raw_dict)
                        session.add(related)
                        session.flush()
                        session.refresh(related)
                    related_items.append(related)
            setattr(owner, relation_name, related_items)
            set_committed_value(self, relation_name, related_items)
        session.flush()

    async def save(self) -> None:
        now = datetime.now()
        for field_name in self.__auto_now_add_fields__:
            if getattr(self, field_name, None) is None:
                setattr(self, field_name, now)
        for field_name in self.__auto_now_fields__:
            setattr(self, field_name, now)
        active = _session.get()
        if active is not None:
            active.add(self)
            active.flush()
            active.refresh(self)
            return
        with Session(engine()) as session:
            session.add(self)
            session.commit()
            session.refresh(self)

    async def delete(self, using_db: Any | None = None) -> None:
        active = _session.get()
        if active is not None:
            active.delete(self)
            active.flush()
            return
        with Session(engine()) as session:
            obj = session.merge(self)
            session.delete(obj)
            session.commit()

    async def fetch_related(self, *args: Any, using_db: Any | None = None) -> None:
        computed: set[str] = set(self.PydanticMeta.computed)
        for field in args:
            if not isinstance(field, str):
                continue
            if field in computed:
                f = getattr(self, field, None)
                if callable(f):
                    v = f()
                    if inspect.isawaitable(v):
                        v = await v
                    object.__setattr__(self, field, v)
                continue
            relation = self.relationship_fields().get(field)
            if relation is None:
                continue
            with session_scope() as session:
                owner = session.merge(self)
                value = getattr(owner, field)
                if relation.is_many:
                    value = list(value)
                set_committed_value(self, field, value)

    async def fetch_related_lazy(
        self, path: str, using_db: Any | None = None
    ) -> Any | None:
        instance = getattr(self, path, None)
        if isinstance(instance, BaseModel):
            return instance
        await self.fetch_related(path, using_db=using_db)
        return getattr(self, path, None)

    @staticmethod
    def normalize_field(field: str) -> str:
        return field.replace(".", "__")

    @classmethod
    def all(cls: type[ModelT]) -> Query[ModelT]:
        return Query(cls)

    @classmethod
    def filter(
        cls: type[ModelT], *conditions: Condition, **kwargs: Any
    ) -> Query[ModelT]:
        return cls.all().filter(*conditions, **kwargs)

    @classmethod
    async def create(cls: type[ModelT], **kwargs: Any) -> ModelT:
        obj = cls(**kwargs)
        await obj.save()
        return obj

    @classmethod
    @lru_cache
    def get_model(cls, model_name: str) -> type["BaseModel"] | None:
        parts = model_name.split(".")
        if len(parts) == 1:
            for app in _apps.values():
                model = app.get(model_name)
                if model:
                    return model
            return None
        return _apps.get(parts[0], {}).get(parts[1])

    @classmethod
    @lru_cache
    def get_field(
        cls, model_name: str, field_name: str
    ) -> tuple[type["BaseModel"] | None, ModelField | None]:
        model = cls.get_model(model_name)
        if model:
            return model, model.fields_map().get(field_name)
        return model, None

    @classmethod
    @lru_cache
    def get_field_model(cls, field: str) -> type["BaseModel"]:
        relation = cls.relationship_fields()[field]
        if relation.model is None:
            raise NoResultFound(field)
        return relation.model


def field_max_length(info: Any) -> int | None:
    for item in getattr(info, "metadata", []):
        max_length = getattr(item, "max_length", None)
        if isinstance(max_length, int):
            return max_length
    return None


def types_union() -> Any:
    return get_origin(int | None)


class BaseUpdateModel(BaseModel):
    __auto_now_fields__: ClassVar[tuple[str, ...]] = ("updated_at",)

    updated_at: datetime = Field(
        default_factory=datetime.now, nullable=False, index=True
    )

    class Meta(BaseModel.Meta):
        abstract = True


def init_models(models: Iterable[str], name: str = "models") -> None:
    app_models: dict[str, type[BaseModel]] = {}
    for module_name in models:
        module = importlib.import_module(module_name)
        for value in vars(module).values():
            if (
                inspect.isclass(value)
                and issubclass(value, BaseModel)
                and value in _model_classes
            ):
                app_models[value.__name__] = value
    _apps[name] = app_models
    BaseModel.get_model.cache_clear()
    BaseModel.get_field.cache_clear()
    BaseModel.get_field_model.cache_clear()
