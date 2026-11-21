"""Duplicate pairs over the API (plan Task 5): listDuplicates, getDuplicate, mergeDuplicate,
dismissDuplicate, the counts and the summary field. A pair the caller cannot see in full
leaves no trace: no row, no count, no chip, and the same 404 as a random id."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.test import Client

from apps.documents import duplicates
from apps.documents.models import Document, DuplicateCandidate, Extraction
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import admin_of, bearer_for, make_document, pair
from tests.helpers import post_json
from tests.members_support import as_user, join
from tests.visibility_support import office

pytestmark = pytest.mark.usefixtures("without_preset_rules")

VISIBILITY_DIFFERS = (
    "Deze twee brieven zijn niet voor dezelfde mensen zichtbaar. Kies 'Het zijn twee "
    "brieven', of laat ze eerst voor dezelfde mensen zichtbaar zijn."
)


def base(ws: object) -> str:
    return f"/workspaces/{ws}/duplicates"


def url(ws: object, candidate_id: object = None) -> str:
    path = base(ws) if candidate_id is None else f"{base(ws)}/{candidate_id}"
    return f"/api/v1{path}"


def merge_path(ws: object, candidate_id: object) -> str:
    return f"{base(ws)}/{candidate_id}/merge"


def dismiss_path(ws: object, candidate_id: object) -> str:
    return f"{base(ws)}/{candidate_id}/dismiss"


def fresh(document: Document) -> Document:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        return Document.objects.get(id=document.id)


def task(document: Document, *, status: str = Task.Status.OPEN) -> Task:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        return Task.objects.create(
            workspace_id=document.workspace_id,
            document=document,
            type=Task.Type.PAY,
            description="Betalen",
            status=status,
        )


def extract(document: Document, field: str, value: str, confidence: str) -> None:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        Extraction.objects.create(
            workspace_id=document.workspace_id,
            document=document,
            field=field,
            value=value,
            confidence=Decimal(confidence),
        )


@pytest.fixture
def client_a(client: Client, ws_a: Workspace) -> Client:
    client.force_login(admin_of(ws_a))
    return client


def two_letters(ws: Workspace) -> tuple[Document, Document, DuplicateCandidate]:
    first, second = make_document(ws, "done"), make_document(ws, "needs_review")
    return first, second, pair(first, second)


def summary_of(client: Client, ws: Workspace, document: Document) -> dict[str, object]:
    r = client.get(f"/api/v1/workspaces/{ws.id}/documents")
    assert r.status_code == 200
    [item] = [d for d in r.json() if d["id"] == str(document.id)]
    return dict(item)


def test_list_shows_pair_to_who_sees_both(client_a: Client, ws_a: Workspace) -> None:
    first, second, candidate = two_letters(ws_a)

    r = client_a.get(url(ws_a.id))

    assert r.status_code == 200, r.content
    [item] = r.json()
    assert item["id"] == str(candidate.id)
    assert item["state"] == "open"
    low, high = sorted((first.id, second.id))
    assert (item["first"]["id"], item["second"]["id"]) == (str(low), str(high))
    assert item["text_similarity"] == 95
    assert item["can_merge"] is True
    assert item["blocked_reason"] == ""
    # The `done` letter is further in review than the `needs_review` one.
    assert item["suggested_keep_id"] == str(first.id)


def test_unseen_pair_leaves_no_trace() -> None:
    o = office()
    private, own = o.letters["private_femke"], o.letters["own_piet"]
    candidate = pair(private, own)
    plain = as_user(o.plain)
    ws = o.workspace.id

    assert plain.get(url(ws)).json() == []
    assert plain.get(f"/api/v1/workspaces/{ws}/inbox/counts").json()["duplicate"] == 0
    assert summary_of(plain, o.workspace, own)["open_duplicate_count"] == 0
    detail = plain.get(f"/api/v1/workspaces/{ws}/documents/{own.id}")
    assert detail.json()["open_duplicate_count"] == 0

    random_id = uuid.uuid4()
    unseen_get, random_get = plain.get(url(ws, candidate.id)), plain.get(url(ws, random_id))
    assert unseen_get.status_code == random_get.status_code == 404
    assert unseen_get.json() == random_get.json()
    assert unseen_get.json()["code"] == "duplicate_not_found"
    body = {"keep_document_id": str(own.id)}
    for path in (merge_path, dismiss_path):
        unseen = post_json(plain, path(ws, candidate.id), body)
        missing = post_json(plain, path(ws, random_id), body)
        assert unseen.status_code == missing.status_code == 404
        assert unseen.json() == missing.json()
    with workspace_context(ws, actor=SYSTEM):
        assert DuplicateCandidate.objects.get(id=candidate.id).state == "open"


def test_seen_pair_counts(client_a: Client, ws_a: Workspace) -> None:
    first, second, _ = two_letters(ws_a)
    make_document(ws_a, "done")

    counts = client_a.get(f"/api/v1/workspaces/{ws_a.id}/inbox/counts").json()

    assert counts["duplicate"] == 1
    for document in (first, second):
        assert summary_of(client_a, ws_a, document)["open_duplicate_count"] == 1
        detail = client_a.get(f"/api/v1/workspaces/{ws_a.id}/documents/{document.id}")
        assert detail.json()["open_duplicate_count"] == 1


def test_viewer_sees_nothing_and_cannot_decide(ws_a: Workspace) -> None:
    viewer = as_user(join(ws_a, Membership.Role.VIEWER))
    first, _, candidate = two_letters(ws_a)

    assert viewer.get(url(ws_a.id)).json() == []
    assert viewer.get(f"/api/v1/workspaces/{ws_a.id}/inbox/counts").json()["duplicate"] == 0
    assert viewer.get(url(ws_a.id, candidate.id)).status_code == 404
    for path in (merge_path, dismiss_path):
        r = post_json(viewer, path(ws_a.id, candidate.id), {"keep_document_id": str(first.id)})
        assert r.status_code == 403
        assert r.json()["code"] == "forbidden"


def test_merge_visibility_differs_409() -> None:
    o = office()
    private, finance = o.letters["private_femke"], o.letters["finance_letter"]
    candidate = pair(private, finance)
    femke = as_user(o.finance)

    out = femke.get(url(o.workspace.id, candidate.id)).json()
    r = post_json(
        femke, merge_path(o.workspace.id, candidate.id), {"keep_document_id": str(finance.id)}
    )

    assert (out["can_merge"], out["blocked_reason"]) == (False, "visibility_differs")
    assert r.status_code == 409
    assert r.json() == {"code": "duplicate_visibility_differs", "message": VISIBILITY_DIFFERS}


def test_merge_returns_kept_letter(client_a: Client, ws_a: Workspace) -> None:
    first, second, candidate = two_letters(ws_a)

    r = post_json(client_a, merge_path(ws_a.id, candidate.id), {"keep_document_id": str(first.id)})

    assert r.status_code == 200, r.content
    assert r.json()["id"] == str(first.id)
    trash = client_a.get(f"/api/v1/workspaces/{ws_a.id}/trash").json()
    assert [item["id"] for item in trash["items"]] == [str(second.id)]
    assert client_a.get(url(ws_a.id)).json() == []


def test_merge_with_device_token(client: Client, ws_a: Workspace) -> None:
    member = join(ws_a)
    first, _, candidate = two_letters(ws_a)

    r = post_json(
        client,
        merge_path(ws_a.id, candidate.id),
        {"keep_document_id": str(first.id)},
        headers=bearer_for(member),
    )

    assert r.status_code == 200, r.content


def test_merge_body_defaults_complete_twins(client_a: Client, ws_a: Workspace) -> None:
    keep, other, candidate = two_letters(ws_a)
    twin = task(keep)
    task(other, status=Task.Status.DONE)

    r = post_json(client_a, merge_path(ws_a.id, candidate.id), {"keep_document_id": str(keep.id)})

    assert r.status_code == 200, r.content
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert Task.objects.get(id=twin.id).status == Task.Status.DONE


def test_task_twins_listed(client_a: Client, ws_a: Workspace) -> None:
    keep, other, candidate = two_letters(ws_a)
    task(keep)
    task(other, status=Task.Status.DONE)

    out = client_a.get(url(ws_a.id, candidate.id)).json()

    assert out["task_twins"] == [
        {
            "done_document_id": str(other.id),
            "open_document_id": str(keep.id),
            "type": "pay",
            "description": "Betalen",
        }
    ]


def test_dismiss_204_and_gone(client_a: Client, ws_a: Workspace) -> None:
    _, _, candidate = two_letters(ws_a)

    r = post_json(client_a, dismiss_path(ws_a.id, candidate.id))
    again = post_json(client_a, dismiss_path(ws_a.id, candidate.id))

    assert r.status_code == 204, r.content
    assert client_a.get(url(ws_a.id)).json() == []
    assert again.status_code == 409
    assert again.json()["code"] == "duplicate_not_open"


def test_differences_and_same(client_a: Client, ws_a: Workspace) -> None:
    first, second, candidate = two_letters(ws_a)
    for document in (first, second):
        extract(document, Extraction.Field.LETTER_DATE, "2026-03-01", "0.950")
    extract(first, Extraction.Field.INVOICE_AMOUNT_INCL, "100.00", "0.950")
    extract(second, Extraction.Field.INVOICE_AMOUNT_INCL, "180.00", "0.500")

    out = client_a.get(url(ws_a.id, candidate.id)).json()

    assert "letter_date" in out["same"]
    low = first if first.id < second.id else second
    first_amount, second_amount = ("100.00", "180.00") if low == first else ("180.00", "100.00")
    assert out["differences"][0] == {
        "field": "amount",
        "first": first_amount,
        "second": second_amount,
        "uncertain": True,
    }


def test_list_capped_and_newest_first(
    client_a: Client, ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    pairs = [two_letters(ws_a)[2] for _ in range(3)]
    monkeypatch.setattr(duplicates, "LIST_LIMIT", 2)

    out = client_a.get(url(ws_a.id)).json()

    assert [item["id"] for item in out] == [str(pairs[2].id), str(pairs[1].id)]


def test_event_out_has_related_document(client_a: Client, ws_a: Workspace) -> None:
    keep, other, candidate = two_letters(ws_a)
    post_json(client_a, merge_path(ws_a.id, candidate.id), {"keep_document_id": str(keep.id)})

    events = client_a.get(f"/api/v1/workspaces/{ws_a.id}/documents/{keep.id}/events").json()

    [merged] = [e for e in events if e["kind"] == "merged"]
    assert merged["related_document_id"] == str(other.id)


def test_merge_keep_outside_pair_422(client_a: Client, ws_a: Workspace) -> None:
    _, _, candidate = two_letters(ws_a)
    third = make_document(ws_a, "done")

    r = post_json(client_a, merge_path(ws_a.id, candidate.id), {"keep_document_id": str(third.id)})

    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_suggested_keep_is_the_confirmed_one(client_a: Client, ws_a: Workspace) -> None:
    first, second, candidate = two_letters(ws_a)
    with workspace_context(ws_a.id, actor=SYSTEM):
        Document.objects.filter(id=second.id).update(
            confirmed_at=fresh(first).created_at, letter_date=date(2026, 3, 1)
        )

    out = client_a.get(url(ws_a.id, candidate.id)).json()

    assert out["suggested_keep_id"] == str(second.id)


def test_search_hit_carries_the_count(client_a: Client, ws_a: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        found = Document.objects.create(
            workspace=ws_a,
            client_id=uuid.uuid4(),
            page_count=1,
            status="done",
            ocr_text="Aanslag waterschapsbelasting",
        )
    pair(found, make_document(ws_a, "done"))

    r = post_json(client_a, f"/workspaces/{ws_a.id}/documents/search", {"q": "waterschap"})

    assert r.status_code == 200, r.content
    [item] = r.json()["items"]
    assert item["open_duplicate_count"] == 1
