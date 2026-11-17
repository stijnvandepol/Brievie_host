from pathlib import Path

import pytest
from django.conf import settings
from django.db import connection
from pytest_django import DjangoDbBlocker, Settings

from apps.workspaces.models import Workspace
from apps.workspaces.services import create_workspace
from tests.factories import UserFactory

_CONNECTION_KEYS = ("NAME", "USER", "PASSWORD", "HOST", "PORT")


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
def _storage_and_master_key(settings: Settings, tmp_path: Path) -> None:
    """A fixed master key and a private storage root for every test."""
    settings.BRIEVIE_MASTER_KEY = "5a" * 32
    settings.BRIEVIE_STORAGE_ROOT = str(tmp_path / "documents")


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
