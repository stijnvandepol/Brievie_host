"""A refused request body names what is wrong, in Dutch (no database needed).

The schemas refuse a too long name or too many conditions before a service can, so
the services' own sentences never showed: every such answer was "De gegevens kloppen
niet." This checks the sentences the handler builds from the schema's error.
"""

import datetime
from typing import Literal
from uuid import UUID

import pytest
from django.http import HttpRequest
from ninja import Field, NinjaAPI, Schema
from ninja.testing import TestClient

from apps.common.errors import install_error_handlers

api = NinjaAPI(urls_namespace="test-input-errors")
install_error_handlers(api)


class Item(Schema):
    value: str = ""


class BodyIn(Schema):
    name: str = Field(default="x", min_length=1, max_length=50)
    note: str = Field(default="", max_length=100)
    conditions: list[Item] = Field(default_factory=list, max_length=20)
    role: Literal["admin", "member", "viewer"] = "member"
    expires_in_days: Literal[1, 7, 30] = 7
    deadline: datetime.date | None = None
    label_id: UUID | None = None
    count: int = 0


@api.post("/body", auth=None)
def body(request: HttpRequest, payload: BodyIn) -> int:
    return 0


client = TestClient(api)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"name": "x" * 51}, "Dit is te lang: een naam heeft hoogstens 50 tekens."),
        ({"note": "x" * 101}, "Dit is te lang: een notitie heeft hoogstens 100 tekens."),
        ({"name": ""}, "Vul een naam in."),
        ({"conditions": [{}] * 21}, "Dit zijn er te veel: hoogstens 20 voorwaarden."),
        ({"role": "owner"}, "Kies een rol: beheerder, lid of meekijker."),
        ({"expires_in_days": 3}, "Een uitnodiging is 1, 7 of 30 dagen geldig."),
        ({"deadline": "2026-02-30"}, "Deze datum bestaat niet. Schrijf hem als jjjj-mm-dd."),
        ({"label_id": "abc"}, "Een id klopt niet."),
        ({"count": "veel"}, "De gegevens kloppen niet. Controleer wat je hebt ingevuld."),
    ],
)
def test_the_message_names_the_problem(payload: dict[str, object], message: str) -> None:
    r = client.post("/body", json=payload)
    assert r.status_code == 422
    assert r.json() == {"code": "validation_error", "message": message}


def test_an_upload_sent_as_json_is_told_to_send_a_form() -> None:
    # The finding: the upload endpoint answered "Stuur de gegevens als JSON." to JSON.
    from django.http import HttpResponse
    from django.test import RequestFactory

    from apps.common.errors import ApiJsonBodyMiddleware

    middleware = ApiJsonBodyMiddleware(lambda request: HttpResponse())
    factory = RequestFactory()
    upload = factory.post(
        "/api/v1/workspaces/00000000-0000-0000-0000-000000000000/documents",
        data=b"{}",
        content_type="application/json",
    )
    other = factory.post("/api/v1/workspaces", data=b"x", content_type="text/plain")

    refused = middleware(upload)
    assert refused.status_code == 415
    assert refused.content.decode() == (
        '{"code": "unsupported_media_type", "message": '
        '"Stuur de brief als formulier met bestanden (multipart/form-data)."}'
    )
    assert "als JSON" in middleware(other).content.decode()
