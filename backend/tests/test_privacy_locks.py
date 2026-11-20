"""One lock order (PB T6, plan Task 5): the caller's membership (`FOR SHARE`) before any
document, and documents and persons locked without their key (`FOR NO KEY UPDATE`)."""

import ast
import uuid
from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest
from django.db import connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext
from psycopg.conninfo import make_conninfo

from apps.common.errors import ApiError
from apps.documents import services
from apps.documents.models import Document, DocumentEvent
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import admin_of, documents_url, make_document
from tests.helpers import post_json

APPS = Path(__file__).resolve().parent.parent / "apps"
KEYLESS_MODELS = {"Document", "Person"}


def _model_of(call: ast.Call) -> str | None:
    """The model name at the root of `X.objects....select_for_update(...)`, if any."""
    node: ast.expr = call.func
    while True:
        if isinstance(node, ast.Attribute):
            if node.attr == "objects" and isinstance(node.value, ast.Name):
                return node.value.id
            node = node.value
        elif isinstance(node, ast.Call):
            node = node.func
        else:
            return None


def _keyed_locks() -> list[str]:
    found = []
    for path in sorted(APPS.rglob("*.py")):
        if "migrations" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "select_for_update"
            ):
                continue
            if _model_of(node) not in KEYLESS_MODELS:
                continue
            no_key = any(
                k.arg == "no_key" and isinstance(k.value, ast.Constant) and k.value.value is True
                for k in node.keywords
            )
            if not no_key:
                found.append(f"{path.relative_to(APPS.parent)}:{node.lineno}")
    return found


def test_document_and_person_row_locks_are_no_key() -> None:
    assert _keyed_locks() == []


def test_the_scan_sees_a_document_lock_through_a_chain() -> None:
    call = ast.parse("Document.objects.filter(x=1).exclude(y=2).select_for_update()").body[0]
    assert isinstance(call, ast.Expr)
    assert isinstance(call.value, ast.Call)
    assert _model_of(call.value) == "Document"


def _lock_positions(queries: list[str]) -> tuple[int, int]:
    membership = next(
        i for i, sql in enumerate(queries) if "workspaces_membership" in sql and "FOR SHARE" in sql
    )
    document = next(
        i
        for i, sql in enumerate(queries)
        if 'FROM "documents_document"' in sql and "FOR NO KEY UPDATE" in sql
    )
    return membership, document


def _put(client: Client, path: str, body: dict[str, object]) -> int:
    response = client.put(path, body, content_type="application/json")
    return int(response.status_code)


@pytest.mark.parametrize(
    "operation", ["labels", "addressees", "confirm", "retry", "reopen", "dismiss"]
)
def test_request_paths_take_the_membership_before_the_document(
    client: Client, ws_a: Workspace, operation: str
) -> None:
    client.force_login(admin_of(ws_a))
    status = {"retry": "failed", "confirm": "needs_review"}.get(operation, "done")
    document = make_document(ws_a, status=status)
    if operation == "reopen":
        with workspace_context(ws_a.id, actor=SYSTEM):
            Document.objects.filter(id=document.id).update(confirmed_by_ai=True)
    base = f"{documents_url(ws_a.id)}/{document.id}"
    calls: dict[str, Callable[[], object]] = {
        "labels": lambda: _put(client, f"{base}/labels", {"label_ids": []}),
        "addressees": lambda: _put(client, f"{base}/addressees", {"person_ids": []}),
        "confirm": lambda: post_json(
            client,
            f"/workspaces/{ws_a.id}/documents/{document.id}/confirm",
            {
                "sender": "",
                "document_type": "",
                "summary": "",
                "summary_by_ai": False,
                "tasks": [],
            },
        ),
        "retry": lambda: post_json(client, f"/workspaces/{ws_a.id}/documents/{document.id}/retry"),
        "reopen": lambda: post_json(
            client, f"/workspaces/{ws_a.id}/documents/{document.id}/reopen"
        ),
        "dismiss": lambda: post_json(
            client,
            f"/workspaces/{ws_a.id}/documents/{document.id}/suggestions/{uuid.uuid4()}/dismiss",
        ),
    }

    with CaptureQueriesContext(connection) as queries:
        calls[operation]()

    membership, document_lock = _lock_positions([q["sql"] for q in queries])
    assert membership < document_lock


def test_a_removed_member_gets_404_from_an_organising_call(ws_a: Workspace) -> None:
    user = admin_of(ws_a)
    document = make_document(ws_a)
    with transaction.atomic(), workspace_context(ws_a.id, actor=user.id):
        # Gone between `workspace_member` and the lock.
        Membership.objects.filter(workspace=ws_a, user=user).delete()
        with pytest.raises(ApiError) as caught:
            services.set_document_labels(
                workspace=ws_a, user=user, document_id=document.id, label_ids=[]
            )
        transaction.set_rollback(True)
    assert (caught.value.status, caught.value.code) == (404, "workspace_not_found")


def _second_connection() -> psycopg.Connection[tuple[object, ...]]:
    settings = connection.settings_dict
    return psycopg.connect(
        make_conninfo(
            dbname=settings["NAME"],
            user=settings["USER"],
            password=settings["PASSWORD"],
            host=settings["HOST"],
            port=settings["PORT"],
        ),
        autocommit=False,
    )


@pytest.mark.django_db(transaction=True)
def test_a_foreign_key_check_does_not_wait_for_a_document_lock(ws_a: Workspace) -> None:
    document = make_document(ws_a)
    with transaction.atomic(), workspace_context(ws_a.id, actor=SYSTEM):
        Document.objects.select_for_update(no_key=True).get(id=document.id)
        with _second_connection() as other, other.cursor() as c:
            c.execute("SET lock_timeout = '1s'")
            # Django's foreign keys are deferred; check this one at the insert itself.
            c.execute("SET CONSTRAINTS ALL IMMEDIATE")
            c.execute("SELECT set_config('brievie.workspace_id', %s, true)", [str(ws_a.id)])
            c.execute("SELECT set_config('brievie.actor', 'system', true)")
            c.execute(
                "INSERT INTO documents_documentevent"
                " (id, created_at, updated_at, workspace_id, document_id, kind, origin,"
                " person_name, label_name, rule_name)"
                " VALUES (%s, now(), now(), %s, %s, 'confirmed', 'user', '', '', '')",
                [uuid.uuid4(), ws_a.id, document.id],
            )
            other.rollback()
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert not DocumentEvent.objects.exists()
