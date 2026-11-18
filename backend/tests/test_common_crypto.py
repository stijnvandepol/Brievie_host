import io
import logging
import os

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from pytest_django import Settings

from apps.common import crypto
from apps.workspaces.context import workspace_context
from apps.workspaces.keys import workspace_key
from apps.workspaces.models import Workspace, WorkspaceKey

KEY = bytes(range(32))
OTHER_KEY = bytes(range(1, 33))


def test_seal_and_open_round_trip() -> None:
    sealed = crypto.seal(KEY, b"een brief", aad=b"pagina-1")

    assert sealed.startswith(b"BRV1")
    assert b"een brief" not in sealed
    assert crypto.open_sealed(KEY, sealed, aad=b"pagina-1") == b"een brief"


def test_two_seals_of_the_same_plaintext_differ() -> None:
    assert crypto.seal(KEY, b"x", aad=b"a") != crypto.seal(KEY, b"x", aad=b"a")


def test_wrong_key_is_refused() -> None:
    sealed = crypto.seal(KEY, b"x", aad=b"a")
    with pytest.raises(crypto.DecryptionError):
        crypto.open_sealed(OTHER_KEY, sealed, aad=b"a")


def test_wrong_aad_is_refused() -> None:
    sealed = crypto.seal(KEY, b"x", aad=b"pagina-1")
    with pytest.raises(crypto.DecryptionError):
        crypto.open_sealed(KEY, sealed, aad=b"pagina-2")


def test_changed_byte_is_refused() -> None:
    sealed = bytearray(crypto.seal(KEY, b"een brief", aad=b"a"))
    sealed[-1] ^= 1
    with pytest.raises(crypto.DecryptionError):
        crypto.open_sealed(KEY, bytes(sealed), aad=b"a")


@pytest.mark.parametrize("sealed", [b"", b"BRV1", b"XXXX" + bytes(40), bytes(10)])
def test_malformed_input_is_refused(sealed: bytes) -> None:
    with pytest.raises(crypto.DecryptionError):
        crypto.open_sealed(KEY, sealed, aad=b"a")


def test_master_key_comes_from_the_settings(settings: Settings) -> None:
    settings.BRIEVIE_MASTER_KEY = KEY.hex()
    assert crypto.master_key() == KEY


@pytest.mark.parametrize("value", ["", "abc", "zz" * 32, "00" * 31, "00" * 33])
def test_missing_or_malformed_master_key_is_refused(settings: Settings, value: str) -> None:
    settings.BRIEVIE_MASTER_KEY = value
    with pytest.raises(ImproperlyConfigured, match="BRIEVIE_MASTER_KEY"):
        crypto.master_key()


def test_commands_that_do_not_need_the_key_run_without_it(settings: Settings) -> None:
    settings.BRIEVIE_MASTER_KEY = ""
    call_command("export_openapi", "--check")


def test_check_master_key_command(settings: Settings) -> None:
    call_command("check_master_key")
    settings.BRIEVIE_MASTER_KEY = ""
    with pytest.raises(CommandError, match="BRIEVIE_MASTER_KEY"):
        call_command("check_master_key")


def test_workspace_key_is_stable_per_workspace_and_differs_between_them(
    ws_a: Workspace, ws_b: Workspace
) -> None:
    with workspace_context(ws_a.id):
        first = workspace_key(ws_a.id)
        again = workspace_key(ws_a.id)
    with workspace_context(ws_b.id):
        other = workspace_key(ws_b.id)

    assert len(first) == 32
    assert first == again
    assert first != other


def test_stored_workspace_key_is_wrapped(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        key = workspace_key(ws_a.id)
        stored = bytes(WorkspaceKey.objects.get().wrapped_key)

    assert key not in stored
    assert crypto.open_sealed(crypto.master_key(), stored, aad=ws_a.id.bytes) == key


def test_wrapped_key_cannot_be_moved_to_another_workspace(ws_a: Workspace, ws_b: Workspace) -> None:
    with workspace_context(ws_a.id):
        workspace_key(ws_a.id)
        stored = bytes(WorkspaceKey.objects.get().wrapped_key)

    with pytest.raises(crypto.DecryptionError):
        crypto.open_sealed(crypto.master_key(), stored, aad=ws_b.id.bytes)


def test_new_workspace_gets_its_key(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        assert WorkspaceKey.objects.count() == 1


def test_workspace_key_needs_the_workspace_context(ws_a: Workspace) -> None:
    with pytest.raises(RuntimeError, match="context"):
        workspace_key(ws_a.id)


def test_random_bytes_are_used_for_keys(ws_a: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    with workspace_context(ws_a.id):
        WorkspaceKey.objects.all().delete()
        monkeypatch.setattr(os, "urandom", lambda n: b"\x07" * n)
        assert workspace_key(ws_a.id) == b"\x07" * 32


def test_backup_password_has_a_fixed_vector() -> None:
    # Never change this: existing back-ups are encrypted with the derived password.
    assert crypto.derive_backup_password(bytes.fromhex("5a" * 32)) == (
        "cc224115e04b5ef8d0fabc864ae85ef455c6ee2a24d4fef9182f6c0884257571"
    )


def test_backup_password_differs_per_master_key() -> None:
    other = crypto.derive_backup_password(bytes(32))

    assert other.startswith("43f6f658")
    assert other != crypto.derive_backup_password(bytes.fromhex("5a" * 32))


def test_derive_backup_password_command(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    out = io.StringIO()
    caplog.set_level(logging.DEBUG)

    call_command("derive_backup_password", stdout=out)

    assert out.getvalue() == crypto.derive_backup_password(bytes.fromhex("5a" * 32)) + "\n"
    assert not caplog.records


def test_derive_backup_password_command_needs_a_valid_master_key(settings: Settings) -> None:
    settings.BRIEVIE_MASTER_KEY = "abc"
    out = io.StringIO()

    with pytest.raises(CommandError, match="BRIEVIE_MASTER_KEY"):
        call_command("derive_backup_password", stdout=out)
    assert out.getvalue() == ""


@pytest.mark.parametrize("value", ["", "abc", "zz" * 32])
def test_derive_backup_password_says_what_is_wrong_in_dutch(settings: Settings, value: str) -> None:
    settings.BRIEVIE_MASTER_KEY = value
    with pytest.raises(CommandError) as caught:
        call_command("derive_backup_password", stdout=io.StringIO())
    assert str(caught.value) == (
        "BRIEVIE_MASTER_KEY in deploy/.env ontbreekt of is geen 64 tekens lang "
        "(cijfers en a tot en met f). Zonder de juiste sleutel zijn de documenten niet te lezen."
    )
