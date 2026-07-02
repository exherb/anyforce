from __future__ import annotations

import re
from typing import Any, Callable, Iterable, cast

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError, ValidationException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from pydantic_core import ErrorDetails
from sqlalchemy.exc import IntegrityError, NoResultFound, SQLAlchemyError

from ..logging import get_logger
from ..model.enum import EnumMissingError

logger = get_logger(__name__)


class ErrorDetail(BaseModel):
    errors: Any


class ErrorResponse(BaseModel):
    detail: ErrorDetail


def error_response(errors: Any, status_code: int) -> JSONResponse:
    return JSONResponse(
        ErrorResponse(detail=ErrorDetail(errors=errors)).model_dump(),
        status_code=status_code,
    )


HTTPForbiddenError = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN, detail={"errors": "禁止访问"}
)
HTTPNotFoundError = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND, detail={"errors": "不存在"}
)
HTTPUnAuthorizedError = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail={"errors": "未认证"}
)
HTTPPreconditionRequiredError = HTTPException(
    status_code=status.HTTP_428_PRECONDITION_REQUIRED,
    detail={"errors": "请求数据已过期"},
)

validation_error_translation: dict[str, str] = {
    "bool_parsing": "无法解析为布尔值",
    "bool_type": "不是有效的布尔值",
    "date_parsing": "无法解析为日期",
    "datetime_parsing": "无法解析为日期时间",
    "decimal_parsing": "无法解析为数字",
    "enum": "不是有效的枚举值",
    "float_parsing": "无法解析为浮点数",
    "int_parsing": "无法解析为整数",
    "json_invalid": "不是有效的 JSON",
    "missing": "必填",
    "string_too_long": "太长",
    "too_long": "太长",
    "too_short": "太短",
    "url_parsing": "无法解析为链接",
    "uuid_parsing": "无法解析为 UUID",
    "value_error": "不是有效的值",
}


def translate_validation_exception(e: ValidationException) -> list[str]:
    msgs: set[str] = set()
    for child_e in e.errors():
        if isinstance(child_e, ValidationError):
            msgs.update(translate_validation_error(child_e))
        elif isinstance(child_e, dict):
            msgs.add(translate_validation_error_detail(cast(dict[str, Any], child_e)))
        else:
            msgs.add(str(child_e))
            logger.bind(e=child_e, raw_error_type=type(child_e)).warn("not translate")
    return list(msgs)


def translate_validation_error(e: ValidationError) -> set[str]:
    msgs: set[str] = set()
    for error in e.errors():
        msgs.add(translate_validation_error_detail(error))
    return msgs


def translate_validation_error_detail(e: dict[str, Any] | ErrorDetails) -> str:
    typ = str(e.get("type", ""))
    msg = str(e.get("msg", ""))
    value = e.get("input") or ""
    path = ".".join([str(x) for x in e.get("loc", [])])
    translate_msg = validation_error_translation.get(typ)
    if not translate_msg:
        logger.bind(raw_error=e, raw_error_type=type(e), msg=msg).info(
            "translate exception"
        )
    return f"{value} {translate_msg or msg}: {path}"


def translate_validate_error(e: ValidationError | list[ValidationError]) -> list[str]:
    msgs: set[str] = set()
    errors = e if isinstance(e, list) else [e]
    for error in errors:
        msgs.update(translate_validation_error(error))
    return list(msgs)


def translate_orm_error(e: SQLAlchemyError | NoResultFound) -> tuple[int, list[str]]:
    if isinstance(e, IntegrityError):
        status_code = status.HTTP_400_BAD_REQUEST
        args: list[str] = []
        for arg in e.args:
            matched = None
            for regexp in (
                ".*Duplicate entry '(.+)' for key.*",
                ".*UNIQUE constraint failed: .*",
            ):
                matched = re.match(regexp, str(arg))
                if matched:
                    status_code = status.HTTP_409_CONFLICT
                    value = matched.groups()[0] if matched.groups() else ""
                    args.append(f"值为 {value} 的对象已存在" if value else "对象已存在")
                    break
            if not matched:
                logger.bind(arg=arg, type=type(arg)).warn("not translate")
                args.append(str(arg))
        return status_code, args
    if isinstance(e, NoResultFound):
        return status.HTTP_404_NOT_FOUND, ["不存在"]
    raise e


async def valueExceptionHandle(
    request: Request | None, exc: ValueError
) -> JSONResponse:
    msg = str(exc)
    regexps = [
        (r"invalid literal for int\(\) with base 10: '(.+)'", "{0} 不是有效的整数"),
        (r"could not convert string to float: '(.+)'", "{0} 不是有效的浮点数"),
    ]
    for regexp, msg_template in regexps:
        matched = re.match(regexp, msg)
        if matched:
            msg = msg_template.format(*matched.groups())
            break
    return error_response(msg, status.HTTP_400_BAD_REQUEST)


async def validationExceptionHandle(
    request: Request | None, exc: ValidationException
) -> JSONResponse:
    return error_response(
        translate_validation_exception(exc), status.HTTP_400_BAD_REQUEST
    )


async def validationErrorHandle(
    request: Request | None, exc: ValidationError
) -> JSONResponse:
    return error_response(translate_validate_error(exc), status.HTTP_400_BAD_REQUEST)


async def ormException(
    request: Request | None, exc: SQLAlchemyError | NoResultFound
) -> JSONResponse:
    status_code, msgs = translate_orm_error(exc)
    return error_response(msgs, status_code)


async def enumMissingErrorHandle(
    request: Request | None, exc: EnumMissingError
) -> JSONResponse:
    return error_response([str(args) for args in exc.args], status.HTTP_400_BAD_REQUEST)


def handlers() -> tuple[
    tuple[Iterable[type[Exception]], Callable[..., Any]],
    tuple[Iterable[type[Exception]], Callable[..., Any]],
    tuple[Iterable[type[Exception]], Callable[..., Any]],
    tuple[Iterable[type[Exception]], Callable[..., Any]],
    tuple[Iterable[type[Exception]], Callable[..., Any]],
]:
    return (
        ([ValueError], valueExceptionHandle),
        ([RequestValidationError, ValidationError], validationErrorHandle),
        ([SQLAlchemyError, NoResultFound], ormException),
        ([EnumMissingError], enumMissingErrorHandle),
        ([RequestValidationError], validationExceptionHandle),
    )


def register(app: FastAPI) -> None:
    for errors, handler in handlers():
        for error in errors:
            app.exception_handler(error)(handler)
