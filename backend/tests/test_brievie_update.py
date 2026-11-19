"""./brievie update, run against real git repositories and a fake docker in tmp_path.

The order of design 7.3 (check, fetch, stop, back up, switch, migrate, start, check
health, record), the refusals before anything changes, the way back after a failure, and
an update that stopped halfway. No test reaches the real docker or this checkout.
"""

import os
import shutil
import stat
import subprocess
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

    update_install.run("update", FAKE_PLAN="already")

    plan = next(c for c in update_install.commands() if "update_plan" in c)
    assert "--target 0.10.0" in plan
    assert "--installed 0.2.0" in plan


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
    assert "Bijwerken afgebroken." in result.stderr
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
