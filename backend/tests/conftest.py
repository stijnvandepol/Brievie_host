import pytest
from django.conf import settings

_CONNECTION_KEYS = ("NAME", "USER", "PASSWORD", "HOST", "PORT")


@pytest.fixture(scope="session")
def django_db_modify_db_settings(django_db_modify_db_settings_parallel_suffix: None) -> None:
    """Run the suite as the schema owner role.

    The owner can create the test database, and, unlike a superuser, it is
    subject to forced row-level security, so the tests see what the app sees.
    """
    owner = settings.DATABASE_OWNER
    default = settings.DATABASES["default"]
    for key in _CONNECTION_KEYS:
        default[key] = owner[key]


@pytest.fixture(autouse=True)
def _database(db: None) -> None:
    """Every request runs in a transaction (ATOMIC_REQUESTS), so every test needs the database."""
