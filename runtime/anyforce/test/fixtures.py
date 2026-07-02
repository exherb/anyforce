from __future__ import annotations

from typing import Iterable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ..api import exceptions
from ..model import close_connections, generate_schemas, init


async def init_sqlmodel(models: Iterable[str]) -> None:
    await init({"db_url": "sqlite://:memory:", "apps": {"models": {"models": models}}})


@pytest.fixture(scope="session")
async def app(models: Iterable[str]):
    await init_sqlmodel(models)
    app = FastAPI()
    exceptions.register(app)
    yield app


@pytest.fixture(scope="session")
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="session")
async def database(models: Iterable[str]):
    await init_sqlmodel(models)
    generate_schemas(False)
    yield True
    await close_connections()
