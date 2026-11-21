"""Reading the audit logs through the HTTP endpoints, as real roles."""

import csv
import io
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.audit.codes import AuditCode, TargetKind
from apps.audit.models import ServerAuditEvent, WorkspaceAuditEvent
from apps.audit.services import AuditActor, AuditTarget, record
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.factories import UserFactory
from tests.members_support import admin_of, as_user, bearer, join, paired

if TYPE_CHECKING:
    from tests.helpers import Response

HEADER = ["Tijdstip", "Wie", "Gebeurtenis", "Code", "Om wie of wat", "Adres", "Details"]


def wpath(ws: Workspace, tail: str = "") -> str:
    return f"/api/v1/workspaces/{ws.id}/audit-events{tail}"


def log(ws: Workspace, code: AuditCode, *, actor: User | None = None, **kwargs: object) -> None:
    who = AuditActor.for_user(actor) if actor else AuditActor.system()
    record(code, actor=who, workspace_id=ws.id, **kwargs)  # type: ignore[arg-type]


def set_time(row_code: str, moment: datetime, *, ws: Workspace | None = None) -> None:
    """Move the newest row of a code to `moment` (the app role cannot update the log, the
    test's owner role can: created_at is the one thing a test must control)."""
    model = WorkspaceAuditEvent if ws else ServerAuditEvent
    if ws:
        with workspace_context(ws.id, actor=SYSTEM):
            model.objects.filter(code=row_code).update(created_at=moment)
    else:
        model.objects.filter(code=row_code).update(created_at=moment)


def server_admin() -> User:
    return UserFactory.create(is_server_admin=True)


def ids(r: "Response") -> list[str]:
    return [row["code"] for row in r.json()]


def test_only_admins_read_the_workspace_log(ws_a: Workspace) -> None:
    log(ws_a, AuditCode.RULE_CREATED)

    for role in (Membership.Role.MEMBER, Membership.Role.VIEWER):
        r = as_user(join(ws_a, role)).get(wpath(ws_a))
        assert r.status_code == 403
        assert r.json()["code"] == "forbidden"
        assert as_user(join(ws_a, role)).get(wpath(ws_a, "/csv")).status_code == 403

    r = as_user(admin_of(ws_a)).get(wpath(ws_a))
    assert r.status_code == 200
    assert ids(r) == ["rule_created"]
    row = r.json()[0]
    assert row["label"] == "Regel gemaakt"
    assert row["actor_kind"] == "system"
    assert row["values"] == {}


def test_a_device_token_gets_401_on_every_log_endpoint(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    admin.is_server_admin = True
    admin.save()
    _, token = paired(admin)
    client = Client()

    for path in (
        wpath(ws_a),
        wpath(ws_a, "/csv"),
        "/api/v1/server/audit-events",
        "/api/v1/server/audit-events/csv",
    ):
        assert client.get(path, headers=bearer(token)).status_code == 401, path
        assert client.get(path).status_code == 401, path


def test_a_workspace_log_never_shows_another_workspace(ws_a: Workspace, ws_b: Workspace) -> None:
    log(ws_a, AuditCode.RULE_CREATED)
    log(ws_b, AuditCode.LABEL_DELETED)

    admin_a, admin_b = as_user(admin_of(ws_a)), as_user(admin_of(ws_b))

    assert ids(admin_a.get(wpath(ws_a))) == ["rule_created"]
    assert ids(admin_b.get(wpath(ws_b))) == ["label_deleted"]
    # An admin of another workspace gets the project's not-found, for the log and its CSV.
    for tail in ("", "/csv"):
        r = admin_b.get(wpath(ws_a, tail))
        assert r.status_code == 404
        assert r.json()["code"] == "workspace_not_found"
    # A server admin who is no member of the workspace does not read it either.
    assert as_user(server_admin()).get(wpath(ws_a)).status_code == 404


def test_row_level_security_hides_another_workspaces_rows(ws_a: Workspace, ws_b: Workspace) -> None:
    log(ws_a, AuditCode.RULE_CREATED)

    with workspace_context(ws_b.id, actor=SYSTEM):
        assert WorkspaceAuditEvent.objects.filter(workspace_id=ws_a.id).count() == 0
        assert WorkspaceAuditEvent.objects.count() == 0
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert WorkspaceAuditEvent.objects.count() == 1


def test_filters_on_user_code_and_days(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    other = join(ws_a)
    third = join(ws_a)
    log(ws_a, AuditCode.RULE_CREATED, actor=admin)
    log(
        ws_a,
        AuditCode.MEMBER_ROLE_CHANGED,
        actor=admin,
        target=AuditTarget(TargetKind.USER, other.id, other.name),
        values={"from": "member", "to": "viewer"},
    )
    log(ws_a, AuditCode.RULE_DELETED, actor=third)
    set_time("rule_created", datetime(2026, 3, 10, 12, 0, tzinfo=UTC), ws=ws_a)
    set_time("member_role_changed", datetime(2026, 3, 11, 12, 0, tzinfo=UTC), ws=ws_a)
    set_time("rule_deleted", datetime(2026, 3, 12, 12, 0, tzinfo=UTC), ws=ws_a)
    client = as_user(admin)

    # The user acted or was acted on.
    assert set(ids(client.get(wpath(ws_a), {"user_id": str(other.id)}))) == {"member_role_changed"}
    assert set(ids(client.get(wpath(ws_a), {"user_id": str(admin.id)}))) == {
        "rule_created",
        "member_role_changed",
    }
    assert ids(client.get(wpath(ws_a), {"code": "rule_deleted"})) == ["rule_deleted"]
    # Whole days, both ends inclusive.
    both = client.get(wpath(ws_a), {"since": "2026-03-11", "until": "2026-03-12"})
    assert ids(both) == ["rule_deleted", "member_role_changed"]
    assert ids(client.get(wpath(ws_a), {"since": "2026-03-12"})) == ["rule_deleted"]
    assert ids(client.get(wpath(ws_a), {"until": "2026-03-10"})) == ["rule_created"]
    # Days are Amsterdam days: 23:30 UTC on 11 March is 00:30 on the 12th there (CET).
    set_time("member_role_changed", datetime(2026, 3, 11, 23, 30, tzinfo=UTC), ws=ws_a)
    on_12th = client.get(wpath(ws_a), {"since": "2026-03-12", "until": "2026-03-12"})
    assert set(ids(on_12th)) == {"rule_deleted", "member_role_changed"}


def test_filters_are_validated(ws_a: Workspace) -> None:
    client = as_user(admin_of(ws_a))
    bad: list[dict[str, str | int]] = [
        {"code": "not_a_code"},
        {"limit": 0},
        {"limit": 501},
        {"since": "2026-03-12", "until": "2026-03-11"},
    ]
    for query in bad:
        r = client.get(wpath(ws_a), query)
        assert r.status_code == 422, query
        assert r.json()["code"] == "validation_error", query
    assert client.get(wpath(ws_a), {"user_id": "nope"}).status_code == 422
    assert client.get(wpath(ws_a), {"before": "nope"}).status_code == 422
    assert client.get(wpath(ws_a), {"limit": 500}).status_code == 200
    # The CSV validates the same filters.
    assert client.get(wpath(ws_a, "/csv"), {"code": "not_a_code"}).status_code == 422
    inverted = {"since": "2026-03-12", "until": "2026-03-11"}
    assert client.get(wpath(ws_a, "/csv"), inverted).status_code == 422


def test_before_pages_backwards(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    for _ in range(5):
        log(ws_a, AuditCode.RULE_CREATED, actor=admin)
    base = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    with workspace_context(ws_a.id, actor=SYSTEM):
        for index, row in enumerate(WorkspaceAuditEvent.objects.order_by("id")):
            WorkspaceAuditEvent.objects.filter(id=row.id).update(
                created_at=base + timedelta(minutes=index)
            )
    client = as_user(admin)

    first = client.get(wpath(ws_a), {"limit": 2}).json()
    second = client.get(wpath(ws_a), {"limit": 2, "before": first[-1]["created_at"]}).json()
    third = client.get(wpath(ws_a), {"limit": 2, "before": second[-1]["created_at"]}).json()

    pages = [row["id"] for page in (first, second, third) for row in page]
    assert len(pages) == 5
    assert len(set(pages)) == 5
    times = [row["created_at"] for page in (first, second, third) for row in page]
    assert times == sorted(times, reverse=True)


def test_server_log_is_for_server_admins(ws_a: Workspace) -> None:
    record(AuditCode.SIGN_IN_FAILED, actor=AuditActor.system())
    log(ws_a, AuditCode.RULE_CREATED)

    for who in (admin_of(ws_a), join(ws_a)):
        for tail in ("", "/csv"):
            r = as_user(who).get(f"/api/v1/server/audit-events{tail}")
            assert r.status_code == 403
            assert r.json()["code"] == "forbidden"
    r = as_user(server_admin()).get("/api/v1/server/audit-events")
    assert r.status_code == 200
    # Only the server's rows, never a workspace's.
    assert ids(r) == ["sign_in_failed"]
    assert as_user(server_admin()).get("/api/v1/server/audit-events/csv").status_code == 200


def test_the_log_is_not_a_way_to_change_it(ws_a: Workspace) -> None:
    log(ws_a, AuditCode.RULE_CREATED)
    client = as_user(admin_of(ws_a))

    for method in (client.post, client.put, client.delete, client.patch):
        assert method(wpath(ws_a)).status_code >= 400
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert WorkspaceAuditEvent.objects.count() == 1


def parsed(r: "Response") -> list[list[str]]:
    return list(csv.reader(io.StringIO(r.content.decode())))


def test_csv_has_the_header_and_guards_formulas(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    log(
        ws_a,
        AuditCode.PERSON_DELETED,
        actor=admin,
        target=AuditTarget(TargetKind.PERSON, UUID(int=1), '=HYPERLINK("http://evil","klik")'),
    )
    log(ws_a, AuditCode.PERSON_LINKED, actor=UserFactory.create(name="+31 6 1234"))
    log(ws_a, AuditCode.PERSON_UNLINKED, actor=UserFactory.create(name="@SUM(1)"))
    log(ws_a, AuditCode.LABEL_DELETED, actor=UserFactory.create(name="-2+3"))
    log(ws_a, AuditCode.RULE_RUN, actor=UserFactory.create(name="\tTab"))
    log(ws_a, AuditCode.RULE_CHANGED, actor=UserFactory.create(name="\rCR"))
    log(ws_a, AuditCode.WORKSPACE_SETTING_CHANGED, actor=admin, values={"setting": "=1+1"})

    rows = parsed(as_user(admin).get(wpath(ws_a, "/csv")))

    assert rows[0] == HEADER
    cells = {cell for row in rows[1:] for cell in row}
    assert '\'=HYPERLINK("http://evil","klik")' in cells
    assert {"'+31 6 1234", "'@SUM(1)", "'-2+3", "'\tTab", "'\rCR"} <= cells
    for row in rows[1:]:
        for cell in row:
            assert not cell.startswith(("=", "+", "-", "@", "\t", "\r")), cell
        assert len(row) == len(HEADER)
    detail = [row[6] for row in rows[1:] if row[3] == "workspace_setting_changed"]
    # "setting=..." starts with a letter, so the formula is not at a cell start.
    assert detail == ["setting==1+1"]


def test_csv_is_an_attachment(ws_a: Workspace) -> None:
    log(ws_a, AuditCode.RULE_CREATED)

    r = as_user(admin_of(ws_a)).get(wpath(ws_a, "/csv"))

    assert r.status_code == 200
    assert r["Content-Type"].startswith("text/csv")
    disposition = r["Content-Disposition"]
    assert disposition.startswith('attachment; filename="brievie-logboek-')
    assert disposition.endswith('.csv"')
    assert "no-store" in r["Cache-Control"]
    assert "private" in r["Cache-Control"]


def test_server_csv_is_an_attachment_and_filtered(ws_a: Workspace) -> None:
    record(AuditCode.SIGN_IN_FAILED, actor=AuditActor.system())
    record(AuditCode.PASSWORD_CHANGED, actor=AuditActor.system(), values={"via": "self"})

    r = as_user(server_admin()).get("/api/v1/server/audit-events/csv", {"code": "password_changed"})

    rows = parsed(r)
    assert r["Content-Disposition"].startswith("attachment;")
    assert "no-store" in r["Cache-Control"]
    assert [row[3] for row in rows[1:]] == ["password_changed"]
    assert rows[1][6] == "via=self"


@pytest.mark.parametrize("path", ["/api/v1/server/audit-events", "/api/v1/server/audit-events/csv"])
def test_an_inactive_server_admin_reads_nothing(path: str) -> None:
    user = server_admin()
    client = as_user(user)
    user.is_active = False
    user.save()

    assert client.get(path).status_code in (401, 403)


def test_a_csv_over_the_cap_is_cut_and_says_so(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("apps.audit.services.CSV_MAX_ROWS", 3)
    for _ in range(5):
        log(ws_a, AuditCode.RULE_CREATED)
    admin = as_user(admin_of(ws_a))

    r = admin.get(wpath(ws_a, "/csv"))

    rows = parsed(r)
    assert r["X-Brievie-Truncated"] == "true"
    assert rows[0] == HEADER
    assert [row[3] for row in rows[1:4]] == ["rule_created"] * 3
    assert rows[4] == ["Let op: alleen de nieuwste 3 gebeurtenissen staan in dit bestand."]
    assert len(rows) == 5


def test_a_csv_at_or_under_the_cap_is_whole(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("apps.audit.services.CSV_MAX_ROWS", 3)
    admin = as_user(admin_of(ws_a))
    for _ in range(3):
        log(ws_a, AuditCode.RULE_CREATED)

    r = admin.get(wpath(ws_a, "/csv"))

    assert "X-Brievie-Truncated" not in r
    assert len(parsed(r)) == 4
    assert not any("Let op" in cell for row in parsed(r) for cell in row)


def test_the_server_csv_is_cut_the_same_way(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("apps.audit.services.CSV_MAX_ROWS", 2)
    for _ in range(3):
        record(AuditCode.SIGN_IN_FAILED, actor=AuditActor.system())

    r = as_user(server_admin()).get("/api/v1/server/audit-events/csv")

    assert r["X-Brievie-Truncated"] == "true"
    assert len(parsed(r)) == 4
