"""A password sign-in through the public route needs the second step (two-step plan Task 13,
external access B2).

Behind Caddy (`BRIEVIE_BEHIND_CADDY`), with the web on at the public address
(`BRIEVIE_PUBLIC_WEB`): every way in by password on the public route (signing in, setting up,
accepting an invitation, completing a reset, typing the password again) needs a confirmed
factor. Without one the answer never tells whether the password was right. Home and VPN are
unchanged; an unknown or missing route is public.
"""

import re

import pytest
from django.conf import settings as django_settings
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import Client, RequestFactory
from pytest_django import Settings

from apps.accounts.models import PasswordReset, SignInFailures, User, UserSession
from apps.accounts.signin import PENDING_KEY, begin_sign_in
from apps.accounts.signin_limits import account_key
from apps.audit.models import ServerAuditEvent
from apps.common.errors import ApiError
from apps.devices.models import Device
from apps.workspaces.models import Invitation, Membership, Workspace
from tests.documents_support import admin_of
from tests.factories import TEST_PASSWORD, UserFactory
from tests.helpers import post_json
from tests.members_support import bearer, paired
from tests.second_factor_support import Factor, give_factor

PUBLIC = {"X-Brievie-Route": "public"}
PRIVATE = {"X-Brievie-Route": "private"}
NEW_PASSWORD = TEST_PASSWORD + "-nieuw"
WRONG_PASSWORD = TEST_PASSWORD + "-fout"

HOME_SENTENCE = "Het e-mailadres of wachtwoord klopt niet."
OUTSIDE = {
    "code": "invalid_credentials",
    "message": (
        "Het e-mailadres of wachtwoord klopt niet, of tweestapsverificatie staat nog niet aan. "
        "Van buiten kun je alleen inloggen met tweestapsverificatie. Zet die eerst thuis aan."
    ),
}
NEEDED_OUTSIDE = {
    "code": "second_factor_needed_outside",
    "message": (
        "Van buiten kun je alleen inloggen met tweestapsverificatie. Doe dit thuis of via "
        "VPN, en zet daarna tweestapsverificatie aan."
    ),
}
RESET_LINK = re.compile(r"#(?P<token>[A-Za-z0-9_-]{43})$")

CADDY_PEERS = {"127.0.0.1"}


@pytest.fixture(autouse=True)
def _caddy_at(monkeypatch: pytest.MonkeyPatch) -> None:
    """Caddy is the peer of these requests (apps.common.net.from_caddy)."""
    from apps.common import net

    monkeypatch.setattr(net, "caddy_addresses", lambda **_kwargs: frozenset(CADDY_PEERS))


@pytest.fixture(autouse=True)
def _outside_with_the_web_on(settings: Settings) -> None:
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.local"]
    settings.BRIEVIE_BEHIND_CADDY = True
    settings.BRIEVIE_PUBLIC_WEB = True


@pytest.fixture
def user() -> User:
    return UserFactory.create()


def sign_in(
    client: Client, user: User, *, route: dict[str, str], password: str = TEST_PASSWORD
) -> tuple[int, dict[str, object]]:
    r = post_json(client, "/auth/login", {"email": user.email, "password": password}, route)
    return r.status_code, r.json()


def failures(user: User) -> int:
    row = SignInFailures.objects.filter(account_key=account_key(user.email)).first()
    return 0 if row is None else row.failures


def failed_rows(user: User) -> list[ServerAuditEvent]:
    return list(
        ServerAuditEvent.objects.filter(code="sign_in_failed", target_id=user.id).order_by(
            "created_at"
        )
    )


def nothing_pending(client: Client) -> bool:
    return PENDING_KEY not in client.session


# --- Signing in with the password -----------------------------------------------------------


def test_home_sign_in_without_a_factor_is_unchanged(user: User) -> None:
    client = Client()
    assert sign_in(client, user, route=PRIVATE)[0] == 200
    assert client.get("/api/v1/me", headers=PRIVATE).status_code == 200
    assert UserSession.objects.filter(user=user).count() == 1


def test_home_with_a_factor_still_asks_the_code(user: User) -> None:
    give_factor(user)
    status, body = sign_in(Client(), user, route=PRIVATE)
    assert (status, body["step"]) == (202, "code")


def test_public_route_with_a_factor_asks_the_code_and_then_signs_in(user: User) -> None:
    factor = give_factor(user)
    client = Client()

    status, body = sign_in(client, user, route=PUBLIC)
    assert (status, body["step"]) == (202, "code")
    assert not UserSession.objects.filter(user=user).exists()

    r = post_json(client, "/auth/login/code", {"code": factor.code()}, PUBLIC)
    assert r.status_code == 200, r.json()
    assert UserSession.objects.get(user=user).second_factor is True
    assert client.get("/api/v1/me", headers=PUBLIC).status_code == 200


def test_public_route_needs_a_second_factor(user: User) -> None:
    client = Client()

    status, body = sign_in(client, user, route=PUBLIC)

    assert (status, body) == (401, OUTSIDE)
    assert not UserSession.objects.filter(user=user).exists()
    assert nothing_pending(client)
    assert client.get("/api/v1/me", headers=PUBLIC).status_code == 401
    (row,) = failed_rows(user)
    assert row.values == {"step": "second_factor"}


def test_without_a_factor_the_right_password_answers_as_a_wrong_one(user: User) -> None:
    other = UserFactory.create()
    right = sign_in(Client(), user, route=PUBLIC)
    wrong = sign_in(Client(), other, route=PUBLIC, password=WRONG_PASSWORD)
    unknown = post_json(
        Client(), "/auth/login", {"email": "niemand@example.nl", "password": TEST_PASSWORD}, PUBLIC
    )

    assert right == wrong == (401, OUTSIDE)
    assert (unknown.status_code, unknown.json()) == (401, OUTSIDE)
    # The same counters, so the next attempt cannot tell either.
    assert failures(user) == failures(other) == 1


def test_a_wrong_password_with_a_factor_answers_the_same_outside(user: User) -> None:
    give_factor(user)
    assert sign_in(Client(), user, route=PUBLIC, password=WRONG_PASSWORD) == (401, OUTSIDE)


def test_the_right_password_without_a_factor_locks_as_wrong_ones_do(user: User) -> None:
    other = UserFactory.create()
    right = [sign_in(Client(), user, route=PUBLIC)[0] for _ in range(6)]
    wrong = [sign_in(Client(), other, route=PUBLIC, password=WRONG_PASSWORD)[0] for _ in range(6)]

    assert right == wrong
    assert right[-1] == 429


def test_a_refusal_outside_does_not_clear_the_count_of_wrong_passwords(user: User) -> None:
    sign_in(Client(), user, route=PUBLIC, password=WRONG_PASSWORD)
    sign_in(Client(), user, route=PUBLIC)
    assert failures(user) == 2


def test_the_home_sentence_stays_at_home(user: User) -> None:
    status, body = sign_in(Client(), user, route=PRIVATE, password=WRONG_PASSWORD)
    assert (status, body["message"]) == (401, HOME_SENTENCE)


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-Brievie-Route": "Private"}, {"X-Brievie-Route": "home"}],
    ids=["missing", "other-case", "unknown"],
)
def test_a_missing_or_unknown_route_is_public(user: User, headers: dict[str, str]) -> None:
    assert sign_in(Client(), user, route=headers) == (401, OUTSIDE)


def test_a_forged_private_route_from_a_peer_that_is_not_caddy_is_public(
    user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.common import net

    monkeypatch.setattr(net, "caddy_addresses", lambda **_kwargs: frozenset({"10.9.8.7"}))
    client = Client()

    assert sign_in(client, user, route=PRIVATE) == (401, OUTSIDE)
    assert not UserSession.objects.filter(user=user).exists()


def test_without_caddy_there_is_no_public_route(user: User, settings: Settings) -> None:
    settings.BRIEVIE_BEHIND_CADDY = False
    assert sign_in(Client(), user, route=PUBLIC)[0] == 200


def test_with_the_public_web_off_the_gate_answers_first(user: User, settings: Settings) -> None:
    settings.BRIEVIE_PUBLIC_WEB = False
    status, body = sign_in(Client(), user, route=PUBLIC)
    assert (status, body["code"]) == (403, "public_web_off")


def test_begin_sign_in_itself_refuses_outside_without_a_factor(user: User) -> None:
    # The wall every caller passes: whatever path forgets the rule, no session and no
    # pending `setup` (which would let a password alone enrol a factor from the internet).
    request = RequestFactory().post("/api/v1/auth/login", headers=PUBLIC)
    request.META["REMOTE_ADDR"] = "127.0.0.1"
    SessionMiddleware(lambda _r: HttpResponse()).process_request(request)
    request.user = AnonymousUser()

    with pytest.raises(ApiError) as caught:
        begin_sign_in(request, user, via=UserSession.Via.PASSWORD)

    assert (caught.value.status, caught.value.code) == (403, NEEDED_OUTSIDE["code"])
    assert not UserSession.objects.filter(user=user).exists()
    assert PENDING_KEY not in request.session


# --- A web session on the public route --------------------------------------------------------


def test_a_session_without_the_second_step_does_not_count_outside(user: User) -> None:
    client = Client()
    assert sign_in(client, user, route=PRIVATE)[0] == 200

    assert client.get("/api/v1/me", headers=PRIVATE).status_code == 200
    r = client.get("/api/v1/me", headers=PUBLIC)
    assert (r.status_code, r.json()["code"]) == (401, "not_authenticated")
    # Not ended: at home it still works.
    assert client.get("/api/v1/me", headers=PRIVATE).status_code == 200


def test_a_session_that_passed_the_second_step_counts_outside(user: User) -> None:
    factor = give_factor(user)
    client = Client()
    assert sign_in(client, user, route=PRIVATE)[0] == 202
    assert (
        post_json(client, "/auth/login/code", {"code": factor.code()}, PRIVATE).status_code == 200
    )

    assert client.get("/api/v1/me", headers=PUBLIC).status_code == 200


def test_reauthenticate_outside_without_a_factor_is_refused_before_the_password(
    user: User,
) -> None:
    client = Client()
    client.force_login(user)
    # A session that passed the second step once (the factor was removed since).
    UserSession.objects.filter(user=user).update(second_factor=True)

    r = post_json(client, "/auth/reauthenticate", {"password": WRONG_PASSWORD}, PUBLIC)

    assert (r.status_code, r.json()) == (403, NEEDED_OUTSIDE)
    assert failures(user) == 0


def test_reauthenticate_outside_with_a_factor_takes_the_code(user: User) -> None:
    factor: Factor = give_factor(user)
    client = Client()
    assert sign_in(client, user, route=PUBLIC)[0] == 202
    assert post_json(client, "/auth/login/code", {"code": factor.code()}, PUBLIC).status_code == 200

    r = post_json(
        client,
        "/auth/reauthenticate",
        {"password": TEST_PASSWORD, "code": factor.code(offset=1)},
        PUBLIC,
    )

    assert r.status_code == 204, r.json()


# --- The app's device token ---------------------------------------------------------------------


@pytest.mark.parametrize("public_web", [True, False])
def test_device_token_traffic_is_unaffected(
    user: User, settings: Settings, public_web: bool
) -> None:
    _, token = paired(user)
    settings.BRIEVIE_PUBLIC_WEB = public_web

    r = Client().get("/api/v1/me", headers={**PUBLIC, **bearer(token)})

    assert r.status_code == 200
    assert Device.objects.get(user=user).revoked_at is None


# --- Setting up the server ----------------------------------------------------------------------


def test_setup_on_the_public_route_is_refused_before_anything_is_made() -> None:
    User.objects.all().delete()
    body = {
        "setup_token": "x" * 20,
        "email": "eerste@example.nl",
        "password": TEST_PASSWORD,
        "name": "Eerste",
        "workspace_name": "Thuis",
        "workspace_kind": "home",
    }

    r = post_json(Client(), "/setup", body, PUBLIC)

    assert (r.status_code, r.json()) == (403, NEEDED_OUTSIDE)
    assert not User.objects.exists()


# --- A reset link -------------------------------------------------------------------------------


def reset_token(ws: Workspace, target: User) -> str:
    admin = Client()
    assert sign_in(admin, admin_of(ws), route=PRIVATE)[0] == 200
    r = post_json(admin, f"/users/{target.id}/password-reset", None, PRIVATE)
    assert r.status_code == 201, r.json()
    match = RESET_LINK.search(r.json()["link"])
    assert match is not None
    return match["token"]


def member_of(ws: Workspace) -> User:
    member = UserFactory.create()
    Membership.objects.create(user=member, workspace=ws, role=Membership.Role.MEMBER)
    return member


def test_a_reset_link_outside_without_a_factor_changes_nothing(ws_a: Workspace) -> None:
    member = member_of(ws_a)
    at_home = Client()
    assert sign_in(at_home, member, route=PRIVATE)[0] == 200
    token = reset_token(ws_a, member)

    r = post_json(
        Client(), "/auth/password-reset", {"token": token, "password": NEW_PASSWORD}, PUBLIC
    )

    assert (r.status_code, r.json()) == (403, NEEDED_OUTSIDE)
    member.refresh_from_db()
    assert member.check_password(TEST_PASSWORD)
    assert PasswordReset.objects.get(user=member).used_at is None
    assert at_home.get("/api/v1/me", headers=PRIVATE).status_code == 200
    # The link still works at home.
    home = Client()
    r = post_json(home, "/auth/password-reset", {"token": token, "password": NEW_PASSWORD}, PRIVATE)
    assert r.status_code == 200, r.json()


def test_a_reset_link_outside_with_a_factor_asks_the_code(ws_a: Workspace) -> None:
    member = member_of(ws_a)
    give_factor(member)
    token = reset_token(ws_a, member)

    r = post_json(
        Client(), "/auth/password-reset", {"token": token, "password": NEW_PASSWORD}, PUBLIC
    )

    assert (r.status_code, r.json()["step"]) == (202, "code")
    assert not UserSession.objects.filter(user=member).exists()


def test_a_dead_reset_link_outside_still_answers_dead() -> None:
    r = post_json(
        Client(), "/auth/password-reset", {"token": "x" * 43, "password": NEW_PASSWORD}, PUBLIC
    )
    assert (r.status_code, r.json()["code"]) == (400, "reset_link_invalid")


# --- An invitation ------------------------------------------------------------------------------


def invite(ws: Workspace) -> str:
    admin = Client()
    admin.force_login(admin_of(ws))
    r = post_json(admin, f"/workspaces/{ws.id}/invitations", {}, PRIVATE)
    assert r.status_code == 201, r.json()
    match = RESET_LINK.search(r.json()["link"])
    assert match is not None
    return match["token"]


def accept(
    client: Client, token: str, *, account: str, email: str, password: str = TEST_PASSWORD
) -> tuple[int, dict[str, object]]:
    body = {
        "token": token,
        "account": account,
        "name": "Nieuw lid",
        "email": email,
        "password": password,
    }
    r = post_json(client, "/invitations/accept", body, PUBLIC)
    return r.status_code, r.json()


def unused(ws: Workspace) -> bool:
    return not Invitation.objects.filter(workspace=ws, accepted_at__isnull=False).exists()


def test_a_new_account_by_invitation_outside_is_refused_and_the_link_stays(
    ws_a: Workspace,
) -> None:
    token = invite(ws_a)

    status, body = accept(Client(), token, account="new", email="nieuw@example.nl")

    assert (status, body) == (403, NEEDED_OUTSIDE)
    assert not User.objects.filter(email="nieuw@example.nl").exists()
    assert unused(ws_a)


def test_an_existing_account_without_a_factor_by_invitation_outside(ws_a: Workspace) -> None:
    token = invite(ws_a)
    account = UserFactory.create()
    other = UserFactory.create()

    right = accept(Client(), token, account="existing", email=account.email)
    wrong = accept(Client(), token, account="existing", email=other.email, password=WRONG_PASSWORD)

    assert right == wrong == (401, OUTSIDE)
    assert failures(account) == failures(other) == 1
    assert not Membership.objects.filter(user=account, workspace=ws_a).exists()
    assert unused(ws_a)
    (row,) = failed_rows(account)
    assert row.values == {"step": "second_factor"}


def test_an_existing_account_with_a_factor_joins_and_then_asks_the_code(ws_a: Workspace) -> None:
    token = invite(ws_a)
    account = UserFactory.create()
    give_factor(account)

    status, body = accept(Client(), token, account="existing", email=account.email)

    assert (status, body["step"]) == (202, "code")
    assert Membership.objects.filter(user=account, workspace=ws_a).exists()
    assert not UserSession.objects.filter(user=account).exists()


# --- What the server says about itself -----------------------------------------------------------


def test_the_second_step_is_available() -> None:
    assert django_settings.BRIEVIE_SECOND_FACTOR_AVAILABLE is True
