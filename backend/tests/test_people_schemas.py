"""What a person update leaves out keeps its stored value (no database needed).

The schema cannot tell "omitted" from `false` by type: `archived` stays a plain boolean
with a default, because the released contract fixes its definition and nullable inputs
must have a default anyway. The fields a client sent are in `model_fields_set`, which
survives Ninja's body parsing; this test proves that.
"""

from django.http import HttpRequest
from ninja import NinjaAPI
from ninja.testing import TestClient

from apps.people.api import sent_fields
from apps.people.schemas import PersonIn

api = NinjaAPI(urls_namespace="test-person-fields")


@api.put("/echo", auth=None)
def echo(request: HttpRequest, payload: PersonIn) -> list[str]:
    return sorted(sent_fields(payload))


def test_ninja_keeps_which_fields_were_sent() -> None:
    client = TestClient(api)
    only_name = client.put("/echo", json={"display_name": "Test Persoon B"})
    assert only_name.json() == ["display_name"]
    explicit = client.put(
        "/echo", json={"display_name": "X", "name_variants": [], "archived": False}
    )
    assert explicit.json() == ["archived", "display_name", "name_variants"]


def test_defaults_are_unchanged_for_create() -> None:
    payload = PersonIn.model_validate({"display_name": "X"})
    assert (payload.archived, payload.name_variants) == (False, [])
