from datetime import datetime
from decimal import Decimal
from typing import Any, cast

import orjson
from dateutil.parser import parse as parse_datetime
from fastapi.encoders import jsonable_encoder


def fast_dumps_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError


def fast_dumps(o: Any) -> str:
    return orjson.dumps(o, default=fast_dumps_default).decode("utf-8")


def raw_dumps(o: Any) -> bytes:
    return orjson.dumps(o, default=jsonable_encoder)


def dumps(
    o: Any,
    skipkeys: bool = False,
    ensure_ascii: bool = False,
    check_circular: bool = False,
    allow_nan: bool = False,
    cls: Any = None,
    indent: int | None = None,
    separators: tuple[str, str] | None = None,
    default: Any = jsonable_encoder,
    sort_keys: bool = False,
) -> str:
    option = 0
    if indent == 2:
        option |= orjson.OPT_INDENT_2
    if sort_keys:
        option |= orjson.OPT_SORT_KEYS
    return orjson.dumps(o, default=default, option=option).decode("utf-8")


def parse_iso_datetime(s: str) -> datetime:
    return parse_datetime(s)


def decoder(input: Any) -> Any:
    if isinstance(input, dict):
        input = cast(dict[Any, Any], input)
        for k, v in input.items():
            input[k] = decoder(v)
    elif isinstance(input, list):
        input = cast(list[Any], input)
        for i, v in enumerate(input):
            input[i] = decoder(v)
    elif isinstance(input, str) and input.find("T") == 10:
        try:
            return parse_iso_datetime(input)
        except ValueError:
            pass
    return input


def loads(raw: bytes | bytearray | str):
    o = orjson.loads(raw)
    return decoder(o)
