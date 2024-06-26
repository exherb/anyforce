import inspect
from typing import Any, Dict, List, Optional, Tuple, Type, cast

from pydantic import validate_model
from pydantic.config import Extra
from pydantic.fields import SHAPE_LIST, SHAPE_SINGLETON, ModelField
from tortoise import fields
from tortoise.contrib.pydantic.base import PydanticModel
from tortoise.exceptions import NoValuesFetched
from tortoise.fields import DatetimeField
from tortoise.fields.base import Field
from tortoise.fields.relational import NoneAwaitable
from tortoise.models import Model
from tortoise.queryset import AwaitableQuery


def patch_pydantic(
    cls: type[Model],
    model: Type[PydanticModel],
    from_models: Tuple[str, ...] = (),
    required_override: Optional[bool] = None,
    is_form: bool = False,
    max_recursion: int = 1,
) -> Type[PydanticModel]:
    model_meta = getattr(cls, "_meta")
    fields_map: Dict[str, Field[Any]] = (
        getattr(model_meta, "fields_map") if model_meta else {}
    )

    # 解决数据动态加载的问题
    model_fields: Dict[str, ModelField] = {}
    model.__config__.extra = Extra.ignore
    if not is_form:
        for k, field in model.__config__.fields.items():
            if isinstance(field, dict):
                field.pop("max_length", None)
    for k, field in model.__fields__.items():
        if not is_form and field.field_info.max_length:
            field.field_info.max_length = None
            field.validators = [
                validator
                for validator in field.validators
                if validator.__name__ not in ["constr_length_validator"]
            ]
            if getattr(field.type_, "max_length"):
                setattr(field.type_, "max_length", None)
            setattr(field.outer_type_, "__modify_schema__", None)

        config = getattr(field.type_, "__config__", None)
        if config:
            orig_model: Optional[Any] = getattr(config, "orig_model", None)
            if orig_model:
                if len(from_models) > max_recursion:
                    if is_form:
                        continue

                    field = ModelField.infer(
                        name=k,
                        value=None,
                        annotation=Optional[Any],
                        class_validators=None,
                        config=config,
                    )
                else:
                    if field.shape == SHAPE_SINGLETON:
                        k_id = f"{k}_id"
                        model_fields[k_id] = ModelField.infer(
                            name=k_id,
                            value=None,
                            annotation=int
                            if (
                                field.required
                                if required_override is None
                                else required_override
                            )
                            else Optional[int],
                            class_validators=None,
                            config=config,
                        )

                        if is_form:
                            continue

                    if is_form:
                        field_pydantic_model = orig_model.form(
                            from_models=from_models,
                            required_override=False,
                        )
                    else:
                        field_pydantic_model = orig_model.detail(
                            from_models=from_models,
                            required_override=required_override,
                        )
                    if field.shape == SHAPE_LIST:
                        field = ModelField.infer(
                            name=k,
                            value=None,
                            annotation=Optional[List[field_pydantic_model]],
                            class_validators=None,
                            config=config,
                        )
                    elif field.shape == SHAPE_SINGLETON:
                        field = ModelField.infer(
                            name=k,
                            value=None,
                            annotation=Optional[field_pydantic_model],
                            class_validators=None,
                            config=config,
                        )

        if field.allow_none:
            field.required = False
        else:
            field.allow_none = True
        model_fields[k] = field
        if required_override is not None:
            field.required = required_override
            if not field.required:
                field.default = None
            continue

        if not field.required:
            continue

        db_field = fields_map.get(k)
        if db_field and isinstance(db_field, DatetimeField):
            if db_field.auto_now or db_field.auto_now_add:
                field.required = False
                continue

        try:
            field.required = not issubclass(field.type_, PydanticModel)
        except TypeError:
            pass
    model.__fields__ = model_fields

    def from_orm(obj: Any) -> PydanticModel:
        obj = model._decompose_class(obj)  # type: ignore
        new_obj: Dict[str, Any] = {}
        for k, v in obj.items():
            if k not in model.__fields__:
                continue

            if (
                isinstance(v, AwaitableQuery)
                or inspect.iscoroutinefunction(v)
                or v is NoneAwaitable
            ):
                continue
            if isinstance(v, fields.ReverseRelation):
                try:
                    v = cast(Any, v)
                    v: Any = list(v)
                except NoValuesFetched:
                    continue
            new_obj[k] = v

        m = model()
        values, fields_set, validation_error = validate_model(model, new_obj)
        if validation_error:
            raise validation_error
        object.__setattr__(m, "__dict__", values)
        object.__setattr__(m, "__fields_set__", fields_set)
        getattr(m, "_init_private_attributes")()
        return m

    def to_dict(self: model, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        if kwargs and kwargs.get("exclude_none") is not None:
            kwargs["exclude_none"] = True
        return super(model, self).dict(*args, **kwargs)

    model.from_orm = from_orm
    model.dict = to_dict

    return model
