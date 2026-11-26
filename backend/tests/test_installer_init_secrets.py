"""init_secrets: the vault and the per-role volumes. Files only under `tmp_path`."""

import base64
import logging
import os
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from apps.common import crypto
from apps.installer import init_secrets
from apps.installer.init_secrets import (
    VAULT_NAMES,
    InitSecretsError,
    Layout,
    parse_import,
    run,
)

OWNER = (os.getuid(), os.getgid())
ROLES = ("postgres", "init", "api", "keeper", "intake")


def make_layout(root: Path) -> Layout:
    directories = {
        name: root / name for name in ("vault", "backup-config", "documents", "pgdata", *ROLES)
    }
    for directory in directories.values():
        directory.mkdir()
    return Layout(
        vault=directories["vault"],
        out={role: directories[role] for role in ROLES},
        backup_config=directories["backup-config"],
        documents=directories["documents"],
        pgdata=directories["pgdata"],
        owners={"vault": OWNER, **{role: OWNER for role in ROLES}},
    )


def vault_values(layout: Layout) -> dict[str, str]:
    return {
        name: (layout.vault / name).read_text().strip()
        for name in VAULT_NAMES
        if (layout.vault / name).exists()
    }


def names_in(path: Path) -> set[str]:
    return {entry.name for entry in path.iterdir()}


def snapshot(root: Path) -> dict[str, tuple[int, str]]:
    return {
        str(path.relative_to(root)): (path.stat().st_mtime_ns, path.read_text())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def import_text(**overrides: str) -> str:
    values = {
        "BRIEVIE_MASTER_KEY": "ab" * 32,
        "SECRET_KEY": base64.b64encode(b"s" * 48).decode(),
        "POSTGRES_PASSWORD": "p" * 24,
        "BRIEVIE_OWNER_PASSWORD": "o" * 24,
        "BRIEVIE_APP_PASSWORD": "a" * 24,
        "BRIEVIE_BACKUP_DB_PASSWORD": "b" * 24,
        "BRIEVIE_INSTALLATION_ID": "cd" * 16,
        **overrides,
    }
    return "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"


def test_first_run_makes_every_file_with_the_right_mode(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    report = run(layout, {})
    assert set(report.created) == set(VAULT_NAMES)
    for name in VAULT_NAMES:
        mode = stat.S_IMODE((layout.vault / name).stat().st_mode)
        assert mode == 0o400, name
    values = vault_values(layout)
    assert len(values["master_key"]) == 64
    assert len(values["installation_id"]) == 32
    assert len(values["postgres_password"]) == 48
    assert len(base64.b64decode(values["secret_key"])) == 48
    assert (layout.backup_config / "installation-id").read_text().strip() == (
        values["installation_id"]
    )
    assert stat.S_IMODE(layout.vault.stat().st_mode) == 0o700


def test_second_run_changes_nothing(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    run(layout, {})
    before = snapshot(tmp_path)
    report = run(layout, {})
    assert report.created == ()
    assert report.placed == 0
    assert snapshot(tmp_path) == before


def test_role_volumes_hold_only_what_the_role_needs(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    run(layout, {})
    assert names_in(layout.out["postgres"]) == {"postgres_password"}
    assert names_in(layout.out["api"]) == {
        "secret_key",
        "app_password",
        "master_key",
        "backup_password",
    }
    assert names_in(layout.out["keeper"]) == {
        "secret_key",
        "app_password",
        "backup_db_password",
        "backup_password",
    }
    assert "master_key" not in names_in(layout.out["keeper"])
    assert names_in(layout.out["init"]) == {
        "secret_key",
        "postgres_password",
        "owner_password",
        "app_password",
        "backup_db_password",
        "backup_password",
    }
    assert "master_key" not in names_in(layout.out["init"])
    for role in ("api", "keeper"):
        assert not names_in(layout.out[role]) & {"owner_password", "postgres_password"}
    assert stat.S_IMODE((layout.out["api"] / "master_key").stat().st_mode) == 0o400


def test_derived_backup_password_matches_the_crypto_function(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    run(layout, {})
    values = vault_values(layout)
    assert values["backup_password"] == crypto.derive_backup_password(
        bytes.fromhex(values["master_key"])
    )


def test_an_own_backup_password_wins_on_the_first_run(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    secret = tmp_path / "own"
    secret.write_text("my-own-backup-password\n")
    run(layout, {"BRIEVIE_BACKUP_PASSWORD_FILE": str(secret)})
    assert vault_values(layout)["backup_password"] == "my-own-backup-password"


def test_stale_files_leave_a_role_volume_but_never_the_vault(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    run(layout, {})
    leak = layout.out["keeper"] / "master_key"
    leak.write_text("x\n")
    (layout.out["api"] / "owner_password").write_text("y\n")
    (layout.out["api"] / "unrelated.txt").write_text("keep me\n")
    report = run(layout, {})
    assert report.removed == 2
    assert not leak.exists()
    assert not (layout.out["api"] / "owner_password").exists()
    assert (layout.out["api"] / "unrelated.txt").exists()
    assert (layout.vault / "master_key").exists()


def test_a_changed_role_file_is_put_back(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    run(layout, {})
    target = layout.out["api"] / "app_password"
    target.chmod(0o600)
    target.write_text("tampered\n")
    report = run(layout, {})
    assert report.placed == 1
    assert target.read_text().strip() == vault_values(layout)["app_password"]
    assert stat.S_IMODE(target.stat().st_mode) == 0o400


@pytest.mark.parametrize(
    "make_data",
    [
        lambda layout: (
            (layout.pgdata / "18" / "docker").mkdir(parents=True)
            or (layout.pgdata / "18" / "docker" / "PG_VERSION").write_text("18\n")
        ),
        lambda layout: (layout.documents / "a.bin").write_bytes(b"x"),
        lambda layout: (layout.backup_config / "installation-id").write_text("ab" * 16 + "\n"),
    ],
    ids=["pgdata", "documents", "installation-id"],
)
def test_data_with_an_empty_vault_refuses_and_writes_nothing(
    tmp_path: Path, make_data: Callable[[Layout], object]
) -> None:
    layout = make_layout(tmp_path)
    make_data(layout)
    before = snapshot(tmp_path)
    with pytest.raises(InitSecretsError) as caught:
        run(layout, {})
    assert caught.value.code == "data_without_keys"
    assert snapshot(tmp_path) == before
    assert names_in(layout.vault) == set()
    for role in ROLES:
        assert names_in(layout.out[role]) == set()


def test_a_vault_that_lost_the_master_key_refuses_when_data_exists(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    run(layout, {})
    (layout.vault / "master_key").chmod(0o600)
    (layout.vault / "master_key").unlink()
    (layout.documents / "a.bin").write_bytes(b"x")
    with pytest.raises(InitSecretsError) as caught:
        run(layout, {})
    assert caught.value.code == "data_without_keys"
    assert not (layout.vault / "master_key").exists()


def test_a_derived_backup_password_may_be_made_anew_with_data(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    run(layout, {})
    keep = vault_values(layout)
    (layout.vault / "backup_password").chmod(0o600)
    (layout.vault / "backup_password").unlink()
    (layout.documents / "a.bin").write_bytes(b"x")
    run(layout, {})
    assert vault_values(layout) == keep


def test_a_different_installation_id_refuses(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    run(layout, {})
    (layout.backup_config / "installation-id").chmod(0o600)
    (layout.backup_config / "installation-id").write_text("ef" * 16 + "\n")
    with pytest.raises(InitSecretsError) as caught:
        run(layout, {})
    assert caught.value.code == "installation_differs"


def test_a_vault_file_with_open_rights_refuses(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    run(layout, {})
    (layout.vault / "app_password").chmod(0o644)
    with pytest.raises(InitSecretsError) as caught:
        run(layout, {})
    assert caught.value.code == "vault_unsafe"


def test_a_hostile_link_in_place_of_a_vault_file_refuses(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    target = tmp_path / "elsewhere"
    target.write_text("important\n")
    os.symlink(target, layout.vault / "master_key")
    before = target.read_text()
    with pytest.raises(InitSecretsError) as caught:
        run(layout, {})
    assert caught.value.code == "vault_unsafe"
    assert target.read_text() == before


def test_a_link_in_a_role_volume_is_replaced_not_followed(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    run(layout, {})
    victim = tmp_path / "victim"
    victim.write_text("do not touch\n")
    link = layout.out["api"] / "secret_key"
    link.unlink()
    os.symlink(victim, link)
    run(layout, {})
    assert victim.read_text() == "do not touch\n"
    assert not link.is_symlink()
    assert link.read_text().strip() == vault_values(layout)["secret_key"]


def test_a_missing_directory_refuses(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    (layout.out["keeper"]).rmdir()
    with pytest.raises(InitSecretsError) as caught:
        run(layout, {})
    assert caught.value.code == "directory_missing"
    assert names_in(layout.vault) == set()


def test_a_crash_between_two_files_leaves_no_half_file_and_the_next_run_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = make_layout(tmp_path)
    real = init_secrets._write
    calls = {"count": 0}

    def crashing(directory: int, name: str, text: str, owner: tuple[int, int]) -> None:
        calls["count"] += 1
        if calls["count"] == 4:
            raise RuntimeError("power cut")
        real(directory, name, text, owner)

    monkeypatch.setattr(init_secrets, "_write", crashing)
    with pytest.raises(RuntimeError):
        run(layout, {})
    monkeypatch.setattr(init_secrets, "_write", real)
    assert not any(name.startswith(".") for name in names_in(layout.vault))
    assert "master_key" not in names_in(layout.vault)
    run(layout, {})
    assert set(vault_values(layout)) == set(VAULT_NAMES)
    assert names_in(layout.out["api"]) == {
        "secret_key",
        "app_password",
        "master_key",
        "backup_password",
    }


def test_leftover_temporary_files_are_removed(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    run(layout, {})
    (layout.out["api"] / ".master_key.tmp-0123456789abcdef").write_text("half")
    run(layout, {})
    assert not any(name.startswith(".") for name in names_in(layout.out["api"]))


# --- import ---


def test_import_fills_an_empty_vault_from_a_wrapper_file(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    (layout.documents / "a.bin").write_bytes(b"x")  # wrapper data exists
    run(layout, {}, import_values=parse_import(import_text()))
    values = vault_values(layout)
    assert values["master_key"] == "ab" * 32
    assert values["postgres_password"] == "p" * 24
    assert values["installation_id"] == "cd" * 16
    assert (layout.backup_config / "installation-id").read_text().strip() == "cd" * 16
    assert values["backup_password"] == crypto.derive_backup_password(bytes.fromhex("ab" * 32))


def test_import_with_only_the_master_key_makes_the_rest(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    run(layout, {}, import_values=parse_import(f"BRIEVIE_MASTER_KEY={'ab' * 32}\n"))
    values = vault_values(layout)
    assert values["master_key"] == "ab" * 32
    assert len(values["app_password"]) == 48


def test_import_matching_the_vault_is_a_no_op(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    imported = parse_import(import_text())
    run(layout, {}, import_values=imported)
    before = snapshot(tmp_path)
    report = run(layout, {}, import_values=imported)
    assert report.created == ()
    assert snapshot(tmp_path) == before


def test_import_differing_from_the_vault_refuses_and_changes_nothing(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    run(layout, {})
    before = snapshot(tmp_path)
    with pytest.raises(InitSecretsError) as caught:
        run(layout, {}, import_values=parse_import(import_text()))
    assert caught.value.code == "import_differs"
    assert snapshot(tmp_path) == before


def test_import_with_an_installation_id_that_differs_from_the_data_refuses(
    tmp_path: Path,
) -> None:
    layout = make_layout(tmp_path)
    (layout.backup_config / "installation-id").write_text("ef" * 16 + "\n")
    with pytest.raises(InitSecretsError) as caught:
        run(layout, {}, import_values=parse_import(import_text()))
    assert caught.value.code == "installation_differs"
    assert names_in(layout.vault) == set()


@pytest.mark.parametrize(
    "text",
    [
        "SECRET_KEY=short\n",
        import_text(BRIEVIE_MASTER_KEY="zz" * 32),
        import_text(BRIEVIE_MASTER_KEY="ab" * 31),
        import_text(BRIEVIE_APP_PASSWORD="has space and ;"),
        import_text(BRIEVIE_INSTALLATION_ID="nothex" * 5 + "ab"),
        "",
    ],
)
def test_import_refuses_malformed_input(text: str) -> None:
    with pytest.raises(InitSecretsError) as caught:
        parse_import(text)
    assert caught.value.code == "import_invalid"


def test_import_understands_quotes_and_export() -> None:
    text = (
        f"export BRIEVIE_MASTER_KEY='{'ab' * 32}'\n# comment\nBRIEVIE_APP_PASSWORD=\"{'a' * 24}\"\n"
    )
    parsed = parse_import(text)
    assert parsed["master_key"] == "ab" * 32
    assert parsed["app_password"] == "a" * 24


# --- never a value in any output ---


def test_no_secret_reaches_stdout_stderr_or_the_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    layout = make_layout(tmp_path)
    with caplog.at_level(logging.DEBUG):
        assert init_secrets.main([], {}, layout) == 0
    values = vault_values(layout)
    out = capsys.readouterr()
    haystack = out.out + out.err + caplog.text
    for name, value in values.items():
        assert value not in haystack, name


def test_refusals_print_a_sentence_without_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    layout = make_layout(tmp_path)
    (layout.documents / "a.bin").write_bytes(b"x")
    assert init_secrets.main([], {}, layout) == 1
    err = capsys.readouterr().err
    assert "sleutels ontbreken" in err
    assert "niets veranderd" in err


def test_import_through_the_command_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    layout = make_layout(tmp_path)
    env_file = tmp_path / "import.env"
    env_file.write_text(import_text())
    assert init_secrets.main(["--import", str(env_file)], {}, layout) == 0
    assert vault_values(layout)["master_key"] == "ab" * 32
    assert "ab" * 32 not in capsys.readouterr().out
    assert init_secrets.main(["--import", str(tmp_path / "missing")], {}, layout) == 1
    assert init_secrets.main(["--bogus"], {}, layout) == 2


def test_the_module_needs_no_django_models_or_database() -> None:
    source = Path(init_secrets.__file__).read_text()
    assert "django.db" not in source
    assert ".models" not in source


def test_a_large_wrapper_env_file_imports_through_the_command_line(
    tmp_path: Path,
) -> None:
    layout = make_layout(tmp_path)
    env_file = tmp_path / "import.env"
    env_file.write_text("# comment line to make the file large\n" * 400 + import_text())
    assert env_file.stat().st_size > 8000
    assert init_secrets.main(["--import", str(env_file)], {}, layout) == 0
    assert vault_values(layout)["master_key"] == "ab" * 32


def test_an_import_of_only_the_master_key_next_to_a_database_refuses_and_writes_nothing(
    tmp_path: Path,
) -> None:
    layout = make_layout(tmp_path)
    (layout.pgdata / "18" / "docker").mkdir(parents=True)
    (layout.pgdata / "18" / "docker" / "PG_VERSION").write_text("18\n")
    before = snapshot(tmp_path)
    with pytest.raises(InitSecretsError) as caught:
        run(layout, {}, import_values=parse_import(f"BRIEVIE_MASTER_KEY={'ab' * 32}\n"))
    assert caught.value.code == "import_incomplete"
    assert snapshot(tmp_path) == before
    assert names_in(layout.vault) == set()
    # With the superuser password of that database it goes through, and a second import is a no-op.
    full = parse_import(import_text())
    run(layout, {}, import_values=full)
    again = run(layout, {}, import_values=full)
    assert again.created == ()


def test_only_the_master_key_is_enough_where_there_is_no_database_yet(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    (layout.documents / "a.bin").write_bytes(b"x")  # documents alone have no password to fit
    run(layout, {}, import_values=parse_import(f"BRIEVIE_MASTER_KEY={'ab' * 32}\n"))
    assert vault_values(layout)["master_key"] == "ab" * 32


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every folder")
@pytest.mark.parametrize("which", ["pgdata", "documents"])
def test_an_unreadable_data_folder_refuses_instead_of_looking_empty(
    tmp_path: Path, which: str
) -> None:
    """Review of PR #45, point 5: an NFS root_squash folder must not read as "no data"."""
    layout = make_layout(tmp_path)
    folder = layout.pgdata if which == "pgdata" else layout.documents
    hidden = folder / "locked"
    hidden.mkdir()
    (hidden / "x").write_text("x")
    folder.chmod(0o000)
    try:
        with pytest.raises(InitSecretsError) as caught:
            run(layout, {})
    finally:
        folder.chmod(0o700)
    assert caught.value.code == "data_unreadable"
    assert names_in(layout.vault) == set()
    for role in ROLES:
        assert names_in(layout.out[role]) == set()


# Security review of PR #64 (M5)


def test_intake_gets_the_master_key_but_no_backup_password(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    run(layout, {})
    assert names_in(layout.out["intake"]) == {"secret_key", "app_password", "master_key"}


def test_a_compose_file_without_the_intake_volume_still_works(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    layout.out["intake"].rmdir()
    run(layout, {})
    assert not layout.out["intake"].exists()
    assert "master_key" in names_in(layout.out["api"])
