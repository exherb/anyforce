from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.testclient import TestClient

from .model import Article, Model1, Model2


async def test_computed_form_setter(database: bool) -> None:
    assert database
    obj = await Model2.create(
        char_enum_field="a",
        required_char_field="computed-setter",
        int_field=4,
        bigint_field=1,
        text_field="https://example.com/",
        json_field=[],
    )

    form = Model2.form(required_override=False).model_validate(
        {"int_field_plus_bigint_field": 10}
    )
    await obj.update(form)

    assert obj.bigint_field == 6


async def test_list_group_by_sums(
    client: TestClient, database: bool, router: APIRouter
) -> None:
    assert database
    assert router
    marker = "group-sum"
    await Model2.create(
        char_enum_field="a",
        required_char_field=f"{marker}-1",
        int_field=2,
        bigint_field=3,
        text_field="https://example.com/",
        json_field=[],
    )
    await Model2.create(
        char_enum_field="a",
        required_char_field=f"{marker}-2",
        int_field=5,
        bigint_field=7,
        text_field="https://example.com/",
        json_field=[],
    )

    response = client.get(
        "/models/",
        params=[
            ("condition", json.dumps({"required_char_field.contains": marker})),
            ("include", "char_enum_field"),
            ("include", "int_field"),
            ("include", "bigint_field"),
            ("group_by", "char_enum_field"),
        ],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["data"][0]["int_field"] == 7
    assert body["data"][0]["bigint_field"] == 10


async def test_list_filter_cross_table(
    client: TestClient, database: bool, router: APIRouter
) -> None:
    assert database
    assert router
    parent = await Model1.create(name="cross-parent")
    await Model2.create(
        char_enum_field="b",
        required_char_field="cross-child",
        int_field=1,
        bigint_field=1,
        text_field="https://example.com/",
        json_field=[],
        model1_field_id=parent.id,
    )

    response = client.get(
        "/models/",
        params={
            "condition": json.dumps({"model1_field.name": "cross-parent"}),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["data"][0]["required_char_field"] == "cross-child"


async def test_many_to_many_save_and_fetch(database: bool) -> None:
    assert database
    article = await Article.create(title="m2m-article")
    await article.save_m2ms(
        {
            "tags": [
                {"name": "m2m-a"},
                {"name": "m2m-b"},
            ]
        }
    )

    await article.fetch_related("tags")

    assert [tag.name for tag in article.tags] == ["m2m-a", "m2m-b"]
    assert "tags" in Article.form().model_fields
