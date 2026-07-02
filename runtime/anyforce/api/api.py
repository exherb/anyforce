import asyncio
from copy import copy
from datetime import datetime
from decimal import Decimal
from enum import IntEnum
from typing import Any, Callable, Coroutine, Generic, Iterable, TypeVar, cast

from dateutil.parser import parse as parse_datetime
from fastapi import (
    APIRouter,
    Body,
    Depends,
    Path,
    Request,
    status,
)
from fastapi import (
    Query as FastAPIQuery,
)
from pydantic import BaseModel as PydanticBaseModel
from pydantic import create_model
from sqlalchemy import func
from sqlalchemy.sql.elements import ColumnElement

from .. import json
from ..model import BaseModel
from ..model.base import Condition, ModelField, Query
from ..model.functions import in_transaction
from .exceptions import (
    HTTPForbiddenError,
    HTTPNotFoundError,
    HTTPPreconditionRequiredError,
)

UserModel = TypeVar("UserModel")
Model = TypeVar("Model", bound=BaseModel)
CreateForm = TypeVar("CreateForm", bound=PydanticBaseModel)
UpdateForm = TypeVar("UpdateForm", bound=PydanticBaseModel)


class ResourceMethod(IntEnum):
    list = 0
    get = 1
    create = 2
    put = 3
    delete = 4


class DeleteResponse(PydanticBaseModel):
    id: str | int | None = None


class API(Generic[UserModel, Model, CreateForm, UpdateForm]):
    def __init__(
        self,
        model: type[Model],
        create_form: type[CreateForm],
        update_form: type[UpdateForm],
        get_current_user: Callable[..., Coroutine[Any, Any, UserModel] | UserModel],
        enable_create: bool = True,
        enable_update: bool = True,
        enable_delete: bool = True,
        enable_get: bool = True,
        enable_summary: bool = False,
    ) -> None:
        self.model = model
        self.create_form = create_form
        self.update_form = update_form
        self.get_current_user = get_current_user
        self.enable_create = enable_create
        self.enable_update = enable_update
        self.enable_delete = enable_delete
        self.enable_get = enable_get
        self.enable_summary = enable_summary

    async def translate_id(self, user: UserModel, id: str, request: Request) -> str:
        return id

    def translate_order_by(
        self, user: UserModel, ordering: str, request: Request
    ) -> list[str]:
        return [self.model.normalize_field(ordering)]

    async def translate_condition(
        self, user: UserModel, q: Query[Model], k: str, v: Any, request: Request
    ) -> Any:
        return v

    def group_by_f(self, group_by: str, field: ModelField) -> ColumnElement[Any] | None:
        if field.is_relation or field.python_type not in (int, float, Decimal):
            return None
        return cast(
            ColumnElement[Any],
            func.sum(getattr(self.model, group_by)),
        )

    def group_by(
        self,
        user: UserModel,
        q: Query[Model],
        include: Iterable[str],
        group_by: list[str],
        order_by: list[str],
    ) -> Query[Model]:
        field_names = include or self.model.fields_map().keys()
        annotates: dict[str, ColumnElement[Any]] = {}
        for field_name in field_names:
            if field_name in group_by or field_name not in self.model.fields_map():
                continue
            field = self.model.fields_map()[field_name]
            annotate = self.group_by_f(field_name, field)
            if annotate is None:
                continue
            group_by_name = f"_{field_name}_"
            annotates[group_by_name] = annotate
            for i, ordering in enumerate(order_by):
                if ordering == field_name or ordering == f"-{field_name}":
                    order_by[i] = ordering.replace(field_name, group_by_name)
        return q.annotate(**annotates).group_by(*group_by)

    async def grouping(
        self,
        user: UserModel,
        q: Query[Model],
        include: Iterable[str],
        group_by: list[str],
        order_by: list[str] | None = None,
    ) -> list[Model]:
        return await self.grouping_q(
            self.group_by(user, q, include, group_by, order_by or []), group_by
        )

    async def grouping_q(self, q: Query[Model], group_by: list[str]) -> list[Model]:
        return await q.grouped_models()

    async def excludes(self, method: ResourceMethod) -> set[str] | None:
        return None

    async def q(
        self,
        user: UserModel,
        request: Request,
        q: Query[Model],
        method: ResourceMethod,
    ) -> Query[Model]:
        excludes = await self.excludes(method)
        if not excludes:
            return q
        include = set(q.selected or self.model.fields_db_projection().keys()) - excludes
        if not include:
            raise HTTPForbiddenError
        return q.only(*include)

    async def fetch_related(
        self, obj: Model, method: ResourceMethod, prefetch: list[str]
    ) -> None:
        excludes = await self.excludes(method)
        if excludes:
            prefetch = list(set(prefetch) - excludes)
        if prefetch:
            await obj.fetch_related(*prefetch)

    async def before_create(
        self,
        user: UserModel,
        input: CreateForm,
        request: Request,
    ) -> Any:
        return input

    async def before_save(
        self,
        user: UserModel,
        obj: Model,
        input: Any,
        request: Request,
    ) -> Model:
        return obj

    async def after_create(
        self, user: UserModel, obj: Model, input: Any, request: Request
    ) -> Any:
        return obj

    async def before_update(
        self, user: UserModel, obj: Model, input: UpdateForm, request: Request
    ) -> Model | None:
        return obj

    async def after_update(
        self,
        user: UserModel,
        old_obj: Model,
        input: UpdateForm,
        obj: Model,
        request: Request,
    ) -> Any:
        return obj

    async def before_delete(
        self, user: UserModel, obj: Model, request: Request
    ) -> Model:
        return obj

    @classmethod
    def ids_path(cls) -> Any:
        return Path(..., title="ids", description="逗号分隔的 id")

    @classmethod
    def include_query(cls) -> Any:
        return FastAPIQuery(
            [],
            title="包含字段",
            description="支持采用 `include=id&include=user.id` 形式传入多个",
        )

    @classmethod
    def prefetch_query(cls) -> Any:
        return FastAPIQuery(
            [],
            title="预加载字段",
            description="支持采用 `prefetch=id&prefetch=user.id` 形式传入多个",
        )

    @classmethod
    def group_by_query(cls) -> Any:
        return FastAPIQuery(
            [],
            title="分组字段",
            description="支持采用 `group_by=id&group_by=user.id` 形式传入多个",
        )

    @classmethod
    def get_form_type(cls, form: Any) -> Any:
        f = getattr(form, "form_type", None)
        return f and f() or Body(...)

    @property
    def connection_name(self) -> str:
        return "default"

    async def get(
        self,
        ids: str,
        include: list[str],
        user: UserModel,
        request: Request,
        method: ResourceMethod,
    ) -> list[Model]:
        normalize_ids = await asyncio.gather(
            *[self.translate_id(user, id.strip(), request) for id in ids.split(",")]
        )
        q = await self.q(
            user,
            request,
            self.model.filter(id__in=normalize_ids),
            method,
        )
        if include:
            q = q.only(*include)
        objs = await q.all()
        if len(objs) != len(normalize_ids):
            raise HTTPNotFoundError
        return objs

    async def translate_kv_condition(
        self, user: UserModel, request: Request, q: Query[Model], kv: dict[str, Any]
    ) -> tuple[Query[Model], Condition]:
        join_infos: dict[str, tuple[str, bool]] = {
            ".and": ("and", False),
            ".or": ("or", False),
            ".not": ("and", True),
            ".not_or": ("or", True),
        }

        conditions: list[Condition] = []
        for k, v in kv.items():
            child_join_type, child_reverse = join_infos.get(k, ("", False))
            if child_join_type:
                child_conditions: list[Condition] = []
                values: list[Any] = cast(list[Any], v) if isinstance(v, list) else [v]
                for child in values:
                    if isinstance(child, dict):
                        q, child_condition = await self.translate_kv_condition(
                            user, request, q, cast(dict[str, Any], child)
                        )
                        child_conditions.append(child_condition)
                if not child_conditions:
                    continue
                condition = (
                    sa_or(*child_conditions)
                    if child_join_type == "or"
                    else sa_and(*child_conditions)
                )
                conditions.append(~condition if child_reverse else condition)
                continue

            field = self.model.normalize_field(k)
            translated = await self.translate_condition(user, q, field, v, request)
            if isinstance(translated, Query):
                q = cast(Query[Model], translated)
                continue
            if isinstance(translated, ColumnElement):
                conditions.append(cast(Condition, translated))
                continue
            if (
                translated is None
                or (isinstance(translated, list) and not translated)
                or (isinstance(translated, dict) and not translated)
            ):
                continue
            condition, joins = q.condition_from_key(field, translated)
            q = q.with_joins(joins)
            conditions.append(condition)

        return q, sa_and(*conditions) if conditions else sa_and()

    def bind(self, router: APIRouter) -> dict[str, Callable[..., Any]]:
        list_exclude = set(self.model.PydanticMeta.list_exclude or [])

        ListPydanticModel = self.model.list()
        DetailPydanticModel = self.model.detail()
        DetailPydanticModels = list[DetailPydanticModel] | DetailPydanticModel

        Response = create_model(
            f"{self.model.__module__}.{self.model.__name__}.Response",
            __base__=PydanticBaseModel,
            total=(int, 0),
            summary=(ListPydanticModel | None, None),
            data=(list[ListPydanticModel], ...),
        )

        methods: dict[str, Callable[..., Any]] = {}

        if self.enable_create:

            @router.post(
                "/",
                response_model=DetailPydanticModels,
                status_code=status.HTTP_201_CREATED,
                response_model_exclude_unset=True,
                response_model_exclude_none=True,
            )
            async def create(
                request: Request,
                input: Any = Body(...),
                prefetch: list[str] = self.prefetch_query(),
                current_user: UserModel = Depends(self.get_current_user),
            ) -> Any:
                async with in_transaction(self.connection_name):
                    is_batch = isinstance(input, list)
                    inputs: list[Any] = cast(list[Any], input) if is_batch else [input]
                    returns: list[PydanticBaseModel] = []
                    for raw_item in inputs:
                        item = self.create_form.model_validate(raw_item)
                        item = await self.before_create(current_user, item, request)
                        raw, computed, m2ms = self.model.process(item)
                        obj = await self.before_save(
                            current_user, self.model(**raw), item, request
                        )
                        await obj.save()
                        await obj.update_computed(computed)
                        await obj.save_m2ms(m2ms)
                        if prefetch:
                            await self.fetch_related(
                                obj, ResourceMethod.create, prefetch
                            )
                        obj_rtn = await self.after_create(
                            current_user, obj, item, request
                        )
                        obj = obj_rtn or obj
                        returns.append(
                            obj
                            if isinstance(obj, PydanticBaseModel)
                            and not isinstance(obj, BaseModel)
                            else DetailPydanticModel.model_validate(obj)
                        )
                    return returns if is_batch else returns[0]

            methods["create"] = create

        if self.enable_get:
            help_text = """
支持 JSON 查询条件。

```javascript
{ "email": "name@example.com", "name.contains": "name", "id.in": [1, 2, 3] }
```
"""

            @router.get(
                "/",
                response_model=Response,
                response_model_exclude_unset=True,
                response_model_exclude_none=True,
            )
            async def index(
                request: Request,
                offset: int = FastAPIQuery(0, title="分页偏移"),
                limit: int = FastAPIQuery(20, title="分页限额"),
                include_summary: bool = FastAPIQuery(
                    False, title="是否包含summary数据"
                ),
                condition: list[str] = FastAPIQuery(
                    [], title="查询条件", description=help_text
                ),
                order_by: list[str] = FastAPIQuery(
                    [],
                    title="排序",
                    description="支持采用 `order_by=id&order_by=user.id` 形式传入多个",
                ),
                include: list[str] = self.include_query(),
                prefetch: list[str] = self.prefetch_query(),
                group_by: list[str] = self.group_by_query(),
                current_user: UserModel = Depends(self.get_current_user),
            ) -> Any:
                q = self.model.all()
                if not include and list_exclude:
                    include = list(
                        set(self.model.fields_db_projection().keys()) - list_exclude
                    )
                if include:
                    q = q.only(*include)

                q = await self.q(current_user, request, q, ResourceMethod.list)
                for raw in condition:
                    kv = json.loads(raw)
                    q, iq = await self.translate_kv_condition(
                        current_user, request, q, kv
                    )
                    q = q.filter(iq)

                summary: Model | None = None
                if self.enable_summary or include_summary:
                    objs = await self.grouping(current_user, q, include, [])
                    if objs:
                        summary = objs[0]

                if group_by:
                    q = self.group_by(current_user, q, include, group_by, order_by)
                    total = await q.grouped_count()
                else:
                    total = await q.count()

                if order_by:
                    orderings: list[str] = []
                    for item in order_by:
                        orderings += self.translate_order_by(
                            current_user, item, request
                        )
                    q = q.order_by(*orderings)

                q = q.offset(offset).limit(limit)
                objs = await self.grouping_q(q, group_by) if group_by else await q

                if prefetch:
                    for obj in objs:
                        await self.fetch_related(obj, ResourceMethod.list, prefetch)
                    if summary:
                        await self.fetch_related(summary, ResourceMethod.list, prefetch)

                return Response(
                    total=total,
                    summary=summary and ListPydanticModel.model_validate(summary),
                    data=[ListPydanticModel.model_validate(obj) for obj in objs],
                )

            @router.get(
                "/{id}",
                response_model=DetailPydanticModel,
                response_model_exclude_unset=True,
                response_model_exclude_none=True,
            )
            async def get(
                request: Request,
                id: str = Path(..., title="id"),
                include: list[str] = self.include_query(),
                prefetch: list[str] = self.prefetch_query(),
                current_user: UserModel = Depends(self.get_current_user),
            ) -> Any:
                id = await self.translate_id(current_user, id, request)
                q = await self.q(
                    current_user,
                    request,
                    self.model.all().filter(id=id),
                    ResourceMethod.get,
                )
                if include:
                    q = q.only(*include)
                obj = await q.first()
                if not obj:
                    raise HTTPNotFoundError
                if prefetch:
                    await self.fetch_related(obj, ResourceMethod.get, prefetch)
                return DetailPydanticModel.model_validate(obj)

            methods["index"] = index
            methods["get"] = get

        if self.enable_update:

            @router.put(
                "/{ids}",
                response_model=DetailPydanticModels,
                response_model_exclude_unset=True,
                response_model_exclude_none=True,
            )
            async def update(
                request: Request,
                ids: str = self.ids_path(),
                input: Any = Body(...),
                include: list[str] = self.include_query(),
                prefetch: list[str] = self.prefetch_query(),
                current_user: UserModel = Depends(self.get_current_user),
            ) -> Any:
                async with in_transaction(self.connection_name):
                    input = self.update_form.model_validate(input)
                    returns: list[Any] = []
                    excludes = await self.excludes(ResourceMethod.put) or set()
                    for obj in await self.get(
                        ids, include, current_user, request, ResourceMethod.put
                    ):
                        r = await self.before_update(current_user, obj, input, request)
                        if r:
                            obj = r
                        raw = input.model_dump(exclude_unset=True, exclude=excludes)
                        updated_at = raw.pop("updated_at", None)
                        if updated_at:
                            if isinstance(updated_at, str):
                                updated_at = parse_datetime(updated_at)
                            if isinstance(updated_at, datetime):
                                obj_updated_at = cast(
                                    datetime | None, getattr(obj, "updated_at", None)
                                )
                                if obj_updated_at:
                                    obj_updated_at = obj_updated_at.replace(
                                        microsecond=0
                                    )
                                if obj_updated_at and obj_updated_at > updated_at:
                                    raise HTTPPreconditionRequiredError

                        old_obj = copy(obj)
                        if raw:
                            await obj.update(raw)
                        if prefetch:
                            await self.fetch_related(obj, ResourceMethod.put, prefetch)
                        obj_rtn = await self.after_update(
                            current_user, old_obj, input, obj, request
                        )
                        obj = obj_rtn or obj
                        returns.append(
                            obj
                            if isinstance(obj, PydanticBaseModel)
                            and not isinstance(obj, BaseModel)
                            else DetailPydanticModel.model_validate(obj)
                        )
                    return returns if len(returns) > 1 else returns[0]

            methods["update"] = update

        if self.enable_delete:

            @router.delete(
                "/{ids}",
                response_model=list[DeleteResponse] | DeleteResponse,
                response_model_exclude_unset=True,
                response_model_exclude_none=True,
            )
            async def delete(
                request: Request,
                ids: str = self.ids_path(),
                current_user: UserModel = Depends(self.get_current_user),
            ) -> list[DeleteResponse] | DeleteResponse:
                async with in_transaction(self.connection_name):
                    rs: list[DeleteResponse] = []
                    for obj in await self.get(
                        ids, [], current_user, request, ResourceMethod.delete
                    ):
                        obj = await self.before_delete(current_user, obj, request)
                        await obj.delete()
                        rs.append(DeleteResponse(id=obj.id))
                    return rs if len(rs) > 1 else rs[0]

            methods["delete"] = delete

        return methods


def get_anonymous_user() -> str:
    return "anonymous"


class PublicAPI(API[str, Model, CreateForm, UpdateForm]):
    def __init__(
        self,
        model: type[Model],
        create_form: type[CreateForm],
        update_form: type[UpdateForm],
        enable_create: bool = True,
        enable_update: bool = True,
        enable_delete: bool = True,
        enable_get: bool = True,
    ) -> None:
        super().__init__(
            model,
            create_form,
            update_form,
            get_anonymous_user,
            enable_create=enable_create,
            enable_update=enable_update,
            enable_delete=enable_delete,
            enable_get=enable_get,
        )


def sa_and(*conditions: Condition) -> Condition:
    from sqlalchemy import and_

    return and_(*conditions)


def sa_or(*conditions: Condition) -> Condition:
    from sqlalchemy import or_

    return or_(*conditions)
