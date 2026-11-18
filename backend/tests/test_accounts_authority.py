"""Who may act on somebody else's account (D 6.3): the rule, and its refusals over HTTP."""

import uuid
from collections.abc import Callable

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from pytest_django import Settings

from apps.accounts import authority
from apps.accounts.authority import account_authority
from apps.accounts.models import AccountNotice, PasswordReset, User
from apps.audit.models import ServerAuditEvent
from apps.common.errors import ApiError
from apps.workspaces.models import Membership, Workspace
from tests.factories import TEST_PASSWORD, UserFactory
from tests.helpers import post_json

NOT_FOUND = {"code": "user_not_found", "message": "We kunnen dit account niet vinden."}
SERVER_ADMIN_ONLY = {
    "code": "server_admin_only",
    "message": "Dit kan alleen de serverbeheerder, omdat dit account ook buiten deze werkruimte "
    "bestaat.",
}


@pytest.fixture(autouse=True)
def _fast_hashing(settings: Settings) -> None:
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def admin_of(ws: Workspace) -> User:
    return Membership.objects.get(workspace=ws, role=Membership.Role.ADMIN).user


def join(ws: Workspace, role: str = Membership.Role.MEMBER, user: User | None = None) -> User:
    user = user or UserFactory.create()
    Membership.objects.create(user=user, workspace=ws, role=role)
    return user


def signed_in(user: User) -> Client:
    client = Client()
    r = post_json(client, "/auth/login", {"email": user.email, "password": TEST_PASSWORD})
    assert r.status_code == 200
    return client


def make_link(client: Client, target_id: uuid.UUID) -> "tuple[int, dict[str, str]]":
    r = post_json(client, f"/users/{target_id}/password-reset")
    return r.status_code, r.json()


# (actor, target) builders over two workspaces; each returns the pair.
Case = Callable[[Workspace, Workspace], tuple[User, User]]


def _server_admin_to_member_of_two(a: Workspace, b: Workspace) -> tuple[User, User]:
    actor = UserFactory.create(is_server_admin=True)
    target = join(b, user=join(a))
    return actor, target


def _server_admin_to_user_without_workspace(a: Workspace, b: Workspace) -> tuple[User, User]:
    return UserFactory.create(is_server_admin=True), UserFactory.create()


def _server_admin_to_other_server_admin(a: Workspace, b: Workspace) -> tuple[User, User]:
    return UserFactory.create(is_server_admin=True), join(
        a, user=UserFactory.create(is_server_admin=True)
    )


def _ws_admin_to_single_member(a: Workspace, b: Workspace) -> tuple[User, User]:
    return admin_of(a), join(a)


def _ws_admin_to_single_viewer(a: Workspace, b: Workspace) -> tuple[User, User]:
    return admin_of(a), join(a, Membership.Role.VIEWER)


def _ws_admin_to_co_admin(a: Workspace, b: Workspace) -> tuple[User, User]:
    return admin_of(a), join(a, Membership.Role.ADMIN)


def _ws_admin_to_member_of_two(a: Workspace, b: Workspace) -> tuple[User, User]:
    return admin_of(a), join(b, user=join(a))


def _admin_of_both_to_member_of_both(a: Workspace, b: Workspace) -> tuple[User, User]:
    actor = join(b, Membership.Role.ADMIN, user=admin_of(a))
    return actor, join(b, user=join(a))


def _ws_admin_to_server_admin_member(a: Workspace, b: Workspace) -> tuple[User, User]:
    return admin_of(a), join(a, user=UserFactory.create(is_server_admin=True))


def _member_to_member(a: Workspace, b: Workspace) -> tuple[User, User]:
    return join(a), join(a)


def _viewer_to_member(a: Workspace, b: Workspace) -> tuple[User, User]:
    return join(a, Membership.Role.VIEWER), join(a)


def _member_to_admin(a: Workspace, b: Workspace) -> tuple[User, User]:
    return join(a), admin_of(a)


def _ws_admin_to_member_of_another_workspace(a: Workspace, b: Workspace) -> tuple[User, User]:
    return admin_of(b), join(a)


def _ws_admin_to_user_without_workspace(a: Workspace, b: Workspace) -> tuple[User, User]:
    return admin_of(a), UserFactory.create()


def _ws_admin_to_themselves(a: Workspace, b: Workspace) -> tuple[User, User]:
    return admin_of(a), admin_of(a)


def _server_admin_to_themselves(a: Workspace, b: Workspace) -> tuple[User, User]:
    actor = join(a, user=UserFactory.create(is_server_admin=True))
    return actor, actor


def _inactive_ws_admin_to_single_member(a: Workspace, b: Workspace) -> tuple[User, User]:
    actor = admin_of(a)
    User.objects.filter(pk=actor.pk).update(is_active=False)
    actor.refresh_from_db()
    return actor, join(a)


ALLOWED = "allowed"
# "workspace": allowed, and the action also belongs in workspace a's log.
IN_WORKSPACE = "workspace"

MATRIX: list[tuple[Case, str]] = [
    (_server_admin_to_member_of_two, ALLOWED),
    (_server_admin_to_user_without_workspace, ALLOWED),
    (_server_admin_to_other_server_admin, IN_WORKSPACE),
    (_ws_admin_to_single_member, IN_WORKSPACE),
    (_ws_admin_to_single_viewer, IN_WORKSPACE),
    (_ws_admin_to_co_admin, IN_WORKSPACE),
    (_ws_admin_to_member_of_two, "server_admin_only"),
    (_admin_of_both_to_member_of_both, "server_admin_only"),
    (_ws_admin_to_server_admin_member, "server_admin_only"),
    (_member_to_member, "user_not_found"),
    (_viewer_to_member, "user_not_found"),
    (_member_to_admin, "user_not_found"),
    (_ws_admin_to_member_of_another_workspace, "user_not_found"),
    (_ws_admin_to_user_without_workspace, "user_not_found"),
    (_inactive_ws_admin_to_single_member, "user_not_found"),
    (_ws_admin_to_themselves, "own_account"),
    (_server_admin_to_themselves, "own_account"),
]


@pytest.mark.parametrize(("case", "expected"), MATRIX, ids=[c.__name__[1:] for c, _ in MATRIX])
def test_authority_matrix(ws_a: Workspace, ws_b: Workspace, case: Case, expected: str) -> None:
    actor, target = case(ws_a, ws_b)

    if expected in (ALLOWED, IN_WORKSPACE):
        result = account_authority(actor, target.id)
        assert result.target == target
        assert result.workspace_id == (ws_a.id if expected == IN_WORKSPACE else None)
        return
    with pytest.raises(ApiError) as caught:
        account_authority(actor, target.id)
    assert caught.value.code == expected


def test_an_unknown_target_is_not_found(ws_a: Workspace) -> None:
    for actor in (admin_of(ws_a), UserFactory.create(is_server_admin=True)):
        with pytest.raises(ApiError) as caught:
            account_authority(actor, uuid.uuid4())
        assert (caught.value.status, caught.value.code) == (404, "user_not_found")


def test_the_private_post_hook_reserves_the_action_for_the_server_admin(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    member = join(ws_a)
    assert authority.account_action_requires_server_admin(member) is False
    assert account_authority(admin_of(ws_a), member.id).workspace_id == ws_a.id

    monkeypatch.setattr(authority, "account_action_requires_server_admin", lambda target: True)

    with pytest.raises(ApiError) as caught:
        account_authority(admin_of(ws_a), member.id)
    assert (caught.value.status, caught.value.code) == (403, "server_admin_only")
    server_admin = UserFactory.create(is_server_admin=True)
    assert account_authority(server_admin, member.id).target == member


# --- The same refusals through POST /users/{id}/password-reset, as the app's role. ---


def nothing_happened(target: User) -> None:
    assert not PasswordReset.objects.filter(user=target).exists()
    assert not AccountNotice.objects.filter(user=target).exists()
    assert not ServerAuditEvent.objects.filter(code="password_reset_created").exists()


def test_workspace_admin_cannot_reset_a_member_of_two_workspaces(
    ws_a: Workspace, ws_b: Workspace
) -> None:
    target = join(ws_b, user=join(ws_a))

    assert make_link(signed_in(admin_of(ws_a)), target.id) == (403, SERVER_ADMIN_ONLY)
    nothing_happened(target)


def test_workspace_admin_cannot_reset_a_server_admin(ws_a: Workspace) -> None:
    target = join(ws_a, user=UserFactory.create(is_server_admin=True))

    assert make_link(signed_in(admin_of(ws_a)), target.id) == (403, SERVER_ADMIN_ONLY)
    nothing_happened(target)


def test_workspace_admin_cannot_reset_themselves_through_this_path(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)

    status, body = make_link(signed_in(admin), admin.id)

    assert (status, body["code"]) == (403, "own_account")
    nothing_happened(admin)


@pytest.mark.parametrize("role", [Membership.Role.MEMBER, Membership.Role.VIEWER])
def test_a_member_or_viewer_cannot_make_a_link_at_all(ws_a: Workspace, role: str) -> None:
    actor = join(ws_a, role)
    for target in (join(ws_a), admin_of(ws_a)):
        assert make_link(signed_in(actor), target.id) == (404, NOT_FOUND)
        nothing_happened(target)


def test_admin_of_another_workspace_gets_404(ws_a: Workspace, ws_b: Workspace) -> None:
    target = join(ws_a)

    assert make_link(signed_in(admin_of(ws_b)), target.id) == (404, NOT_FOUND)
    nothing_happened(target)


def test_an_unknown_user_answers_like_a_stranger(ws_a: Workspace, ws_b: Workspace) -> None:
    client = signed_in(admin_of(ws_b))
    stranger = join(ws_a)

    with CaptureQueriesContext(connection) as known:
        known_answer = make_link(client, stranger.id)
    with CaptureQueriesContext(connection) as unknown:
        unknown_answer = make_link(client, uuid.uuid4())

    assert known_answer == unknown_answer == (404, NOT_FOUND)
    # The same work for both, so the time taken reveals nothing either.
    assert len(known.captured_queries) == len(unknown.captured_queries)


def test_making_a_link_needs_a_signed_in_caller(ws_a: Workspace) -> None:
    target = join(ws_a)

    assert make_link(Client(), target.id)[0] == 401
    nothing_happened(target)
