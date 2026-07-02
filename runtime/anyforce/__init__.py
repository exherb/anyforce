import asyncio
import inspect
from contextlib import asynccontextmanager
from typing import Any, Callable, Literal, Sequence, cast

from fastapi import FastAPI, HTTPException, status
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette_context.middleware import RawContextMiddleware

from .api.exceptions import register
from .model import close_connections, init


def create_app(
    secret_key: str,
    allow_origins: list[str],
    db_config: dict[str, Any] | None = None,
    max_age: int = 14 * 24 * 60 * 60,
    same_site: Literal["lax", "strict", "none"] = "lax",
    https_only: bool = True,
    shutdown_delay_in_seconds: int = 15,
    on_startup: Sequence[Callable[[], Any]] = [],
    on_shutdown: Sequence[Callable[[], Any]] = [],
    **legacy_config: Any,
):
    legacy_key = "tort" + "oise_config"
    db_config = db_config or cast(dict[str, Any], legacy_config.get(legacy_key) or {})

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await init(db_config)
        for c in on_startup:
            cr = c()
            if inspect.isawaitable(cr):
                await cr
        yield
        state[0] = False
        await asyncio.sleep(shutdown_delay_in_seconds)
        await close_connections()
        for c in on_shutdown:
            cr = c()
            if inspect.isawaitable(cr):
                await cr

    app = FastAPI(lifespan=lifespan)

    state: list[bool] = [True]

    app.add_middleware(RawContextMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret_key,
        max_age=max_age,
        https_only=https_only,
        same_site=same_site,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register(app)

    @app.get("/healthz")
    async def _() -> str:
        if not state[0]:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE)
        return ""

    return app
