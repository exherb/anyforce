import pytest
from fastapi import status
from faker import Faker
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, NoResultFound, SQLAlchemyError

from anyforce.api import exceptions
from anyforce.model.enum import EnumMissingError

from .model import CharEnum, Model1


@pytest.mark.asyncio
async def test_exceptions(faker: Faker):
    handlers = exceptions.handlers()
    validationErrorHandle = handlers[1][1]
    try:
        Model1.detail().model_validate({})
    except ValidationError as e:
        response = await validationErrorHandle(None, e)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    ormException = handlers[2][1]
    response = await ormException(None, NoResultFound(faker.pystr()))
    assert response.status_code == status.HTTP_404_NOT_FOUND
    response = await ormException(
        None,
        IntegrityError(
            "insert",
            {},
            Exception("UNIQUE constraint failed: model.name"),
        ),
    )
    assert response.status_code == status.HTTP_409_CONFLICT
    try:
        await ormException(None, SQLAlchemyError(faker.pystr()))
    except SQLAlchemyError:
        pass

    try:
        CharEnum("c")
    except EnumMissingError as e:
        enumMissingErrorHandle = handlers[3][1]
        response = await enumMissingErrorHandle(None, e)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
