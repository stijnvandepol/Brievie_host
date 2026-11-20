"""The actor helpers that the visibility policies read (PB T1, plan Task 3)."""

from uuid import UUID, uuid4

import pytest
from django.db import connection, transaction

APP_ROLE = "brievie_app"
HELPERS = ("public.brievie_actor_id()", "public.brievie_is_system()")


def _with_actor(value: str | None, expression: str) -> object:
    with transaction.atomic(), connection.cursor() as c:
        if value is not None:
            c.execute("SELECT set_config('brievie.actor', %s, true)", [value])
        c.execute(f"SELECT {expression}")
        row = c.fetchone()
    assert row is not None
    return row[0]


def test_actor_id_is_the_uuid_of_a_user_actor() -> None:
    user_id = uuid4()
    assert _with_actor(str(user_id), "public.brievie_actor_id()") == user_id


@pytest.mark.parametrize(
    "value", ["system", "", "SYSTEM", "not-a-uuid", "0000000-0000-0000-0000-000000000000"]
)
def test_actor_id_is_null_for_anything_else(value: str) -> None:
    assert _with_actor(value, "public.brievie_actor_id()") is None


def test_actor_id_is_null_without_the_setting() -> None:
    assert _with_actor(None, "public.brievie_actor_id()") is None


def test_is_system_only_for_the_exact_word() -> None:
    assert _with_actor("system", "public.brievie_is_system()") is True
    for value in ("System", "", str(uuid4())):
        assert _with_actor(value, "public.brievie_is_system()") is False
    assert _with_actor(None, "public.brievie_is_system()") is False


def _proc(signature: str, column: str) -> object:
    with connection.cursor() as c:
        c.execute(
            f"SELECT {column} FROM pg_proc WHERE oid = %s::regprocedure",  # noqa: S608
            [signature],
        )
        row = c.fetchone()
    assert row is not None
    return row[0]


@pytest.mark.parametrize("signature", HELPERS)
def test_helpers_pin_search_path_and_are_stable(signature: str) -> None:
    assert _proc(signature, "proconfig") == ['search_path=""']
    assert _proc(signature, "provolatile") == "s"
    assert _proc(signature, "prosecdef") is False


@pytest.mark.parametrize("signature", HELPERS)
def test_helpers_are_owned_by_the_schema_owner(signature: str) -> None:
    with connection.cursor() as c:
        c.execute("SELECT current_user")
        row = c.fetchone()
    assert row is not None
    assert _proc(signature, "pg_get_userbyid(proowner)") == row[0]


@pytest.mark.parametrize("signature", HELPERS)
def test_app_role_may_execute_the_helpers(signature: str) -> None:
    with connection.cursor() as c:
        c.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", [APP_ROLE, signature])
        row = c.fetchone()
    assert row == (True,)


def test_actor_id_returns_a_uuid_type() -> None:
    value = _with_actor(str(UUID(int=7)), "public.brievie_actor_id()")
    assert isinstance(value, UUID)
