"""Moving from a ./brievie install: the file deploy/init-env.sh makes imports as it is.

Files only under tmp_path (the real moving, with Docker, is the CI job `adopt`).
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from apps.common import crypto
from apps.installer import init_secrets
from apps.installer.init_secrets import VAULT_NAMES, Layout, parse_import, run

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
OWNER = (os.getuid(), os.getgid())
ROLES = ("postgres", "api", "keeper")


@pytest.fixture
def wrapper_env(tmp_path: Path) -> Path:
    """The deploy/.env the wrapper's own script makes, with the keys it makes."""
    target = tmp_path / "deploy"
    target.mkdir()
    for name in ("init-env.sh", ".env.example"):
        shutil.copy(DEPLOY / name, target / name)
    subprocess.run(  # noqa: S603
        ["/bin/sh", str(target / "init-env.sh")],
        capture_output=True,
        check=True,
        stdin=subprocess.DEVNULL,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "BRIEVIE_HOSTNAME": "mijnnas.local",
        },
    )
    return target / ".env"


def layout(root: Path) -> Layout:
    names = ("vault", "backup-config", "documents", "pgdata", *ROLES)
    for name in names:
        (root / name).mkdir()
    return Layout(
        vault=root / "vault",
        out={role: root / role for role in ROLES},
        backup_config=root / "backup-config",
        documents=root / "documents",
        pgdata=root / "pgdata",
        owners={"vault": OWNER, **{role: OWNER for role in ROLES}},
    )


def test_what_init_env_makes_passes_the_import(wrapper_env: Path) -> None:
    values = parse_import(wrapper_env.read_text())
    assert set(values) >= {
        "master_key",
        "secret_key",
        "postgres_password",
        "owner_password",
        "app_password",
        "backup_db_password",
    }


def test_the_vault_equals_the_wrapper_env_and_the_data_is_this_installations(
    wrapper_env: Path, tmp_path: Path
) -> None:
    env_text = wrapper_env.read_text()
    values = parse_import(env_text)
    (tmp_path / "volumes").mkdir()
    place = layout(tmp_path / "volumes")
    # The wrapper's data exist: documents and an installation id.
    (place.documents / "page.bin").write_bytes(b"sealed")
    installation = values.get("installation_id", "ab" * 16)
    (place.backup_config / "installation-id").write_text(installation + "\n")
    run(place, {}, import_values={**values, "installation_id": installation})
    for name in ("master_key", "secret_key", "postgres_password", "owner_password", "app_password"):
        assert (place.vault / name).read_text().strip() == values[name]
    assert (place.backup_config / "installation-id").read_text().strip() == installation
    assert (place.vault / "installation_id").read_text().strip() == installation
    derived = crypto.derive_backup_password(bytes.fromhex(values["master_key"]))
    expected = values.get("backup_password", derived)
    assert (place.vault / "backup_password").read_text().strip() == expected
    # The keeper never gets the master key, the api never gets the owner password.
    assert not (place.out["keeper"] / "master_key").exists()
    assert not (place.out["api"] / "owner_password").exists()


def test_data_without_the_import_refuses_to_make_a_new_master_key(tmp_path: Path) -> None:
    (tmp_path / "volumes").mkdir()
    place = layout(tmp_path / "volumes")
    (place.documents / "page.bin").write_bytes(b"sealed")
    with pytest.raises(init_secrets.InitSecretsError) as caught:
        run(place, {})
    assert caught.value.code == "data_without_keys"
    assert not any(place.vault.iterdir())


def test_the_import_of_another_installation_refuses(wrapper_env: Path, tmp_path: Path) -> None:
    (tmp_path / "volumes").mkdir()
    place = layout(tmp_path / "volumes")
    (place.backup_config / "installation-id").write_text("ef" * 16 + "\n")
    values = parse_import(wrapper_env.read_text())
    with pytest.raises(init_secrets.InitSecretsError) as caught:
        run(place, {}, import_values={**values, "installation_id": "ab" * 16})
    assert caught.value.code == "installation_differs"
    assert not any(place.vault.iterdir())


def test_every_key_the_wrapper_needs_is_imported() -> None:
    assert set(init_secrets.IMPORT_KEYS.values()) == set(VAULT_NAMES)
