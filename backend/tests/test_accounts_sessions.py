import ast
import logging
import runpy
from datetime import timedelta
from pathlib import Path

import environ
import pytest
from django.apps import apps as global_apps
from django.contrib.auth import BACKEND_SESSION_KEY, SESSION_KEY
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError, connection
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from pytest_django import Settings

import brievie.settings
from apps.accounts import jobs
from apps.accounts.models import User, UserSession
from apps.accounts.sessions import (
    MODEL_BACKEND,
    describe_user_agent,
    rekey_session,
    session_policy,
    start_session,
)
from apps.audit.models import ServerAuditEvent
from tests.factories import TEST_PASSWORD, UserFactory
from tests.helpers import post_json

FIREFOX_WINDOWS = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0"
SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1"
)
CHROME_ANDROID = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
)
EDGE_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)
SAFARI_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.1 Safari/605.1.15"
)
CHROME_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) CriOS/131.0.6778.73 Mobile/15E148 Safari/604.1"
)

APPS_DIR = Path(__file__).resolve().parent.parent / "apps"


def login(client: Client, email: str, password: str = TEST_PASSWORD) -> int:
    return post_json(client, "/auth/login", {"email": email, "password": password}).status_code


def current_row(client: Client) -> UserSession:
    return UserSession.objects.get(session_key=client.session.session_key)


def server_events(code: str) -> list[ServerAuditEvent]:
    return list(ServerAuditEvent.objects.filter(code=code))


# --- Recording ---------------------------------------------------------------------------


def test_login_records_a_session_with_browser_and_address() -> None:
    user = UserFactory.create(email="jan@example.nl")
    client = Client(REMOTE_ADDR="192.0.2.7", HTTP_USER_AGENT=FIREFOX_WINDOWS)

    assert login(client, "jan@example.nl") == 200

    row = UserSession.objects.get()
    assert row.user == user
    assert row.session_key == client.session.session_key
    assert row.browser == "Firefox op Windows"
    assert row.client_ip == "192.0.2.7"
    assert row.method == "password"
    assert row.created_via == "password"


def test_force_login_records_a_session(client: Client) -> None:
    user = UserFactory.create()

    client.force_login(user)

    row = UserSession.objects.get()
    assert row.user == user
    assert row.session_key == client.session.session_key
    assert client.get("/api/v1/me").status_code == 200


def test_setup_records_a_session_made_by_setup(client: Client, settings: Settings) -> None:
    settings.BRIEVIE_SETUP_TOKEN = "test-installatiecode"
    body = {
        "setup_token": "test-installatiecode",
        "email": "beheer@example.nl",
        "password": "een-lang-wachtwoord-2026",
        "name": "Anna de Vries",
        "workspace_name": "Thuis",
        "workspace_kind": "home",
    }

    assert post_json(client, "/setup", body).status_code == 201

    row = current_row(client)
    assert row.created_via == "setup"
    assert server_events("sign_in_succeeded")[0].values == {"via": "setup"}


# --- Enforcement -------------------------------------------------------------------------


def test_a_session_without_record_is_signed_out(client: Client) -> None:
    client.force_login(UserFactory.create())
    key = client.session.session_key

    UserSession.objects.all().delete()
    r = client.get("/api/v1/me")

    assert r.status_code == 401
    # Fails closed: Django's own session is gone as well.
    assert not Session.objects.filter(session_key=key).exists()


def test_idle_session_ends_after_the_idle_limit(client: Client) -> None:
    client.force_login(UserFactory.create())
    UserSession.objects.update(last_used_at=timezone.now() - timedelta(days=15))

    assert client.get("/api/v1/me").status_code == 401
    assert not UserSession.objects.exists()


def test_session_just_inside_the_idle_limit_stays(client: Client) -> None:
    client.force_login(UserFactory.create())
    UserSession.objects.update(last_used_at=timezone.now() - timedelta(days=13, hours=23))

    assert client.get("/api/v1/me").status_code == 200


def test_session_ends_after_the_maximum_even_when_used(client: Client) -> None:
    client.force_login(UserFactory.create())
    UserSession.objects.update(
        created_at=timezone.now() - timedelta(days=91), last_used_at=timezone.now()
    )

    assert client.get("/api/v1/me").status_code == 401
    assert not UserSession.objects.exists()


def test_limits_come_from_the_settings(client: Client, settings: Settings) -> None:
    settings.BRIEVIE_SESSION_IDLE_DAYS = 2
    settings.BRIEVIE_SESSION_MAX_DAYS = 3
    client.force_login(UserFactory.create())
    UserSession.objects.update(last_used_at=timezone.now() - timedelta(days=2, minutes=1))

    assert session_policy("password").idle == timedelta(days=2)
    assert session_policy("password").maximum == timedelta(days=3)
    assert client.get("/api/v1/me").status_code == 401


def updates_of_the_record(queries: CaptureQueriesContext) -> int:
    return sum(
        1
        for query in queries.captured_queries
        if query["sql"].startswith('UPDATE "accounts_usersession"')
    )


def test_last_used_is_written_at_most_once_a_minute(client: Client) -> None:
    client.force_login(UserFactory.create())
    UserSession.objects.update(last_used_at=timezone.now() - timedelta(minutes=2))

    with CaptureQueriesContext(connection) as first:
        assert client.get("/api/v1/me").status_code == 200
    used = current_row(client).last_used_at
    with CaptureQueriesContext(connection) as second:
        assert client.get("/api/v1/me").status_code == 200

    assert updates_of_the_record(first) == 1
    assert updates_of_the_record(second) == 0
    assert timezone.now() - used < timedelta(seconds=30)
    assert current_row(client).last_used_at == used


def test_a_failing_last_used_write_does_not_fail_the_request(
    client: Client, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    client.force_login(UserFactory.create())
    UserSession.objects.update(last_used_at=timezone.now() - timedelta(minutes=2))

    def broken(*args: object, **kwargs: object) -> None:
        raise OperationalError("database is read-only")

    monkeypatch.setattr("apps.accounts.middleware.touch_session", broken)
    with caplog.at_level(logging.WARNING, logger="apps.accounts.middleware"):
        r = client.get("/api/v1/me")

    assert r.status_code == 200
    assert "last use" in caplog.text


def test_expiry_is_the_earlier_of_idle_and_maximum(client: Client) -> None:
    client.force_login(UserFactory.create())
    created = timezone.now() - timedelta(days=85)
    UserSession.objects.update(
        created_at=created, last_used_at=timezone.now() - timedelta(minutes=2)
    )

    assert client.get("/api/v1/me").status_code == 200

    expire = Session.objects.get(session_key=client.session.session_key).expire_date
    assert abs(expire - (created + timedelta(days=90))) < timedelta(seconds=1)


def test_a_fresh_session_expires_after_the_idle_limit(client: Client) -> None:
    client.force_login(UserFactory.create())

    expire = Session.objects.get(session_key=client.session.session_key).expire_date

    assert abs(expire - (timezone.now() + timedelta(days=14))) < timedelta(minutes=1)


def test_logout_deletes_the_record(client: Client) -> None:
    client.force_login(UserFactory.create())

    assert post_json(client, "/auth/logout").status_code == 204

    assert not UserSession.objects.exists()


# --- One place to sign in ----------------------------------------------------------------


def test_signing_in_again_as_the_same_user_keeps_one_row(client: Client) -> None:
    user = UserFactory.create(email="jan@example.nl")
    client.force_login(user)

    assert login(client, "jan@example.nl") == 200

    row = UserSession.objects.get()
    assert row.user == user
    assert row.session_key == client.session.session_key
    assert client.get("/api/v1/me").status_code == 200


def test_signing_in_as_another_user_replaces_the_row(client: Client) -> None:
    anne = UserFactory.create(email="anne@example.nl")
    bram = UserFactory.create(email="bram@example.nl")
    client.force_login(anne)
    anne_key = client.session.session_key

    assert login(client, "bram@example.nl") == 200

    assert not UserSession.objects.filter(session_key=anne_key).exists()
    assert not UserSession.objects.filter(user=anne).exists()
    assert UserSession.objects.get().user == bram
    assert client.get("/api/v1/me").json()["id"] == str(bram.id)


def test_start_session_uses_the_model_backend(rf: RequestFactory) -> None:
    user = User.objects.get(pk=UserFactory.create().pk)  # fresh: no `.backend` attribute
    request = rf.post("/api/v1/auth/login")
    request.session = SessionStore()
    request.user = user

    start_session(request, user, via="invitation")

    assert request.session[BACKEND_SESSION_KEY] == MODEL_BACKEND
    assert request.session[SESSION_KEY] == str(user.pk)
    row = UserSession.objects.get()
    assert (row.session_key, row.created_via) == (request.session.session_key, "invitation")


def test_rekey_session_follows_a_cycled_key(client: Client, rf: RequestFactory) -> None:
    client.force_login(UserFactory.create())
    request = rf.get("/")
    request.session = SessionStore(client.session.session_key)
    old_key = request.session.session_key
    assert old_key is not None

    request.session.cycle_key()
    rekey_session(request, old_key)
    request.session.save()
    client.cookies["sessionid"] = str(request.session.session_key)

    assert current_row(client).session_key == request.session.session_key
    assert client.get("/api/v1/me").status_code == 200


@pytest.mark.urls("tests.session_views")
def test_ending_the_current_session_flushes_instead_of_deleting(client: Client) -> None:
    user = UserFactory.create()
    client.force_login(user)
    other = Client()
    other.force_login(user)
    # The middleware writes "last used" and a new expiry into the session first.
    UserSession.objects.update(last_used_at=timezone.now() - timedelta(minutes=2))

    r = client.get("/test/end-all-my-sessions")

    assert r.status_code == 204
    assert not UserSession.objects.exists()
    assert client.get("/api/v1/me").status_code == 401
    assert other.get("/api/v1/me").status_code == 401


def test_start_session_is_the_only_caller_of_django_login() -> None:
    callers: set[str] = set()
    for path in APPS_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            imports_login = isinstance(node, ast.ImportFrom) and (
                node.module == "django.contrib.auth"
                and any(alias.name in {"login", "alogin"} for alias in node.names)
            )
            calls_through_module = (
                isinstance(node, ast.Attribute)
                and node.attr in {"login", "alogin"}
                and isinstance(node.value, ast.Name)
                and node.value.id == "auth"
            )
            if imports_login or calls_through_module:
                callers.add(str(path.relative_to(APPS_DIR)))

    assert callers == {"accounts/sessions.py"}


# --- Audit -------------------------------------------------------------------------------


def test_failed_sign_in_is_logged_without_the_password(
    client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    user = UserFactory.create(email="jan@example.nl", name="Jan")

    with caplog.at_level(logging.DEBUG):
        assert login(client, "jan@example.nl", "geheim-fout-wachtwoord") == 401

    # Read after the response: the 401 committed the request transaction with the row.
    (event,) = server_events("sign_in_failed")
    assert (event.actor_kind, event.actor_id) == ("user", user.id)
    assert (event.target_kind, event.target_id, event.target_name) == ("user", user.id, "Jan")
    assert event.values == {}
    assert "geheim-fout-wachtwoord" not in caplog.text
    assert "geheim-fout-wachtwoord" not in str(event.values)


def test_failed_sign_in_for_an_unknown_address_names_nobody(client: Client) -> None:
    assert login(client, "niemand@example.nl", "fout-wachtwoord") == 401

    (event,) = server_events("sign_in_failed")
    assert (event.actor_id, event.actor_name) == (None, "")
    assert (event.target_id, event.target_name) == (None, "")
    assert "niemand" not in str(
        ServerAuditEvent.objects.values_list("actor_name", "target_name", "values")
    )


def test_lockout_is_logged_once(client: Client) -> None:
    user = UserFactory.create(email="jan@example.nl")

    statuses = [login(client, "jan@example.nl", "fout-wachtwoord") for _ in range(6)]

    assert statuses == [401] * 5 + [429]
    (event,) = server_events("sign_in_locked_out")
    assert (event.target_kind, event.target_id) == ("user", user.id)
    assert len(server_events("sign_in_failed")) == 5


def test_login_is_throttled_per_address(client: Client, settings: Settings) -> None:
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

    statuses = [login(client, f"niemand{n}@example.nl", "fout") for n in range(31)]

    assert statuses[:30] == [401] * 30
    assert statuses[30] == 429
    # Another address is not held back.
    assert login(Client(REMOTE_ADDR="192.0.2.8"), "niemand@example.nl", "fout") == 401


def test_successful_sign_in_is_logged_with_via(client: Client) -> None:
    user = UserFactory.create(email="jan@example.nl")

    assert login(client, "jan@example.nl") == 200

    (event,) = server_events("sign_in_succeeded")
    assert (event.actor_kind, event.actor_id) == ("user", user.id)
    assert event.values == {"via": "password"}


# --- Browser description -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("agent", "text"),
    [
        (FIREFOX_WINDOWS, "Firefox op Windows"),
        (SAFARI_IPHONE, "Safari op iPhone"),
        (CHROME_ANDROID, "Chrome op Android"),
        (EDGE_WINDOWS, "Edge op Windows"),
        (SAFARI_MAC, "Safari op Mac"),
        (CHROME_IPHONE, "Chrome op iPhone"),
        ("curl/8.7.1", "Onbekende browser"),
        ("", "Onbekende browser"),
    ],
)
def test_describe_user_agent(agent: str, text: str) -> None:
    assert describe_user_agent(agent) == text


def test_a_long_user_agent_is_never_stored_raw() -> None:
    assert describe_user_agent("Firefox/1 " + "x" * 5000) == "Firefox"


# --- Upgrade and clean-up ----------------------------------------------------------------


def test_migration_records_existing_sessions() -> None:
    from importlib import import_module

    migration = import_module("apps.accounts.migrations.0005_usersession")
    user = UserFactory.create()
    signed_in = SessionStore()
    signed_in[SESSION_KEY] = str(user.pk)
    signed_in.create()
    anonymous = SessionStore()
    anonymous["something"] = "else"
    anonymous.create()
    expired = SessionStore()
    expired[SESSION_KEY] = str(user.pk)
    expired.create()
    Session.objects.filter(session_key=expired.session_key).update(
        expire_date=timezone.now() - timedelta(minutes=1)
    )
    UserSession.objects.all().delete()

    migration.record_existing_sessions(global_apps, None)
    migration.record_existing_sessions(global_apps, None)  # twice is harmless

    row = UserSession.objects.get()
    assert (row.session_key, row.user_id) == (signed_in.session_key, user.id)
    assert (row.method, row.created_via, row.browser) == ("password", "password", "")


def test_stale_sessions_are_removed() -> None:
    now = timezone.now()
    user = UserFactory.create()

    def session(name: str) -> str:
        store = SessionStore()
        store[SESSION_KEY] = str(user.pk)
        store.create()
        key = store.session_key
        assert key is not None
        UserSession.objects.create(
            user=user, session_key=key, method="password", last_used_at=now, browser=name
        )
        return key

    live = session("live")
    gone = session("gone")
    Session.objects.filter(session_key=gone).delete()
    expired = session("expired")
    Session.objects.filter(session_key=expired).update(expire_date=now - timedelta(seconds=1))
    too_old = session("too old")
    UserSession.objects.filter(session_key=too_old).update(created_at=now - timedelta(days=91))
    idle = session("idle")
    UserSession.objects.filter(session_key=idle).update(last_used_at=now - timedelta(days=15))

    jobs.remove_stale_sessions(timestamp=0)

    assert list(UserSession.objects.values_list("session_key", flat=True)) == [live]
    assert set(Session.objects.values_list("session_key", flat=True)) == {live}


def test_remove_stale_sessions_is_periodic_with_a_queueing_lock() -> None:
    from procrastinate.contrib.django import app as procrastinate_app

    task = procrastinate_app.tasks["remove_stale_sessions"]
    assert task.queue == "pipeline"
    assert task.queueing_lock == "remove_stale_sessions"
    periodic = {
        entry.task.name: entry.cron
        for entry in procrastinate_app.periodic_registry.periodic_tasks.values()
    }
    assert periodic["remove_stale_sessions"] == "23 * * * *"


# --- Settings ----------------------------------------------------------------------------


def evaluate_settings(monkeypatch: pytest.MonkeyPatch, **values: str) -> dict[str, object]:
    for name in ("BRIEVIE_SESSION_IDLE_DAYS", "BRIEVIE_SESSION_MAX_DAYS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(environ.Env, "read_env", lambda *args, **kwargs: None)
    return runpy.run_path(str(Path(brievie.settings.__file__)))


def test_session_settings_default_to_14_and_90_days(monkeypatch: pytest.MonkeyPatch) -> None:
    values = evaluate_settings(monkeypatch)

    assert values["BRIEVIE_SESSION_IDLE_DAYS"] == 14
    assert values["BRIEVIE_SESSION_MAX_DAYS"] == 90
    assert values["SESSION_COOKIE_AGE"] == 14 * 24 * 3600


@pytest.mark.parametrize(
    "values",
    [
        {"BRIEVIE_SESSION_IDLE_DAYS": "0"},
        {"BRIEVIE_SESSION_MAX_DAYS": "0", "BRIEVIE_SESSION_IDLE_DAYS": "0"},
        {"BRIEVIE_SESSION_IDLE_DAYS": "30", "BRIEVIE_SESSION_MAX_DAYS": "20"},
    ],
)
def test_impossible_session_settings_are_refused(
    monkeypatch: pytest.MonkeyPatch, values: dict[str, str]
) -> None:
    with pytest.raises(ImproperlyConfigured):
        evaluate_settings(monkeypatch, **values)
