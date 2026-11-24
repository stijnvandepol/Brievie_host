from collections.abc import Iterator
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth.signals import user_logged_in
from django.db import connection
from django.http import HttpRequest
from django.utils import translation
from psycopg.conninfo import make_conninfo
from pytest_django import DjangoDbBlocker, Settings

from apps.accounts.models import User, UserSession
from apps.accounts.sessions import record_session
from apps.backup import safepaths
from apps.workspaces.models import Workspace
from apps.workspaces.services import create_workspace
from tests.factories import UserFactory
from tests.installer_support import connect, scratch  # noqa: F401 - fixtures of the installer tests

_CONNECTION_KEYS = ("NAME", "USER", "PASSWORD", "HOST", "PORT")
_TEST_SESSION_RECEIVER = "tests_record_session"


def record_test_session(sender: object, request: HttpRequest, user: User, **kwargs: object) -> None:
    """Records the session of `Client.force_login`, which calls Django's `login` directly.

    Only the test run connects this. In production nothing records a session but
    `start_session`, so a direct `login()` is signed out by the middleware.
    """
    record_session(request, user, method=UserSession.Method.PASSWORD)


@pytest.fixture(scope="session", autouse=True)
def _record_force_login_sessions() -> Iterator[None]:
    user_logged_in.connect(record_test_session, dispatch_uid=_TEST_SESSION_RECEIVER)
    yield
    user_logged_in.disconnect(dispatch_uid=_TEST_SESSION_RECEIVER)


@pytest.fixture
def without_test_session_receiver() -> Iterator[None]:
    """For this test, sessions are recorded as in production: by `start_session` only."""
    user_logged_in.disconnect(dispatch_uid=_TEST_SESSION_RECEIVER)
    yield
    user_logged_in.connect(record_test_session, dispatch_uid=_TEST_SESSION_RECEIVER)


@pytest.fixture(autouse=True)
def _default_language() -> Iterator[None]:
    """Each test starts in the default language (Dutch). A request activates the
    language it answers in for its thread and leaves it there, as Django does; the next
    request activates its own, but code a test runs outside a request would inherit it."""
    translation.activate(settings.LANGUAGE_CODE)
    yield
    translation.activate(settings.LANGUAGE_CODE)


@pytest.fixture(scope="session")
def django_db_modify_db_settings(django_db_modify_db_settings_parallel_suffix: None) -> None:
    """Run the suite as the schema owner role.

    The owner can create the test database, and, unlike a superuser, it is
    subject to forced row-level security, so the tests see what the app sees.
    """
    owner = settings.DATABASE_OWNER
    if owner is None:
        pytest.fail("The test run needs DATABASE_OWNER_URL (see deploy/.env.example).")
    default = settings.DATABASES["default"]
    for key in _CONNECTION_KEYS:
        default[key] = owner[key]


@pytest.fixture
def backup_conninfo() -> str:
    """libpq conninfo for the test database as the read-only back-up role."""
    backup = settings.DATABASE_BACKUP
    if backup is None:
        pytest.fail(
            "The test run needs DATABASE_BACKUP_URL (see deploy/.env.example); run make dev."
        )
    return make_conninfo(
        dbname=str(connection.settings_dict["NAME"]),
        user=str(backup["USER"]),
        password=str(backup["PASSWORD"]),
        host=str(backup["HOST"]),
        port=str(backup["PORT"]),
    )


@pytest.fixture(autouse=True)
def _database(db: None) -> None:
    """Every request runs in a transaction (ATOMIC_REQUESTS), so every test needs the database."""


@pytest.fixture(scope="session", autouse=True)
def _test_role_is_subject_to_rls(django_db_setup: None, django_db_blocker: DjangoDbBlocker) -> None:
    """Forced RLS does not bind superusers or BYPASSRLS roles; such a run proves nothing."""
    with django_db_blocker.unblock(), connection.cursor() as c:
        c.execute(
            "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        row = c.fetchone()
    if row is None or row[1] or row[2]:
        pytest.fail(f"Tests must run as a role without SUPERUSER and BYPASSRLS, got {row!r}")


@pytest.fixture(autouse=True)
def _storage_and_master_key(settings: Settings, tmp_path: Path) -> Iterator[None]:
    """A fixed master key and private directories for every test.

    No test can reach /data/..., /backup or a real restic by default (on a Linux CI
    runner or in a dev container those paths may exist and be writable): every
    directory setting points into the test's own tmp_path, and restic at a path that
    does not exist. Tests that need the fake restic or the production values set them.
    """
    # The only way paths outside the fixed production list become destroyable: this
    # test's own tmp_path, installed explicitly (never from TMPDIR or a setting).
    safepaths.allow_test_root(tmp_path)
    settings.BRIEVIE_MASTER_KEY = "5a" * 32
    settings.BRIEVIE_STORAGE_ROOT = str(tmp_path / "documents")
    settings.BRIEVIE_AUTHORITY_COPY_DIR = str(tmp_path / "authority")
    settings.BRIEVIE_BACKUP_CONFIG_DIR = str(tmp_path / "backup-config")
    settings.BRIEVIE_BACKUP_MOUNT = str(tmp_path / "backup")
    settings.BRIEVIE_RESTIC_BINARY = str(tmp_path / "no-restic-here" / "restic")
    yield
    safepaths.clear_test_root()


@pytest.fixture
def ws_a() -> Workspace:
    return create_workspace(name="Thuis", kind="home", admin=UserFactory.create())


@pytest.fixture
def ws_b() -> Workspace:
    return create_workspace(name="Bedrijf", kind="business", admin=UserFactory.create())


@pytest.fixture
def without_preset_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Workspaces made in the test get the preset labels but not their rules.

    For tests of name matching, AI picks and the review that do not want the preset
    rules' proposals mixed in (those rules have their own tests).
    """
    monkeypatch.setattr("apps.rules.presets.ensure_preset_rules", lambda workspace: None)
