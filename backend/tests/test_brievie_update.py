"""./brievie update, run against real git repositories and a fake docker in tmp_path.

The order of design 7.3 (check, fetch, stop, back up, switch, migrate, start, check
health, record), the refusals before anything changes, the way back after a failure, and
an update that stopped halfway. No test reaches the real docker or this checkout.
"""

import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.wrapper_support import (
    BACKUP_PW,
    MASTER,
    OWNER_PW,
    POSTGRES_PW,
    REPO,
    RUN_ID,
    SAFETY_ID,
    UpdateInstall,
    env_text,
    order_of,
)

SHELLS = ["/bin/sh"] + ([dash] if (dash := shutil.which("dash")) else [])


@pytest.fixture(autouse=True)
def _database() -> None:
    """Overrides the suite's database fixture: these tests run only shell, git and a fake."""


@pytest.fixture(scope="session", autouse=True)
def _test_role_is_subject_to_rls() -> None:
    """Overrides the suite's session check: no database is used here."""


@pytest.fixture
def update_install(tmp_path: Path) -> UpdateInstall:
    return UpdateInstall(tmp_path)


def setting(install: UpdateInstall, key: str) -> list[str]:
    return [line for line in install.dotenv.read_text().splitlines() if line.startswith(key + "=")]


def state_unchanged(install: UpdateInstall, before: tuple[str, str]) -> None:
    assert install.tracked_state() == before
    assert not install.marker.exists()
    assert not install.has(" stop ")
    assert not install.has("build --file")


# --- 1. check: refused before anything changes -----------------------------------------------


@pytest.mark.parametrize(
    "version", ["latest", "0.3", "v0.3.0-rc.1", "0.3.0;rm -rf x", "../x", "0.3.0\nx", "-0.3.0"]
)
def test_update_refuses_a_malformed_version(update_install: UpdateInstall, version: str) -> None:
    before = update_install.tracked_state()
    tags_before = update_install.git("tag", "-l")

    result = update_install.run("update", version)

    assert result.returncode in (1, 2)
    assert update_install.calls() == []
    assert update_install.git("tag", "-l") == tags_before
    state_unchanged(update_install, before)


def test_update_with_two_versions_is_usage(update_install: UpdateInstall) -> None:
    result = update_install.run("update", "0.3.0", "0.4.0")
    assert result.returncode == 2
    assert update_install.calls() == []


def test_update_refuses_a_dirty_tree(update_install: UpdateInstall) -> None:
    (update_install.root / "deploy" / "Caddyfile").write_text("# changed\n")
    before = update_install.tracked_state()

    result = update_install.run("update")

    assert result.returncode == 1
    assert "lokale wijzigingen" in result.stderr
    assert "Er is niets veranderd." in result.stderr
    state_unchanged(update_install, before)


def test_update_refuses_local_commits(update_install: UpdateInstall) -> None:
    (update_install.root / "notes.txt").write_text("mine\n")
    update_install.git("add", "notes.txt")
    update_install.git("commit", "-q", "-m", "mine")
    before = update_install.tracked_state()

    result = update_install.run("update")

    assert result.returncode == 1
    assert "eigen commits" in result.stderr
    state_unchanged(update_install, before)


def test_update_from_before_versions_gives_the_manual_path(update_install: UpdateInstall) -> None:
    update_install.write_env(env_text(version=None))
    before = update_install.tracked_state()

    result = update_install.run("update")

    assert result.returncode == 1
    assert "git checkout v0.3.0" in result.stderr
    assert "./brievie start" in result.stderr
    assert not update_install.has("update_plan")
    state_unchanged(update_install, before)


def test_update_refuses_a_custom_image(update_install: UpdateInstall) -> None:
    update_install.write_env(env_text() + "BRIEVIE_IMAGE=mijn/brievie:1\n")
    before = update_install.tracked_state()

    result = update_install.run("update")

    assert result.returncode == 1
    assert "eigen image" in result.stderr
    state_unchanged(update_install, before)


def test_update_refuses_when_fetch_fails(update_install: UpdateInstall) -> None:
    update_install.origin.rename(update_install.tmp / "gone.git")
    before = update_install.tracked_state()

    result = update_install.run("update", "0.3.0")

    assert result.returncode == 1
    assert "opgehaald" in result.stderr
    assert not update_install.has("update_plan")
    state_unchanged(update_install, before)


def test_update_refuses_a_moved_tag(update_install: UpdateInstall) -> None:
    update_install.git("fetch", "-q", "--tags", "origin")  # the old v0.3.0 is known here
    update_install.release("0.3.0", minimum="0.2.0", tag=False)
    update_install.git("tag", "-f", "v0.3.0", cwd=update_install.source)
    update_install.publish()
    before = update_install.tracked_state()

    result = update_install.run("update", "0.3.0")

    assert result.returncode == 1
    assert "opgehaald" in result.stderr
    assert not update_install.has("update_plan")
    state_unchanged(update_install, before)


def test_update_refuses_when_brievie_does_not_run(update_install: UpdateInstall) -> None:
    before = update_install.tracked_state()
    result = update_install.run("update", FAKE_RUNNING="postgres")
    assert result.returncode == 1
    assert "./brievie start" in result.stderr
    state_unchanged(update_install, before)


def test_update_to_the_installed_version_says_so(update_install: UpdateInstall) -> None:
    result = update_install.run("update", "0.2.0", FAKE_PLAN="already")

    assert result.returncode == 0
    assert "Je hebt al versie 0.2.0." in result.stdout
    assert not update_install.has("build --file")
    assert not update_install.marker.exists()


def test_refused_jump_is_passed_on(update_install: UpdateInstall) -> None:
    sentence = "Van versie 0.2.0 kun je niet in één keer naar 0.3.0. Werk eerst bij naar 0.2.5"
    before = update_install.tracked_state()

    result = update_install.run("update", FAKE_PLAN=f"refuse:{sentence}")

    assert result.returncode == 1
    assert sentence in result.stderr
    assert "CommandError" not in result.stderr
    state_unchanged(update_install, before)


def test_update_without_version_takes_the_newest_tag(update_install: UpdateInstall) -> None:
    update_install.release("0.10.0", minimum="0.2.0")
    update_install.release("0.4.0", minimum="0.2.0")
    update_install.git("tag", "v0.11.0-rc.1", cwd=update_install.source)
    update_install.publish()

    update_install.run("update", FAKE_PLAN="already", FAKE_PLAN_TARGET="0.10.0")

    # The installed image chooses the newest valid release from the stream it gets.
    plan = next(c for c in update_install.calls() if "update_plan" in c["argv"])
    assert "--target" not in plan["argv"]
    assert "--installed 0.2.0" in " ".join(plan["argv"])
    assert "--- v0.10.0" in str(plan["stdin"])
    assert "--- v0.11.0-rc.1" not in str(plan["stdin"])


def test_release_stream_reaches_update_plan(update_install: UpdateInstall) -> None:
    update_install.run("update", "0.3.0", FAKE_PLAN="already")

    plan = next(c for c in update_install.calls() if "update_plan" in c["argv"])
    argv = plan["argv"]
    assert argv[:5] == ["run", "--rm", "-i", "--network", "none"]
    assert "--postgres-major 18" in " ".join(argv)
    stdin = str(plan["stdin"])
    assert "--- v0.3.0" in stdin
    assert '"min_upgrade_from": "0.2.0"' in stdin
    # The image of the installed version decides.
    assert "brievie/backend:0.2.0" in argv


def test_nothing_changes_before_stop(update_install: UpdateInstall) -> None:
    before = update_install.tracked_state()

    result = update_install.run("update", FAKE_FAIL="build --file=1")

    assert result.returncode == 1
    assert "Het bouwen van versie 0.3.0 is mislukt" in result.stderr
    assert update_install.tracked_state() == before
    assert not update_install.has(" stop ")
    assert not update_install.has(" up ")
    assert not update_install.marker.exists()


def test_update_without_a_terminal_needs_the_typed_word(update_install: UpdateInstall) -> None:
    before = update_install.tracked_state()

    result = update_install.run("update", stdin="BIJWERKEN\n", BRIEVIE_UPDATE_CONFIRM="<unset>")

    assert result.returncode == 1
    assert "BRIEVIE_UPDATE_CONFIRM=BIJWERKEN" in result.stderr
    state_unchanged(update_install, before)


def test_update_refuses_while_a_restore_is_halfway(update_install: UpdateInstall) -> None:
    (update_install.root / "deploy" / ".restore-in-progress").write_text(
        f"run={RUN_ID}\nsafety=\nstep=de database\ntime=19-11-2026 21:00\n"
    )
    result = update_install.run("update")
    assert result.returncode == 1
    assert "halverwege" in result.stderr
    assert not update_install.has("update_plan")


# --- 2-4. fetch, stop, back up -----------------------------------------------------------


def test_steps_1_to_4_run_in_order(update_install: UpdateInstall) -> None:
    result = update_install.run("update")

    assert result.returncode == 0, result.stderr
    order_of(
        update_install,
        "update_plan",
        "update_preflight",
        "build --file deploy/Dockerfile --tag brievie/backend:0.3.0 -",
        "stop api worker caddy",
        "store_env_copy",
        "backup_init --update-fallback",
        "update_backup --label pre-update-0.3.0",
        "stop keeper",
    )


def test_the_build_gets_the_tag_as_its_context(update_install: UpdateInstall) -> None:
    update_install.run("update")
    build = next(c for c in update_install.calls() if c["argv"][:1] == ["build"])
    assert int(str(build["stdin"])) > 1000  # git archive of v0.3.0 on stdin


def test_the_fallback_is_mounted_for_its_own_calls_only(update_install: UpdateInstall) -> None:
    update_install.run("update")
    for call in update_install.calls():
        joined = " ".join(call["argv"])
        if call["argv"][:1] != ["compose"]:
            continue
        expected = (
            "update-backup" if ("backup_init" in joined or "update_backup" in joined) else "<unset>"
        )
        assert call["backup_dir_env"] == expected, joined
    backup = next(c for c in update_install.commands() if "update_backup" in c)
    assert "--user 10001" in backup


def test_with_a_destination_the_keeper_makes_a_labelled_safety_backup(
    update_install: UpdateInstall,
) -> None:
    update_install.write_env(env_text(destination=True))

    result = update_install.run("update")

    assert result.returncode == 0, result.stderr
    order_of(
        update_install,
        "stop api worker caddy",
        "keeper python manage.py backup --safety --label pre-update-0.3.0",
        "stop keeper",
    )
    assert not update_install.has("update_backup")
    assert f"pre-update-0.3.0 (nummer {SAFETY_ID})" in result.stdout


def test_backup_failure_restarts_the_old_version(update_install: UpdateInstall) -> None:
    before_head = update_install.head()

    result = update_install.run("update", FAKE_FAIL="update_backup=1")

    assert result.returncode == 1
    assert "De back-up vóór het bijwerken is mislukt." in result.stderr
    assert "Brievie draait weer op versie 0.2.0" in result.stderr
    backup = update_install.index("update_backup")
    restart = update_install.index("up -d --wait --no-build", after=backup)
    assert update_install.calls()[restart]["image_env"] == "brievie/backend:0.2.0"
    assert update_install.calls()[restart]["backup_dir_env"] == "<unset>"
    assert update_install.head() == before_head
    assert setting(update_install, "BRIEVIE_VERSION") == ["BRIEVIE_VERSION=0.2.0"]
    assert not update_install.marker.exists()
    assert not (update_install.root / "deploy" / ".brievie-lock").exists()


@pytest.mark.parametrize("shell", SHELLS)
def test_ctrl_c_during_the_backup_restarts_the_old_version(
    update_install: UpdateInstall, shell: str
) -> None:
    before_head = update_install.head()

    result = update_install.run("update", shell=shell, FAKE_KILL_AT="update_backup")

    assert result.returncode == 1
    # The shell sees the signal and the step's failure in either order (under load the
    # failure first): both end in the same place, the old version running again.
    assert "Bijwerken afgebroken." in result.stderr or "mislukt" in result.stderr
    assert "Brievie draait weer op versie 0.2.0" in result.stderr
    assert update_install.index("up -d --wait --no-build") > update_install.index("update_backup")
    assert update_install.head() == before_head
    assert not update_install.marker.exists()


def test_env_copy_has_no_master_key_and_no_secret_in_argv(update_install: UpdateInstall) -> None:
    update_install.run("update")

    copy = update_install.stdin_of("store_env_copy")
    for key in ("BRIEVIE_MASTER_KEY", "POSTGRES_PASSWORD", "BRIEVIE_OWNER_PASSWORD"):
        assert key not in copy
    for secret in (MASTER, POSTGRES_PW, OWNER_PW):
        assert secret not in copy
    assert BACKUP_PW in copy  # the back-up password travels with the copy, as in B1
    update_install.assert_no_secret_in_argv()


def test_start_pins_the_checkout_version(update_install: UpdateInstall) -> None:
    update_install.write_env(env_text(version=None))

    result = update_install.run("start")

    assert result.returncode == 0, result.stderr
    assert setting(update_install, "BRIEVIE_VERSION") == ["BRIEVIE_VERSION=0.2.0"]
    assert stat.S_IMODE(update_install.dotenv.stat().st_mode) == 0o600
    images = {c["image_env"] for c in update_install.calls() if c["argv"][0] == "compose"}
    assert images == {"brievie/backend:0.2.0"}


def test_a_named_installation_tags_its_image_with_version_and_name(
    update_install: UpdateInstall,
) -> None:
    update_install.write_env(env_text() + "COMPOSE_PROJECT_NAME=thuis\n")
    update_install.run("start")
    images = {c["image_env"] for c in update_install.calls() if c["argv"][0] == "compose"}
    assert images == {"brievie/backend:0.2.0-thuis"}


# --- 5-6. switch, migrate, start, check, record ------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_full_update_runs_every_step_in_order(update_install: UpdateInstall, shell: str) -> None:
    result = update_install.run("update", shell=shell)

    assert result.returncode == 0, result.stderr
    order_of(
        update_install,
        "build --file",
        "stop api worker caddy",
        "update_backup",
        "stop keeper",
        "run --rm migrate",
        "up -d --wait --no-build",
        "check_health --expect-version 0.3.0",
        f"record_update --from 0.2.0 --to 0.3.0 --backup-run {RUN_ID}",
    )
    assert update_install.head() == update_install.tag_commit("v0.3.0")
    assert setting(update_install, "BRIEVIE_VERSION") == ["BRIEVIE_VERSION=0.3.0"]
    assert stat.S_IMODE(update_install.dotenv.stat().st_mode) == 0o600
    assert not update_install.marker.exists()
    assert "Bijgewerkt naar versie 0.3.0." in result.stdout
    assert "telt niet als back-up" in result.stdout
    migrate = update_install.calls()[update_install.index("run --rm migrate")]
    assert migrate["image_env"] == "brievie/backend:0.3.0"
    assert not (update_install.root / "deploy" / ".brievie-lock").exists()


def test_the_target_wrapper_finishes_the_update(update_install: UpdateInstall) -> None:
    newer = (
        (REPO / "brievie")
        .read_text()
        .replace(
            'echo "Bijgewerkt naar versie $finish_to."',
            'echo "NIEUW-BIJGEWERKT naar versie $finish_to."',
        )
    )
    update_install.release("0.4.0", minimum="0.2.0", wrapper=newer)
    update_install.publish()

    result = update_install.run("update", "0.4.0")

    assert result.returncode == 0, result.stderr
    assert "NIEUW-BIJGEWERKT naar versie 0.4.0." in result.stdout


def test_the_running_file_is_a_copy(update_install: UpdateInstall) -> None:
    update_install.run("update")
    running = update_install.root / "deploy" / ".update" / "brievie-running"
    assert running.read_text() == update_install.git("show", "v0.2.0:brievie") + "\n"
    assert stat.S_IMODE(running.stat().st_mode) == 0o700


def test_checkout_failure_restarts_the_old_version(update_install: UpdateInstall) -> None:
    # An untracked file where v0.3.0 has a file: the switch refuses to overwrite it.
    update_install.release("0.3.1", minimum="0.2.0")
    (update_install.source / "extra.txt").write_text("tracked in 0.4.0\n")
    update_install.git("add", "extra.txt", cwd=update_install.source)
    update_install.git("commit", "-q", "-m", "extra", cwd=update_install.source)
    update_install.release("0.4.0", minimum="0.2.0")
    update_install.publish()
    (update_install.root / "extra.txt").write_text("mine, untracked\n")
    before_head = update_install.head()

    result = update_install.run("update", "0.4.0")

    assert result.returncode == 1
    assert "Er is niets bijgewerkt" in result.stderr
    assert update_install.head() == before_head
    assert update_install.index("up -d --wait --no-build") > update_install.index("stop keeper")
    assert setting(update_install, "BRIEVIE_VERSION") == ["BRIEVIE_VERSION=0.2.0"]
    assert not update_install.marker.exists()
    assert (update_install.root / "extra.txt").read_text() == "mine, untracked\n"


def way_back_lines(stdout: str, ref: str = "v0.2.0") -> None:
    lines = stdout.splitlines()
    assert f"  git checkout {ref}" in lines
    assert "  ./brievie restore pre-update-0.3.0 --replace --skip-safety-backup" in lines
    assert "Of probeer het opnieuw: ./brievie update 0.3.0" in lines


def test_migrate_failure_prints_the_way_back(update_install: UpdateInstall) -> None:
    result = update_install.run("update", FAKE_FAIL="run --rm migrate=1")

    assert result.returncode == 3
    way_back_lines(result.stdout)
    migrate = update_install.index("run --rm migrate")
    assert not any("up -d --wait" in c for c in update_install.commands()[migrate:])
    assert update_install.index("stop api worker keeper caddy", after=migrate)
    assert update_install.marker.exists()
    assert not (update_install.root / "deploy" / ".brievie-lock").exists()


def test_health_failure_prints_the_way_back(update_install: UpdateInstall) -> None:
    result = update_install.run("update", FAKE_FAIL="check_health=1")

    assert result.returncode == 3
    way_back_lines(result.stdout)
    health = update_install.index("check_health")
    assert update_install.index("stop api worker keeper caddy", after=health)
    assert f"Het nummer van die back-up is {RUN_ID}." in result.stdout


def test_way_back_names_a_commit_when_no_tag_fits(update_install: UpdateInstall) -> None:
    # Installed from an untagged commit of origin/main whose pyproject says 0.2.0.
    update_install.release("0.2.0", tag=False)
    update_install.release("0.3.0", minimum="0.2.0", tag=False)
    update_install.git("tag", "-f", "v0.3.0", cwd=update_install.source)
    update_install.git(
        "push",
        "-q",
        "--force",
        str(update_install.origin),
        "main",
        "v0.3.0",
        cwd=update_install.source,
    )
    update_install.git("fetch", "-q", "--force", "--tags", "origin")
    untagged = update_install.git("rev-parse", "origin/main~1")
    update_install.git("checkout", "-q", untagged)

    result = update_install.run("update", "0.3.0", FAKE_FAIL="check_health=1")

    assert result.returncode == 3
    way_back_lines(result.stdout, ref=untagged[:12])


def test_record_failure_only_warns(update_install: UpdateInstall) -> None:
    result = update_install.run("update", FAKE_FAIL="record_update=1")

    assert result.returncode == 0, result.stderr
    assert "het vastleggen lukte niet" in result.stdout
    assert not update_install.marker.exists()


# --- an update that stopped halfway ------------------------------------------------------


def checked_out_marker(install: UpdateInstall) -> None:
    previous = install.head()
    install.git("fetch", "-q", "--tags", "origin")
    install.git("checkout", "-q", "v0.3.0")
    install.write_marker(
        **{
            "from": "0.2.0",
            "to": "0.3.0",
            "previous_ref": previous,
            "run_id": RUN_ID,
            "kind": "update",
            "step": "checked-out",
            "time": "19-11-2026 21:00",
        }
    )


def test_unfinished_update_is_resumed(update_install: UpdateInstall) -> None:
    checked_out_marker(update_install)

    result = update_install.run("update")

    assert result.returncode == 0, result.stderr
    assert "niet afgemaakt; die wordt nu afgemaakt" in result.stdout
    assert not update_install.has("build --file deploy/Dockerfile --tag brievie/backend:0.3.0 -")
    assert not update_install.has("update_backup")
    assert update_install.has("run --rm migrate")
    assert not update_install.marker.exists()


def test_unfinished_update_to_another_version_is_refused(update_install: UpdateInstall) -> None:
    checked_out_marker(update_install)
    result = update_install.run("update", "0.4.0")
    assert result.returncode == 1
    assert "niet afgemaakt" in result.stdout
    way_back_lines(result.stdout)
    assert not update_install.has("run --rm migrate")


def test_unfinished_update_before_checkout_restarts_first(update_install: UpdateInstall) -> None:
    update_install.write_marker(
        **{
            "from": "0.2.0",
            "to": "0.3.0",
            "previous_ref": update_install.head(),
            "run_id": RUN_ID,
            "kind": "update",
            "step": "backed-up",
            "time": "19-11-2026 21:00",
        }
    )

    result = update_install.run("update")

    assert result.returncode == 0, result.stderr
    restart = update_install.index("up -d --wait --no-build")
    assert restart < update_install.index("update_plan")
    assert update_install.head() == update_install.tag_commit("v0.3.0")


def test_start_refuses_while_an_update_is_unfinished(update_install: UpdateInstall) -> None:
    checked_out_marker(update_install)

    result = update_install.run("start")

    assert result.returncode == 1
    assert "niet afgemaakt" in result.stdout
    way_back_lines(result.stdout)
    assert not update_install.has(" up ")


def test_start_after_an_update_that_never_switched_starts_the_old_version(
    update_install: UpdateInstall,
) -> None:
    update_install.write_marker(
        **{"from": "0.2.0", "to": "0.3.0", "previous_ref": update_install.head(), "step": "stopped"}
    )
    result = update_install.run("start")
    assert result.returncode == 0, result.stderr
    assert "afgebroken voordat er iets veranderde" in result.stdout
    assert not update_install.marker.exists()


def test_finish_interface_reads_only_the_marker(update_install: UpdateInstall) -> None:
    result = update_install.run("_update-finish")
    assert result.returncode == 2
    assert not update_install.has("migrate")

    checked_out_marker(update_install)
    result = update_install.run("_update-finish")
    assert result.returncode == 0, result.stderr
    assert update_install.has("run --rm migrate")


def test_a_copy_outside_its_folder_is_not_trusted(update_install: UpdateInstall) -> None:
    # BRIEVIE_ROOT is honoured only for the copy that lies inside that folder.
    elsewhere = update_install.tmp / "elsewhere" / "deploy" / ".update"
    elsewhere.mkdir(parents=True)
    copy = elsewhere / "brievie-running"
    copy.write_text((update_install.root / "brievie").read_text())
    env = {**update_install.env, "BRIEVIE_ROOT": str(update_install.root)}
    done = subprocess.run(  # noqa: S603
        ["/bin/sh", str(copy), "status"],
        cwd=update_install.tmp,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert done.returncode == 1
    assert "docker-compose.yml" in done.stderr
    assert update_install.calls() == []
    assert os.environ.get("BRIEVIE_ROOT") is None


def test_ctrl_c_during_the_migration_prints_the_way_back(update_install: UpdateInstall) -> None:
    result = update_install.run("update", FAKE_KILL_AT="run --rm migrate")

    assert result.returncode == 3
    # Printed by the exit trap (stderr) or, when the shell sees the failed step before
    # the signal, by the failure path (stdout): the way back either way.
    way_back_lines(result.stdout + result.stderr)
    assert update_install.marker.exists()
    assert not (update_install.root / "deploy" / ".brievie-lock").exists()


# --- the way back: a restore by name ------------------------------------------------------


def restore_env(**extra: str) -> dict[str, str]:
    return {
        "BRIEVIE_RESTORE_MASTER_KEY": MASTER,
        "BRIEVIE_RESTORE_CONFIRM": "WISSEN",
        **extra,
    }


def failed_update(install: UpdateInstall) -> None:
    result = install.run("update", FAKE_FAIL="check_health=1")
    assert result.returncode == 3
    assert install.marker.exists()


def test_the_way_back_restores_the_fallback_by_name(update_install: UpdateInstall) -> None:
    failed_update(update_install)
    update_install.git("checkout", "-q", "v0.2.0")
    # The env copy in the back-up carries no version: the restore pins the checkout's.
    update_install.restored.write_text(env_text(version=None))
    first = len(update_install.calls())

    result = update_install.run(
        "restore",
        "pre-update-0.3.0",
        "--replace",
        "--skip-safety-backup",
        "--keep-hostname",
        **restore_env(),
    )

    assert result.returncode == 0, result.stderr
    fetch = next(c for c in update_install.calls()[first:] if "restore_fetch" in c["argv"])
    argv = fetch["argv"]
    assert "brievie_update-backup:/backup:ro" in argv
    assert argv[argv.index("--run") + 1] == "pre-update-0.3.0"
    assert "brievie/backend:0.2.0" in argv
    assert setting(update_install, "BRIEVIE_VERSION") == ["BRIEVIE_VERSION=0.2.0"]
    assert not update_install.marker.exists()
    assert "versie 0.2.0" in result.stdout
    update_install.assert_no_secret_in_argv()


def test_a_restore_by_name_needs_the_old_checkout_first(update_install: UpdateInstall) -> None:
    failed_update(update_install)  # HEAD is still v0.3.0
    first = len(update_install.calls())

    result = update_install.run(
        "restore", "pre-update-0.3.0", "--replace", "--skip-safety-backup", **restore_env()
    )

    assert result.returncode == 1
    way_back_lines(result.stdout)
    assert not any("restore_fetch" in c["argv"] for c in update_install.calls()[first:])
    assert update_install.marker.exists()


def test_a_restore_of_another_backup_waits_for_the_update(update_install: UpdateInstall) -> None:
    failed_update(update_install)
    update_install.git("checkout", "-q", "v0.2.0")
    first = len(update_install.calls())

    result = update_install.run(
        "restore", "0b6c3f7e-0000-4c5b-9e8f-7a6b5c4d3e2f", "--replace", **restore_env()
    )

    assert result.returncode == 1
    assert "niet afgemaakt" in result.stdout
    assert not any("restore_fetch" in c["argv"] for c in update_install.calls()[first:])


@pytest.mark.parametrize("name", ["pre-update-0.3", "pre-update-latest", "pre-update-0.3.0;x"])
def test_a_malformed_backup_name_is_usage(update_install: UpdateInstall, name: str) -> None:
    result = update_install.run("restore", name, "--replace", **restore_env())
    assert result.returncode == 2
    assert update_install.calls() == []


def test_status_shows_the_last_update(update_install: UpdateInstall) -> None:
    result = update_install.run("status")

    assert result.returncode == 0, result.stderr
    order_of(update_install, "backup_status", "update_status")
    assert "Laatst bijgewerkt: 0.2.0 → 0.3.0" in result.stdout


# --- Docker itself: attestations and a step that hangs --------------------------------------


@pytest.mark.parametrize("command", [("start",), ("update",)])
def test_local_builds_get_no_default_attestation(
    update_install: UpdateInstall, command: tuple[str, ...]
) -> None:
    # A provenance attestation holds the build time: with the containerd image store
    # every build would get a new image id and compose would recreate every service.
    result = update_install.run(*command)

    assert result.returncode == 0, result.stderr
    assert {c["attest_env"] for c in update_install.calls()} == {"1"}


def test_a_slow_start_gets_one_honest_line_and_is_not_stopped(
    update_install: UpdateInstall,
) -> None:
    result = update_install.run(
        "start", FAKE_SLEEP_AT="up -d --build", FAKE_SLEEP_SECONDS="3", BRIEVIE_SLOW_SECONDS="1"
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr.count("Docker is al 1 seconden bezig") == 1
    assert "Ctrl-C" in result.stderr
    # The hanging call ran to its end; nothing was killed.
    assert update_install.has("<done> compose")


def test_a_slow_build_in_an_update_happens_before_anything_stops(
    update_install: UpdateInstall,
) -> None:
    result = update_install.run(
        "update", FAKE_SLEEP_AT="build --file", FAKE_SLEEP_SECONDS="3", BRIEVIE_SLOW_SECONDS="1"
    )

    assert result.returncode == 0, result.stderr
    assert "Brievie draait gewoon door" in result.stderr
    assert update_install.index("<done> build") < update_install.index("stop api worker caddy")


def test_a_quick_step_says_nothing_and_leaves_no_waiting_behind(
    update_install: UpdateInstall,
) -> None:
    started = time.monotonic()
    result = update_install.run("start", BRIEVIE_SLOW_SECONDS="60")

    assert result.returncode == 0, result.stderr
    assert "Docker is al" not in result.stderr
    # The watchdog's sleep holds no pipe of ./brievie: the run ends when the work does.
    assert time.monotonic() - started < 45


# --- review: a failed update is never "nothing changed" --------------------------------------


def failed_after_migrate(install: UpdateInstall) -> str:
    """A finish that migrated and then failed its health check; returns the old HEAD."""
    previous = install.head()
    result = install.run("update", FAKE_FAIL="check_health=1")
    assert result.returncode == 3
    assert install.marker.exists()
    return previous


@pytest.mark.parametrize("command", ["start", "update"])
def test_a_failed_migration_with_the_checkout_put_back_still_refuses(
    update_install: UpdateInstall, command: str
) -> None:
    # The user typed only the first line of the way back (git checkout), then start or
    # update: the database is the new version's, so nothing may start the old code.
    previous = failed_after_migrate(update_install)
    update_install.git("checkout", "-q", previous)
    first = len(update_install.calls())

    result = update_install.run(command)

    assert result.returncode == 1
    way_back_lines(result.stdout)
    assert update_install.marker.exists()
    assert setting(update_install, "BRIEVIE_VERSION") == ["BRIEVIE_VERSION=0.3.0"]
    later = [" ".join(c["argv"]) for c in update_install.calls()[first:]]
    assert not any(" up " in c or "update_plan" in c for c in later)


def test_status_during_an_unfinished_update_names_the_way_back(
    update_install: UpdateInstall,
) -> None:
    failed_after_migrate(update_install)
    first = len(update_install.calls())

    result = update_install.run("status")

    assert result.returncode == 1
    way_back_lines(result.stdout)
    assert not any("backup_status" in c["argv"] for c in update_install.calls()[first:])


def stopped_marker(install: UpdateInstall, step: str = "stopped") -> None:
    install.write_marker(
        **{
            "from": "0.2.0",
            "to": "0.3.0",
            "previous_ref": install.head(),
            "run_id": RUN_ID,
            "kind": "update",
            "step": step,
            "time": "19-11-2026 21:00",
        }
    )


def test_status_after_an_untouched_interruption_says_start(update_install: UpdateInstall) -> None:
    stopped_marker(update_install)
    result = update_install.run("status")
    assert result.returncode == 1
    assert "./brievie start" in result.stdout
    assert "git checkout" not in result.stdout


def test_a_dirty_tree_with_the_marker_at_stopped_is_not_untouched(
    update_install: UpdateInstall,
) -> None:
    # A power cut during `git checkout` can leave HEAD old and the files half new.
    stopped_marker(update_install, "backed-up")
    (update_install.root / "deploy" / "Caddyfile").write_text("# half switched\n")

    result = update_install.run("start")

    assert result.returncode == 1
    assert "niet afgemaakt" in result.stdout
    assert "git checkout --force v0.2.0" in result.stdout
    assert not update_install.has(" up ")
    assert update_install.marker.exists()


def test_a_marker_at_migrating_with_the_old_head_is_not_untouched(
    update_install: UpdateInstall,
) -> None:
    stopped_marker(update_install, "migrating")
    result = update_install.run("start")
    assert result.returncode == 1
    way_back_lines(result.stdout)
    assert update_install.marker.exists()


def test_the_finish_marks_the_migration_before_it_runs(update_install: UpdateInstall) -> None:
    result = update_install.run("update", FAKE_FAIL="run --rm migrate=1")
    assert result.returncode == 3
    assert "step=migrating" in update_install.marker.read_text()


def install_post_checkout_interrupt(install: UpdateInstall) -> None:
    """A git hook that stops ./brievie (SIGTERM to git's parent) right after a successful
    checkout: the shell runs its trap as soon as git returns, before the hand-over."""
    hook = install.root / ".git" / "hooks" / "post-checkout"
    hook.write_text(
        f"#!{sys.executable}\nimport os, signal, subprocess\n"
        "git = os.getppid()\n"
        "out = subprocess.run(['/bin/ps', '-o', 'ppid=', '-p', str(git)],\n"
        "                     capture_output=True, text=True, check=True).stdout\n"
        "os.kill(int(out), signal.SIGTERM)\n"
    )
    hook.chmod(0o755)


def test_ctrl_c_right_after_the_checkout_keeps_the_update_resumable(
    update_install: UpdateInstall,
) -> None:
    install_post_checkout_interrupt(update_install)

    result = update_install.run("update")

    assert result.returncode == 3
    assert update_install.head() == update_install.tag_commit("v0.3.0")
    keeper = update_install.index("stop keeper")
    assert not any("up -d" in c for c in update_install.commands()[keeper:])
    assert update_install.marker.exists()
    assert "Er is niets bijgewerkt" not in result.stderr
    assert "./brievie update 0.3.0" in result.stdout + result.stderr

    (update_install.root / ".git" / "hooks" / "post-checkout").unlink()
    resumed = update_install.run("update")
    assert resumed.returncode == 0, resumed.stderr
    assert not update_install.marker.exists()


@pytest.mark.parametrize("shell", SHELLS)
def test_a_hangup_during_the_backup_restarts_the_old_version_into_the_log(
    update_install: UpdateInstall, shell: str
) -> None:
    result = update_install.run("update", shell=shell, FAKE_HUP_AT="update_backup")

    assert result.returncode != 0
    assert update_install.index("up -d --wait --no-build") > update_install.index("update_backup")
    assert not update_install.marker.exists()
    assert not (update_install.root / "deploy" / ".brievie-lock").exists()
    log = (update_install.root / "deploy" / ".brievie-last.log").read_text()
    assert "Brievie draait weer op versie 0.2.0" in log


def test_the_finish_checks_health_before_caddy_opens_the_door(
    update_install: UpdateInstall,
) -> None:
    result = update_install.run("update")

    assert result.returncode == 0, result.stderr
    migrate = update_install.index("run --rm migrate")
    order_of(
        update_install,
        "run --rm migrate",
        "up -d --wait --no-build api worker keeper",
        "check_health",
        "up -d --wait --no-build caddy",
    )
    assert not any(
        c.endswith("up -d --wait --no-build") for c in update_install.commands()[migrate:]
    )


def known_migrations(install: UpdateInstall) -> None:
    folder = install.root / "backend" / "apps" / "updates" / "migrations"
    folder.mkdir(parents=True)
    (folder / "0001_initial.py").write_text("")


def test_start_refuses_code_older_than_the_database(update_install: UpdateInstall) -> None:
    known_migrations(update_install)
    result = update_install.run(
        "start", FAKE_APPLIED_MIGRATIONS="updates|0001_initial\nupdates|0099_from_the_future\n"
    )

    assert result.returncode == 1
    assert "nieuwere versie" in result.stderr
    assert "updates 0099_from_the_future" in result.stderr
    assert not update_install.has("up -d --build")


def test_the_schema_check_starts_postgres_first(update_install: UpdateInstall) -> None:
    known_migrations(update_install)
    result = update_install.run(
        "start",
        FAKE_RUNNING="",
        FAKE_APPLIED_MIGRATIONS="updates|0099_from_the_future\n",
    )
    assert result.returncode == 1
    assert "nieuwere versie" in result.stderr
    check = next(
        i
        for i, c in enumerate(update_install.calls())
        if "django_migrations" in str(c.get("stdin", ""))
    )
    assert update_install.index("up -d --wait postgres") < check


def test_a_schema_that_cannot_be_read_refuses(update_install: UpdateInstall) -> None:
    known_migrations(update_install)
    result = update_install.run("start", FAKE_FAIL="psql -U postgres -d brievie -q -t -A=2")
    assert result.returncode == 1
    assert "kon niet worden gecontroleerd" in result.stderr
    assert not update_install.has("up -d --build")


def test_start_accepts_a_database_the_code_knows(update_install: UpdateInstall) -> None:
    known_migrations(update_install)
    result = update_install.run(
        "start", FAKE_APPLIED_MIGRATIONS="updates|0001_initial\nauth|0012_whatever\n"
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("command", [("backup",), ("sweep", "--delete"), ("recovery-sheet",)])
def test_commands_that_change_things_wait_for_an_unfinished_update(
    update_install: UpdateInstall, command: tuple[str, ...]
) -> None:
    checked_out_marker(update_install)
    result = update_install.run(*command)
    assert result.returncode == 1
    assert "niet afgemaakt" in result.stdout
    assert not any("manage.py" in c for c in update_install.commands())


def test_update_without_a_version_lets_the_image_choose_and_says_what_it_skipped(
    update_install: UpdateInstall,
) -> None:
    result = update_install.run(
        "update",
        FAKE_PLAN="already",
        FAKE_PLAN_TARGET="0.2.0",
        FAKE_PLAN_STDERR="Versie 0.4.0 is overgeslagen: deploy/release.json klopt niet.\n",
    )

    assert result.returncode == 0, result.stderr
    plan = next(c for c in update_install.commands() if "update_plan" in c)
    assert "--target" not in plan
    assert "Versie 0.4.0 is overgeslagen" in result.stderr
    assert "Je hebt al versie 0.2.0." in result.stdout


# --- the lock -------------------------------------------------------------------------------


def boot_id() -> str:
    linux = Path("/proc/sys/kernel/random/boot_id")
    if linux.exists():
        return linux.read_text().strip()
    return subprocess.run(
        ["/usr/sbin/sysctl", "-n", "kern.boottime"], capture_output=True, text=True, check=True
    ).stdout.strip()


def write_lock(install: UpdateInstall, *, pid: int | None, boot: str | None) -> None:
    lock = install.root / "deploy" / ".brievie-lock"
    lock.mkdir()
    (lock / "started").write_text(f"{int(time.time())}\n")
    if pid is not None:
        (lock / "pid").write_text(f"{pid}\n")
    if boot is not None:
        (lock / "boot").write_text(boot + "\n")


def gone_pid() -> int:
    done = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
    done.wait()
    return done.pid


@pytest.mark.parametrize("shell", SHELLS)
def test_a_lock_of_a_process_that_is_gone_is_taken_over(
    update_install: UpdateInstall, shell: str
) -> None:
    write_lock(update_install, pid=gone_pid(), boot=boot_id())
    result = update_install.run("status", shell=shell)
    assert result.returncode == 0, result.stderr
    assert "niet meer draait" in result.stderr
    assert not (update_install.root / "deploy" / ".brievie-lock").exists()


@pytest.mark.parametrize("shell", SHELLS)
def test_a_lock_of_a_running_process_is_respected(
    update_install: UpdateInstall, shell: str
) -> None:
    write_lock(update_install, pid=os.getpid(), boot=boot_id())
    result = update_install.run("status", shell=shell)
    assert result.returncode == 1
    assert "al een ./brievie bezig" in result.stderr
    assert (update_install.root / "deploy" / ".brievie-lock").exists()


PROC_BOOT_ID = Path("/proc/sys/kernel/random/boot_id").exists()


@pytest.mark.parametrize("shell", SHELLS)
def test_another_boot_counts_only_with_linux_boot_id(
    update_install: UpdateInstall, shell: str
) -> None:
    # Linux's boot id is fixed per boot: another one means the machine restarted and the
    # process id may belong to another program now. Elsewhere (macOS: kern.boottime moves
    # when the clock is stepped after sleep) a live process always holds its lock.
    write_lock(update_install, pid=os.getpid(), boot="an earlier boot")
    result = update_install.run("status", shell=shell)
    if PROC_BOOT_ID:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode == 1
        assert "al een ./brievie bezig" in result.stderr
        assert (update_install.root / "deploy" / ".brievie-lock").exists()


def test_without_linux_boot_id_no_boot_is_recorded(update_install: UpdateInstall) -> None:
    result = update_install.run(
        "status", FAKE_SLEEP_AT="backup_status", FAKE_SLEEP_SECONDS="0", FAKE_COPY_LOCK="1"
    )
    assert result.returncode == 0, result.stderr
    copied = update_install.tmp / "lock-copy"
    assert (copied / "pid").read_text().strip().isdigit()
    assert (copied / "boot").exists() == PROC_BOOT_ID


def test_two_wrappers_never_both_take_over_a_stale_lock(update_install: UpdateInstall) -> None:
    # Another ./brievie judged the same lock stale a moment earlier and made its own: by
    # the time this one moves the lock aside, it holds a live process id. It must put that
    # lock back and wait, not remove it.
    write_lock(update_install, pid=gone_pid(), boot=None)
    fake_mv = update_install.bin / "mv"
    fake_mv.write_text(
        "#!/bin/sh\n"
        "case $1 in\n"
        '*/.brievie-lock) printf "%s\\n" "$FAKE_LIVE_PID" >"$1/pid" ;;\n'
        "esac\n"
        'exec /bin/mv "$@"\n'
    )
    fake_mv.chmod(0o755)

    result = update_install.run("status", FAKE_LIVE_PID=str(os.getpid()))

    assert result.returncode == 1
    assert "al een ./brievie bezig" in result.stderr
    lock = update_install.root / "deploy" / ".brievie-lock"
    assert (lock / "pid").read_text().strip() == str(os.getpid())
    assert not list((update_install.root / "deploy").glob(".brievie-lock.stale.*"))


def test_a_lock_without_a_pid_keeps_the_twelve_hour_rule(update_install: UpdateInstall) -> None:
    write_lock(update_install, pid=None, boot=None)
    result = update_install.run("status")
    assert result.returncode == 1
    assert "al een ./brievie bezig" in result.stderr


def test_the_watchdog_leaves_no_sleep_behind(update_install: UpdateInstall) -> None:
    result = update_install.run("start", BRIEVIE_SLOW_SECONDS="4171")
    assert result.returncode == 0, result.stderr
    time.sleep(1.5)
    processes = subprocess.run(
        ["/bin/ps", "-A", "-o", "args="], capture_output=True, text=True, check=True
    ).stdout
    # Neither the watchdog's subshell (it shows as ./brievie itself) nor a long sleep.
    assert str(update_install.root / "brievie") not in processes
    assert "sleep 4171" not in processes


def test_the_way_back_without_skip_safety_names_the_real_command(
    update_install: UpdateInstall,
) -> None:
    failed_update(update_install)
    update_install.git("checkout", "-q", "v0.2.0")
    first = len(update_install.calls())

    result = update_install.run("restore", "pre-update-0.3.0", "--replace", **restore_env())

    assert result.returncode == 1
    assert (
        "./brievie restore pre-update-0.3.0 --replace --skip-safety-backup"
        in result.stdout + result.stderr
    )
    assert not any("restore_fetch" in c["argv"] for c in update_install.calls()[first:])
