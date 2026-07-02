from typing import Any, Hashable, MutableMapping, cast

from starlette_context import context


def get(k: Hashable) -> Any:
    data = cast(MutableMapping[Hashable, Any], cast(Any, context).data)
    return data.get(k)


def set(k: Hashable, v: Any):
    data = cast(MutableMapping[Hashable, Any], cast(Any, context).data)
    data[k] = v


def user():
    return get("user")


def set_user(user: Any):
    return set("user", user)
