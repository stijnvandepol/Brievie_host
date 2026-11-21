"""Changing who sees what over the API: preview, confirmation, audit, notices (plan
Task 37, S 4.4, 4.13)."""

import uuid
from typing import TYPE_CHECKING

from django.test import Client

from apps.accounts.models import AccountNotice, User
from apps.accounts.notices import notice_message
from apps.audit.models import WorkspaceAuditEvent
from apps.documents.models import Document
from apps.labels.models import Label
from apps.rules.models import Rule
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership, Workspace
from apps.workspaces.schemas import VisibilityIn
from apps.workspaces.visibility_settings import fingerprint, snapshot
from tests.visibility_support import Office, as_actor, office, private_letter

if TYPE_CHECKING:
    from tests.helpers import Response


def _url(o: Office, rest: str = "") -> str:
    return f"/api/v1/workspaces/{o.workspace.id}/visibility{rest}"


def _as(client: Client, user: User) -> Client:
    client.force_login(user)
    return client


def _preview(client: Client, o: Office, body: dict[str, object]) -> "Response":
    return client.post(_url(o, "/preview"), body, content_type="application/json")


def _update(client: Client, o: Office, body: dict[str, object]) -> "Response":
    return client.put(_url(o), body, content_type="application/json")


def _switches(o: Office) -> tuple[bool, bool, bool]:
    w = Workspace.objects.get(id=o.workspace.id)
    return (w.everyone_sees_all, w.departments_enabled, w.admins_read_all)


def _notices(o: Office) -> dict[str, list[tuple[str, dict[str, str]]]]:
    found: dict[str, list[tuple[str, dict[str, str]]]] = {}
    for notice in AccountNotice.objects.filter(user__in=o.users.values()).order_by("created_at"):
        found.setdefault(notice.user.name, []).append((notice.code, notice.values))
    return found


def _audit(o: Office, code: str) -> list[dict[str, object]]:
    with as_actor(o.workspace, SYSTEM):
        return [
            dict(e.values)
            for e in WorkspaceAuditEvent.objects.filter(
                workspace_id=o.workspace.id, code=code
            ).order_by("created_at")
        ]


def test_preview_lists_who_gains_and_loses_and_counts_letters(client: Client) -> None:
    o = office()
    o.state(e=False, d=True, a=True)  # Anne reads all post that is not private
    r = _preview(_as(client, o.admin), o, {"everyone_sees_all": True})
    assert r.status_code == 200, r.json()
    body = r.json()
    assert [c["name"] for c in body["changes"]] == ["Femke", "Hans", "Piet", "Sandra", "Vera"]
    assert all(c["gains"] == ["alle post die niet privé is"] for c in body["changes"])
    assert all(c["losses"] == [] for c in body["changes"])
    # Every letter that is not private changes viewers; the private one keeps its readers.
    assert body["letter_count"] == len(o.letters) - 1
    assert body["needs_confirmation"] is True
    assert body["sentences"][0] == "Iedereen in Brievie ziet alle post, behalve post die privé is."
    assert len(body["preview_token"]) == 64


def test_preview_changes_nothing(client: Client) -> None:
    o = office()
    before = _switches(o)
    r = _preview(_as(client, o.admin), o, {"everyone_sees_all": True, "apply_preset": True})
    assert r.status_code == 200
    assert _switches(o) == before
    assert _audit(o, "workspace_setting_changed") == []
    assert _notices(o) == {}
    assert o.visible(o.plain.id) == {"own_piet", "scanned_in_review"}


def test_widening_without_confirmation_is_409(client: Client) -> None:
    o = office()
    r = _update(_as(client, o.admin), o, {"everyone_sees_all": True})
    assert r.status_code == 409
    assert r.json()["code"] == "confirmation_required"
    assert _switches(o) == (False, True, False)
    assert _audit(o, "workspace_setting_changed") == []

    token = _preview(client, o, {"everyone_sees_all": True}).json()["preview_token"]
    r = _update(client, o, {"everyone_sees_all": True, "confirmed": True, "preview_token": token})
    assert r.status_code == 200, r.json()
    assert r.json()["everyone_sees_all"] is True
    assert _switches(o) == (True, True, False)
    assert "hr_letter" in o.visible(o.plain.id)


def test_stale_preview_token_is_409_and_changes_nothing(client: Client) -> None:
    o = office()
    token = _preview(_as(client, o.admin), o, {"everyone_sees_all": True}).json()["preview_token"]
    # Another admin flags a sorter in between.
    Membership.objects.filter(workspace=o.workspace, user=o.plain).update(sorts_post=True)
    r = _update(client, o, {"everyone_sees_all": True, "confirmed": True, "preview_token": token})
    assert r.status_code == 409
    assert r.json()["code"] == "preview_outdated"
    assert _switches(o) == (False, True, False)
    assert _notices(o) == {}


def test_narrowing_needs_no_confirmation(client: Client) -> None:
    o = office()
    o.state(e=True, d=True, a=True)
    r = _update(_as(client, o.admin), o, {"everyone_sees_all": False})
    assert r.status_code == 200, r.json()
    assert _switches(o) == (False, True, True)
    assert o.visible(o.plain.id) == {"own_piet", "scanned_in_review"}


def test_audit_row_per_changed_setting(client: Client) -> None:
    o = office()
    o.state(e=True, d=False, a=True)
    r = _update(
        _as(client, o.admin),
        o,
        {"everyone_sees_all": False, "departments_enabled": False, "allow_member_export": True},
    )
    assert r.status_code == 200, r.json()
    # D was off already: no row; E and the export switch changed.
    assert _audit(o, "workspace_setting_changed") == [
        {"setting": "everyone_sees_all", "value": False},
        {"setting": "allow_member_export", "value": True},
    ]


def test_preset_switch_writes_preset_row_and_never_removes_labels_or_rules(client: Client) -> None:
    o = office()
    with as_actor(o.workspace, SYSTEM):
        labels = set(Label.objects.values_list("id", flat=True))
        rules = set(Rule.objects.values_list("id", flat=True))
    body: dict[str, object] = {"kind": "home", "apply_preset": True, "add_start_set": True}
    token = _preview(_as(client, o.admin), o, body).json()["preview_token"]
    r = _update(client, o, {**body, "confirmed": True, "preview_token": token})
    assert r.status_code == 200, r.json()
    workspace = Workspace.objects.get(id=o.workspace.id)
    assert (workspace.kind, workspace.care_is_private, workspace.allow_member_export) == (
        "home",
        True,
        True,
    )
    assert _switches(o) == (True, False, True)
    # Emergency access is the server admin's: the preset leaves it.
    assert workspace.emergency_access is True
    assert _audit(o, "workspace_preset_applied") == [{"preset": "home"}]
    settings = {row["setting"] for row in _audit(o, "workspace_setting_changed")}
    assert settings == {
        "kind",
        "everyone_sees_all",
        "departments_enabled",
        "admins_read_all",
        "care_is_private",
        "allow_member_export",
    }
    with as_actor(o.workspace, SYSTEM):
        assert labels <= set(Label.objects.values_list("id", flat=True))
        assert rules <= set(Rule.objects.values_list("id", flat=True))


def test_admins_read_all_on_notifies_every_member(client: Client) -> None:
    o = office()
    token = _preview(_as(client, o.admin), o, {"admins_read_all": True}).json()["preview_token"]
    r = _update(client, o, {"admins_read_all": True, "confirmed": True, "preview_token": token})
    assert r.status_code == 200, r.json()
    notices = _notices(o)
    for name in ("Anne", "Sandra", "Femke", "Hans", "Piet", "Vera"):
        assert ("admins_read_all_on", {}) in notices[name], name
    assert ("sight_widened", {"category": "all_as_admin"}) in notices["Anne"]


def test_notices_go_to_exactly_the_changed_members(client: Client) -> None:
    o = office()
    body: dict[str, object] = {"departments_enabled": False}
    token = _preview(_as(client, o.admin), o, body).json()["preview_token"]
    r = _update(client, o, {**body, "confirmed": True, "preview_token": token})
    assert r.status_code == 200, r.json()
    notices = _notices(o)
    sandra = notices.pop("Sandra")
    assert notices == {
        "Femke": [("sight_narrowed", {"category": "department", "department": "Finance"})],
        "Hans": [("sight_narrowed", {"category": "department", "department": "HR"})],
    }
    # Sandra sorts post: both departments' post is now hers; one notice names one of them.
    [(code, values)] = sandra
    assert code == "sight_widened"
    assert values["category"] == "department"
    assert values["department"] in {"Finance", "HR"}
    notice = AccountNotice.objects.get(user=o.finance)
    assert notice_message(notice) == "Je ziet de post van Finance niet meer."


def test_departments_switch_while_everyone_sees_all_only_audits(client: Client) -> None:
    o = office()
    o.state(e=True, d=True, a=False)
    r = _update(_as(client, o.admin), o, {"departments_enabled": False})
    assert r.status_code == 200, r.json()
    assert _audit(o, "workspace_setting_changed") == [
        {"setting": "departments_enabled", "value": False}
    ]
    assert _notices(o) == {}


def test_member_gets_sentences_from_get_visibility(client: Client) -> None:
    o = office()
    r = _as(client, o.plain).get(_url(o))
    assert r.status_code == 200
    body = r.json()
    assert (body["kind"], body["everyone_sees_all"], body["departments_enabled"]) == (
        "business",
        False,
        True,
    )
    assert body["admins_read_all"] is False
    assert body["sorter_user_ids"] == [str(o.sorter.id)]
    assert body["sentences"][0] == (
        "Je post zien alleen jij en, als hij op naam van een afdeling staat, de mensen van "
        "die afdeling."
    )
    assert "Post zonder naam zien: Sandra (verdeelt post)." in body["sentences"]
    assert body["summary"] == []
    summary = _as(client, o.admin).get(_url(o)).json()["summary"]
    assert summary[0] == "Ieder ziet zijn eigen post en die van zijn afdelingen."


def test_viewer_cannot_change_visibility(client: Client) -> None:
    o = office()
    for user in (o.viewer, o.plain, o.sorter):
        r = _update(_as(client, user), o, {"everyone_sees_all": False})
        assert r.status_code == 403
        assert r.json()["code"] == "forbidden"
        assert _preview(client, o, {"everyone_sees_all": True}).status_code == 403
    assert _switches(o) == (False, True, False)


def test_unknown_kind_or_member_change_is_422(client: Client) -> None:
    o = office()
    _as(client, o.admin)
    assert _update(client, o, {"kind": "school"}).status_code == 422
    assert _preview(client, o, {"member_change": "promote"}).status_code == 422


def test_emergency_access_is_not_in_visibility_in() -> None:
    assert "emergency_access" not in VisibilityIn.model_fields


def test_workspace_settings_answer_the_switches(client: Client) -> None:
    o = office()
    body = _as(client, o.plain).get(f"/api/v1/workspaces/{o.workspace.id}/settings").json()
    assert (body["everyone_sees_all"], body["departments_enabled"], body["admins_read_all"]) == (
        False,
        True,
        False,
    )


def test_switching_off_ends_assignments_on_letters_no_longer_seen(client: Client) -> None:
    o = office()
    o.state(e=True, d=True, a=False)
    with as_actor(o.workspace, SYSTEM):
        task = Task.objects.get(document=o.letters["hr_letter"])
        Task.objects.filter(id=task.id).update(assigned_to=o.plain, assigned_by=o.admin)
    r = _update(_as(client, o.admin), o, {"everyone_sees_all": False})
    assert r.status_code == 200, r.json()
    with as_actor(o.workspace, SYSTEM):
        assert Task.objects.get(id=task.id).assigned_to_id is None


def test_a_hidden_letter_answers_like_an_unknown_one_right_after_the_switch(
    client: Client,
) -> None:
    o = office()
    o.state(e=True, d=True, a=False)
    letter = o.letters["hr_letter"]
    _as(client, o.plain)
    url = f"/api/v1/workspaces/{o.workspace.id}/documents/{letter.id}"
    assert client.get(url).status_code == 200
    _as(client, o.admin)
    assert _update(client, o, {"everyone_sees_all": False}).status_code == 200
    _as(client, o.plain)
    r = client.get(url)
    assert r.status_code == 404
    assert r.json()["code"] == "document_not_found"
    unknown = client.get(f"/api/v1/workspaces/{o.workspace.id}/documents/{uuid.uuid4()}")
    assert r.json() == unknown.json()
    with as_actor(o.workspace, SYSTEM):
        assert Document.objects.filter(id=letter.id).exists()


def test_a_token_confirms_only_its_own_change(client: Client) -> None:
    o = office()
    _as(client, o.admin)
    narrowing = _preview(client, o, {"admins_read_all": False}).json()["preview_token"]
    r = _update(
        client, o, {"everyone_sees_all": True, "confirmed": True, "preview_token": narrowing}
    )
    assert r.status_code == 409
    assert r.json()["code"] == "preview_outdated"
    # Nor can a token be computed from what members may read: the state's fingerprint.
    forged = fingerprint(snapshot(o.workspace))
    r = _update(client, o, {"everyone_sees_all": True, "confirmed": True, "preview_token": forged})
    assert r.status_code == 409
    assert _switches(o) == (False, True, False)


def test_a_token_belongs_to_the_admin_who_previewed(client: Client) -> None:
    o = office()
    Membership.objects.filter(workspace=o.workspace, user=o.hr).update(role="admin")
    hans = _preview(_as(client, o.hr), o, {"everyone_sees_all": True}).json()["preview_token"]
    r = _update(
        _as(client, o.admin),
        o,
        {"everyone_sees_all": True, "confirmed": True, "preview_token": hans},
    )
    assert r.status_code == 409
    assert r.json()["code"] == "preview_outdated"
    assert _switches(o) == (False, True, False)


def test_a_token_belongs_to_its_workspace(client: Client) -> None:
    o = office()
    other = office()
    Membership.objects.create(workspace=other.workspace, user=o.admin, role="admin")
    _as(client, o.admin)
    token = _preview(client, other, {"everyone_sees_all": True}).json()["preview_token"]
    r = _update(client, o, {"everyone_sees_all": True, "confirmed": True, "preview_token": token})
    assert r.status_code == 409
    assert r.json()["code"] == "preview_outdated"
    assert _switches(o) == (False, True, False)


def test_the_preview_counts_only_letters_the_admin_can_read(client: Client) -> None:
    """Finding 4: no exact count of a department's or person's post, private or not,
    for an admin who cannot read it."""
    o = office()
    private_letter(o.workspace, addressees=[o.persons["Finance"]])
    body: dict[str, object] = {
        "member_change": "department_add",
        "member_user_id": str(o.plain.id),
        "member_person_id": str(o.persons["Finance"].id),
    }
    _as(client, o.admin)
    # Admins read no post that is not for them: Finance's letters are not counted.
    assert _preview(client, o, body).json()["letter_count"] == 0
    # Admins read all post: Finance's open letter counts, its private one does not.
    o.state(e=False, d=True, a=True)
    assert _preview(client, o, body).json()["letter_count"] == 1
    # Femke reads both and would count both, were she an admin.
    Membership.objects.filter(workspace=o.workspace, user=o.finance).update(role="admin")
    assert _preview(_as(client, o.finance), o, body).json()["letter_count"] == 2
