"""The compose restore: its order, its refusals, and the one place it removes files."""

import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from apps.backup import safepaths
from apps.backup.errors import BackupError
from apps.installer import init_secrets, restore_install
from apps.installer.restore_install import StepError, Steps, remove_staging, run


@pytest.fixture(autouse=True)
def _test_root(tmp_path: Path) -> Iterator[None]:
    """The only place a test may remove files (the suite installs the same in conftest)."""
    safepaths.allow_test_root(tmp_path)
    yield
    safepaths.clear_test_root()


RUN = "11111111-1111-4111-8111-111111111111"


class Log:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.said: list[str] = []
        self.complained: list[str] = []
        self.sessions = 0
        self.data = False
        self.fail_at = ""

    def step(self, name: str) -> None:
        self.calls.append(name)
        if self.fail_at == name:
            raise StepError(f"{name} mislukt.")

    def steps(self) -> Steps:
        def fetch(wanted: str | None) -> str:
            self.step(f"fetch:{wanted}")
            return RUN

        def verify() -> str:
            self.step("verify")
            return "Het herstel is gecontroleerd."

        return Steps(
            open_sessions=lambda: self.sessions,
            has_data=lambda: self.data,
            fetch=fetch,
            check=lambda found: self.step(f"check:{found}"),
            reset_database=lambda: self.step("reset"),
            load_database=lambda found: self.step(f"database:{found}"),
            load_files=lambda found: self.step(f"files:{found}"),
            adopt_id=lambda: self.step("adopt_id"),
            verify=verify,
            clean_staging=lambda: self.calls.append("clean"),
        )

    def go(self, *, run_id: str | None = None, replace_data: bool = False) -> int:
        return run(
            self.steps(),
            run_id=run_id,
            replace=replace_data,
            say=self.said.append,
            complain=self.complained.append,
        )


def test_the_steps_run_in_the_order_of_the_wrapper() -> None:
    log = Log()
    assert log.go() == 0
    assert log.calls == [
        "fetch:None",
        f"check:{RUN}",
        "reset",
        f"database:{RUN}",
        f"files:{RUN}",
        "adopt_id",
        "verify",
        "clean",
    ]
    assert log.said[-1].startswith("Herstel gelukt.")
    assert "docker compose up -d" in log.said[-1]


def test_a_chosen_run_is_the_one_fetched() -> None:
    log = Log()
    log.go(run_id="pre-update-0.5.0")
    assert log.calls[0] == "fetch:pre-update-0.5.0"


def test_running_services_refuse_before_anything_changes() -> None:
    log = Log()
    log.sessions = 2
    assert log.go(replace_data=True) == 1
    assert log.calls == []
    assert "docker compose stop api worker keeper caddy" in log.complained[0]
    assert log.complained[0].endswith("Er is niets veranderd.")


def test_data_is_replaced_only_when_asked() -> None:
    log = Log()
    log.data = True
    assert log.go() == 1
    assert log.calls == []
    assert "--replace" in log.complained[0]
    assert log.go(replace_data=True) == 0


@pytest.mark.parametrize(
    ("failing", "later"),
    [
        ("fetch:None", ["check", "reset", "database", "files"]),
        (f"check:{RUN}", ["reset", "database", "files"]),
        ("reset", ["database", "files", "adopt_id"]),
        (f"database:{RUN}", ["files", "adopt_id", "verify"]),
        (f"files:{RUN}", ["adopt_id", "verify"]),
        ("verify", []),
    ],
)
def test_a_failing_step_stops_the_rest_and_cleans_up(failing: str, later: list[str]) -> None:
    log = Log()
    log.fail_at = failing
    assert log.go() == 1
    assert log.complained == [f"{failing} mislukt."]
    assert log.calls[-1] == "clean"
    for name in later:
        assert not any(call.startswith(name) for call in log.calls)
    assert log.said[-1] != "Herstel gelukt."


def test_nothing_is_reset_when_the_back_up_does_not_open() -> None:
    log = Log()
    log.fail_at = "fetch:None"
    log.go()
    assert "reset" not in log.calls


def test_no_sentence_holds_a_key_or_password() -> None:
    log = Log()
    log.go()
    text = " ".join(log.said + log.complained)
    assert "BRIEVIE_MASTER_KEY" not in text
    assert "postgres://" not in text


# --- the staging folder ---


def make_staging(tmp_path: Path) -> Path:
    staging = tmp_path / "staging"
    staging.mkdir()
    return staging


def test_the_staging_files_of_the_list_are_removed(tmp_path: Path) -> None:
    staging = make_staging(tmp_path)
    (staging / "env").write_text("BRIEVIE_MASTER_KEY=x\n")
    (staging / "manifest.json").write_text("{}")
    (staging / "other.txt").write_text("keep")
    assert remove_staging(staging) == 2
    assert sorted(entry.name for entry in staging.iterdir()) == ["other.txt"]


def test_a_missing_file_and_a_second_call_are_fine(tmp_path: Path) -> None:
    staging = make_staging(tmp_path)
    assert remove_staging(staging) == 0
    assert remove_staging(staging) == 0


def test_a_link_in_place_of_a_staging_file_is_removed_not_followed(tmp_path: Path) -> None:
    staging = make_staging(tmp_path)
    victim = tmp_path / "victim"
    victim.write_text("keep me")
    os.symlink(victim, staging / "env")
    assert remove_staging(staging) == 1
    assert victim.read_text() == "keep me"


def test_a_folder_that_is_not_the_staging_folder_is_refused(tmp_path: Path) -> None:
    outside = Path("/etc")
    with pytest.raises(BackupError) as caught:
        remove_staging(outside)
    assert caught.value.code == "unsafe_path"
    elsewhere = Path("/data/documents")
    with pytest.raises(BackupError):
        remove_staging(elsewhere)


def test_a_link_as_the_staging_folder_is_refused(tmp_path: Path) -> None:
    real = make_staging(tmp_path)
    (real / "env").write_text("x")
    link = tmp_path / "linked"
    os.symlink(real, link)
    with pytest.raises(BackupError):
        remove_staging(link)
    assert (real / "env").exists()


def test_the_default_staging_folder_is_the_one_the_restore_allows() -> None:
    assert restore_install.STAGING == safepaths.RESTORE_STAGING
    assert replace(Log().steps()) is not None


# --- the installation id ---


def test_the_vault_id_replaces_the_one_of_the_restored_data(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    config = tmp_path / "config"
    vault.mkdir()
    config.mkdir()
    (vault / "installation_id").write_text("ab" * 16 + "\n")
    (config / "installation-id").write_text("cd" * 16 + "\n")
    (config / "manifest.json").write_text("{}")
    owner = (os.getuid(), os.getgid())
    assert init_secrets.adopt_installation_id(vault, config, owner) == "ab" * 16
    assert (config / "installation-id").read_text().strip() == "ab" * 16
    assert (config / "manifest.json").read_text() == "{}"


def test_a_vault_without_an_id_changes_nothing(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    config = tmp_path / "config"
    vault.mkdir()
    config.mkdir()
    (config / "installation-id").write_text("cd" * 16 + "\n")
    with pytest.raises(init_secrets.InitSecretsError):
        init_secrets.adopt_installation_id(vault, config, (os.getuid(), os.getgid()))
    assert (config / "installation-id").read_text().strip() == "cd" * 16


def test_the_printed_way_back_is_the_documented_one() -> None:
    from apps.installer.prepare import way_back
    from apps.installer.restore_install import restore_commands
    from apps.updates.release import Version

    selfhost = (Path(__file__).resolve().parents[2] / "docs" / "SELFHOST.md").read_text()
    printed = way_back(Version.parse("0.5.0"))
    for line in restore_commands("pre-update-0.5.0"):
        assert line in printed
    for line in restore_commands("pre-update-<version>"):
        assert line in selfhost
    # The old, wrong form must not come back.
    assert "admin restore" not in printed
    assert "--profile admin" in printed
    assert "restore_install pre-update-0.5.0 --replace" in printed
    assert "docker compose stop api worker keeper caddy" in printed
