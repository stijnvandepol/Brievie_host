"""Every way in stops for the second step (two-step sign-in S 3, plan Task 6)."""

import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpRequest, HttpResponse
from django.test import Client, RequestFactory
from django.utils import timezone
from pytest_django import Settings

from apps.accounts.models import AccountNotice, SecondFactor, User, UserSession
from apps.accounts.second_factor import passed_without_factor
from apps.accounts.sessions import start_session
from apps.accounts.signin import PENDING_KEY, pending
from apps.workspaces.models import Membership, Workspace
from apps.workspaces.services import create_workspace
from tests.factories import TEST_PASSWORD, UserFactory
from tests.helpers import SETUP_TOKEN, post_json
from tests.second_factor_support import give_factor

BACKEND_DIR = Path(__file__).resolve().parents[1]
RESET_LINK = re.compile(r"#(?P<token>[A-Za-z0-9_-]{43})$")
NEW_PASSWORD = TEST_PASSWORD + "-nieuw"
SETUP_BODY: dict[str, object] = {
    "setup_token": SETUP_TOKEN,
    "email": "beheer@example.nl",
    "password": NEW_PASSWORD,
    "name": "Beheerder",
    "workspace_name": "Thuis",
    "workspace_kind": "home",
}


@pytest.fixture(autouse=True)
def _fast_hashing_and_public_url(settings: Settings) -> None:
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.local"]
    settings.BRIEVIE_SETUP_TOKEN = SETUP_TOKEN


def admin_client(ws: Workspace) -> Client:
    client = Client()
    client.force_login(Membership.objects.get(workspace=ws, role="admin").user)
    return client


def by_password(client: Client, user: User) -> tuple[int, dict[str, object]]:
    r = post_json(client, "/auth/login", {"email": user.email, "password": TEST_PASSWORD})
    return r.status_code, r.json()


def by_reset(client: Client, user: User) -> tuple[int, dict[str, object]]:
    admin = UserFactory.create(is_server_admin=True)
    maker = Client()
    maker.force_login(admin)
    link = post_json(maker, f"/users/{user.id}/password-reset").json()["link"]
    match = RESET_LINK.search(link)
    assert match is not None
    r = post_json(
        client, "/auth/password-reset", {"token": match["token"], "password": NEW_PASSWORD}
    )
    return r.status_code, r.json()


def by_invitation(client: Client, user: User) -> tuple[int, dict[str, object]]:
    ws = create_workspace(name="Kantoor", kind="business", admin=UserFactory.create())
    r = post_json(admin_client(ws), f"/workspaces/{ws.id}/invitations", {})
    match = RESET_LINK.search(r.json()["link"])
    assert match is not None, r.json()
    body = {
        "token": match["token"],
        "account": "existing",
        "name": "",
        "email": user.email,
        "password": TEST_PASSWORD,
    }
    answer = post_json(client, "/invitations/accept", body)
    return answer.status_code, answer.json()


CALLERS = {"password": by_password, "reset": by_reset, "invitation": by_invitation}


@pytest.mark.parametrize("via", list(CALLERS))
def test_every_caller_stops_at_the_code_step(via: str) -> None:
    user = UserFactory.create()
    give_factor(user)
    client = Client()

    status, body = CALLERS[via](client, user)

    assert (status, body) == (202, {"step": "code", "expires_in_seconds": 300})
    assert not UserSession.objects.filter(user=user).exists()
    assert client.get("/api/v1/me").status_code == 401


def test_the_invitation_joins_before_the_code() -> None:
    user = UserFactory.create()
    give_factor(user)

    by_invitation(Client(), user)

    assert Membership.objects.filter(user=user, workspace__name="Kantoor").exists()


def test_the_setup_stops_at_setting_up_when_admins_must_and_no_grace(
    settings: Settings,
) -> None:
    settings.BRIEVIE_REQUIRE_SECOND_FACTOR = "server_admins"
    settings.BRIEVIE_SECOND_FACTOR_GRACE_DAYS = 0
    client = Client()

    r = post_json(client, "/setup", SETUP_BODY)

    assert (r.status_code, r.json()) == (202, {"step": "setup", "expires_in_seconds": 300})
    assert User.objects.get().is_server_admin
    assert not UserSession.objects.exists()


@pytest.mark.parametrize("via", list(CALLERS))
def test_no_factor_signs_in_as_today(via: str) -> None:
    user = UserFactory.create()
    client = Client()

    status, _body = CALLERS[via](client, user)

    assert status == 200
    row = UserSession.objects.get(user=user)
    assert row.second_factor is False
    assert row.created_via == via
    assert client.get("/api/v1/me").status_code == 200


def test_setup_without_a_rule_signs_in_as_today() -> None:
    client = Client()

    assert post_json(client, "/setup", SETUP_BODY).status_code == 201
    assert UserSession.objects.get().second_factor is False


def test_grace_signs_in_with_one_notice(settings: Settings) -> None:
    settings.BRIEVIE_REQUIRE_SECOND_FACTOR = "everyone"
    settings.BRIEVIE_SECOND_FACTOR_GRACE_DAYS = 7
    user = UserFactory.create()

    assert by_password(Client(), user)[0] == 200
    assert by_password(Client(), user)[0] == 200

    (notice,) = AccountNotice.objects.filter(user=user, code="second_factor_required")
    assert set(notice.values) == {"until"}
    assert User.objects.get(pk=user.pk).second_factor_required_since is not None


def test_after_the_grace_the_password_leads_to_setting_up(settings: Settings) -> None:
    settings.BRIEVIE_REQUIRE_SECOND_FACTOR = "everyone"
    user = UserFactory.create(second_factor_required_since=timezone.now() - timedelta(days=8))

    status, body = by_password(Client(), user)

    assert (status, body["step"]) == (202, "setup")
    assert not UserSession.objects.filter(user=user).exists()


def test_pending_session_is_not_signed_in() -> None:
    user = UserFactory.create()
    give_factor(user)
    client = Client()
    by_password(client, user)

    assert client.get("/api/v1/me").status_code == 401
    assert client.get("/api/v1/me/sessions").status_code == 401
    assert post_json(client, "/devices/pairing-codes").status_code == 401
    assert client.get("/api/v1/me/notices").status_code == 401
    assert not UserSession.objects.exists()


def browser() -> HttpRequest:
    request = RequestFactory().post("/api/v1/auth/login", REMOTE_ADDR="192.0.2.8")
    SessionMiddleware(lambda r: HttpResponse()).process_request(request)
    request.session.save()
    request.user = AnonymousUser()
    return request


def test_pending_expires_after_five_minutes() -> None:
    from apps.accounts.signin import begin_sign_in

    user = UserFactory.create()
    give_factor(user)
    request = browser()
    started = timezone.now()
    begin_sign_in(request, user, via="password", now=started)

    assert pending(request, now=started + timedelta(minutes=4, seconds=59)) is not None
    assert pending(request, now=started + timedelta(minutes=5, seconds=1)) is None
    assert PENDING_KEY not in request.session


def test_session_key_cycled_on_pending() -> None:
    from apps.accounts.signin import begin_sign_in

    user = UserFactory.create()
    give_factor(user)
    request = browser()
    before = request.session.session_key

    begin_sign_in(request, user, via="password")

    assert request.session.session_key != before
    stored = request.session[PENDING_KEY]
    assert set(stored) == {
        "user_id",
        "via",
        "method",
        "step",
        "started_at",
        "failures",
        "factor_id",
        "auth_hash",
    }
    assert stored["factor_id"] == str(SecondFactor.objects.get(user=user).id)


def test_a_signed_in_browser_is_signed_out_by_a_pending_sign_in() -> None:
    other = UserFactory.create()
    user = UserFactory.create()
    give_factor(user)
    client = Client()
    assert by_password(client, other)[0] == 200

    assert by_password(client, user)[0] == 202

    assert client.get("/api/v1/me").status_code == 401
    assert not UserSession.objects.filter(user=other).exists()


def test_start_session_without_a_pass_raises() -> None:
    user = UserFactory.create()
    give_factor(user)
    request = browser()

    with pytest.raises(RuntimeError, match="second factor"):
        start_session(request, user, via="password", second_factor=None)
    with pytest.raises(RuntimeError, match="second factor"):
        start_session(request, user, via="password", second_factor=passed_without_factor(user))
    assert not UserSession.objects.exists()


def test_start_session_refuses_another_users_pass() -> None:
    user = UserFactory.create()
    other = UserFactory.create()

    with pytest.raises(RuntimeError, match="second factor"):
        start_session(browser(), user, via="password", second_factor=passed_without_factor(other))


def test_start_session_refuses_none_past_the_grace(settings: Settings) -> None:
    settings.BRIEVIE_REQUIRE_SECOND_FACTOR = "everyone"
    user = UserFactory.create(second_factor_required_since=timezone.now() - timedelta(days=30))

    with pytest.raises(RuntimeError, match="second factor"):
        start_session(browser(), user, via="password", second_factor=passed_without_factor(user))


def test_only_the_sign_in_module_calls_start_session() -> None:
    callers = sorted(
        str(path.relative_to(BACKEND_DIR))
        for path in (BACKEND_DIR / "apps").rglob("*.py")
        if "start_session(" in path.read_text(encoding="utf-8")
    )

    assert callers == ["apps/accounts/sessions.py", "apps/accounts/signin.py"]


def test_a_sign_in_records_how_the_second_step_was_passed() -> None:
    from apps.audit.models import ServerAuditEvent

    user = UserFactory.create()
    by_password(Client(), user)

    (row,) = ServerAuditEvent.objects.filter(code="sign_in_succeeded")
    assert row.values == {"via": "password", "second_factor": "none"}
