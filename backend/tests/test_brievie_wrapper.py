"""./brievie: the shell wrapper a home user types, run against a fake `docker`.

Every test copies the wrapper and deploy/ into tmp_path and runs it there, with a fake
`docker` first on PATH and HOME inside tmp_path. No test reaches the real docker, and
no test runs the wrapper from the repository.
"""

import json
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

import pytest

from apps.backup.envcopy import _leaks

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "brievie"
DASH = shutil.which("dash")
SHELLS = ["/bin/sh"] + ([DASH] if DASH else [])

MASTER = "5a" * 32
TYPED_MASTER = "5A5A 5A5A-" * 8 + "\n"  # spaces, dashes and capitals, as read off paper
POSTGRES_PW = "a1" * 24
OWNER_PW = "b2" * 24
APP_PW = "c3" * 24
BACKUP_DB_PW = "d4" * 24
DERIVED_PW = "e5" * 32
SEPARATE_PW = "f6" * 32
TYPED_SEPARATE_PW = "my own back-up words 7"
NEW_POSTGRES_PW = "17" * 24
NEW_OWNER_PW = "28" * 24
S3_SECRET = "s3-secret-value-39"
RUN_ID = "0b6c3f7e-1d2a-4c5b-9e8f-7a6b5c4d3e2f"
OWNERSHIP_CALL = (
    "ps -a --filter label=com.docker.compose.project=brievie "
    '--format owner={{.Label "com.docker.compose.project.working_dir"}}'
)
CURRENT_ID = "c0" * 16
BACKUP_ID = "b0" * 16
NEW_ID = "a7" * 16
SECRETS = [
    MASTER,
    POSTGRES_PW,
    OWNER_PW,
    APP_PW,
    BACKUP_DB_PW,
    DERIVED_PW,
    SEPARATE_PW,
    TYPED_SEPARATE_PW,
    NEW_POSTGRES_PW,
    NEW_OWNER_PW,
    S3_SECRET,
]


class Call(TypedDict, total=False):
    argv: list[str]
    stdin: str
    env: dict[str, str]
    secret_env: list[str]
    watch_mode: str
    image_env: str
    backup_dir_env: str


# The fake: logs each call as a JSON line, answers from FAKE_* variables, and writes the
# restored files of `restore_fetch` into the folder mounted at /out. It reads stdin only
# for the calls that pipe something in (store_env_copy, psql), never the user's answers.
FAKE_DOCKER = """#!{python}
import json, os, sys
from pathlib import Path

argv = sys.argv[1:]
joined = " ".join(argv)
entry = {{"argv": argv}}
piped = ("store_env_copy" in argv or "psql" in argv
         or "cat > /data/backup-config/installation-id" in joined)
if piped:
    entry["stdin"] = sys.stdin.read()
secret_names = ("BRIEVIE_MASTER_KEY", "BRIEVIE_BACKUP_PASSWORD", "BRIEVIE_RESTORE_MASTER_KEY",
                "BRIEVIE_RESTORE_BACKUP_PASSWORD", "BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY")
if argv[:1] == ["compose"]:
    entry["secret_env"] = [name for name in secret_names if os.environ.get(name)]
    entry["image_env"] = os.environ.get("BRIEVIE_IMAGE", "")
    entry["backup_dir_env"] = os.environ.get("BRIEVIE_BACKUP_DIR", "<unset>")
if "restore_fetch" in argv:
    names = ("BRIEVIE_MASTER_KEY", "BRIEVIE_BACKUP_PASSWORD", "BRIEVIE_BACKUP_DIR_SET",
             "BRIEVIE_BACKUP_REPOSITORY", "BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY", "SECRET_KEY")
    entry["env"] = {{name: os.environ[name] for name in names if name in os.environ}}
watch = os.environ.get("FAKE_WATCH", "")
if watch and os.path.exists(watch):
    entry["watch_mode"] = oct(os.stat(watch).st_mode & 0o777)
with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
    log.write(json.dumps(entry) + "\\n")

remove_at = os.environ.get("FAKE_REMOVE_AT", "")
if remove_at and remove_at in joined:
    os.remove(os.environ["FAKE_REMOVE_PATH"])
fail = os.environ.get("FAKE_DOCKER_FAIL", "")
if fail and fail in joined:
    sys.exit(int(os.environ.get("FAKE_DOCKER_FAIL_CODE", "1")))

running = os.environ.get("FAKE_DOCKER_RUNNING", "").split()
if argv[:2] == ["ps", "-a"]:
    template = argv[argv.index("--format") + 1]
    label = '{{{{.Label "com.docker.compose.project.working_dir"}}}}'
    owners = [o for o in os.environ.get("FAKE_DOCKER_OWNERS", "").split("|") if o]
    for owner in owners:
        sys.stdout.write(template.replace(label, "" if owner == "<empty>" else owner) + "\\n")
    sys.exit(0)
if argv[:1] == ["compose"] and "ps" in argv:
    if "--services" in argv:
        sys.stdout.write("".join(name + "\\n" for name in running))
    elif "--quiet" in argv and argv[-1] in running:
        sys.stdout.write("0123abcd\\n")
    sys.exit(0)
if argv[:2] == ["volume", "inspect"]:
    sys.exit(0 if argv[2] in os.environ.get("FAKE_DOCKER_VOLUMES", "").split() else 1)
if "derive_backup_password" in argv:
    sys.stdout.write(os.environ["FAKE_DERIVED_PASSWORD"] + "\\n")
elif "token_hex(16)" in joined:
    sys.stdout.write(os.environ.get("FAKE_INSTALLATION_ID", "{new_id}") + "\\n")
elif "cat /data/backup-config/installation-id" in joined:
    sys.stdout.write(os.environ.get("FAKE_VOLUME_ID", ""))
elif "-c" in argv and "secrets" in joined:
    sys.stdout.write(os.environ["FAKE_SEPARATE_PASSWORD"] + "\\n")
elif "recovery_sheet" in argv:
    sys.stdout.write("<html>herstelblad</html>\\n")
elif "restore_fetch" in argv:
    out = next(argv[i + 1].rsplit(":/out", 1)[0] for i, a in enumerate(argv)
               if a == "-v" and argv[i + 1].endswith(":/out"))
    Path(out, "env").write_text(Path(os.environ["FAKE_RESTORED_ENV"]).read_text())
    Path(out, "manifest.json").write_text("{{}}")
    run_id = os.environ.get("FAKE_RUN_ID", "{run_id}")
    sys.stdout.write("run_id=" + run_id + "\\ncreated_at=2026-11-10T03:00:00+00:00\\n"
                     "hostname=oud.local\\nbrievie_version=1.4.0\\n")
sys.exit(0)
"""


def current_env(
    *,
    backup_password: bool = True,
    project: str | None = None,
    image: str | None = None,
    installation_id: str | None = CURRENT_ID,
) -> str:
    lines = [
        "# A server's deploy/.env",
        f"POSTGRES_PASSWORD={POSTGRES_PW}",
        f"BRIEVIE_OWNER_PASSWORD={OWNER_PW}",
        f"BRIEVIE_APP_PASSWORD={APP_PW}",
        f"DATABASE_OWNER_URL=postgres://brievie_owner:{OWNER_PW}@localhost:5432/brievie",
        "SECRET_KEY=" + "k" * 60,
        "BRIEVIE_HOSTNAME=brievie.local",
        f"BRIEVIE_MASTER_KEY={MASTER}",
        f"BRIEVIE_BACKUP_DB_PASSWORD={BACKUP_DB_PW}",
        f"DATABASE_BACKUP_URL=postgres://brievie_backup:{BACKUP_DB_PW}@localhost:5432/brievie",
        "BRIEVIE_BACKUP_DIR=/Volumes/Backup/brievie",
    ]
    if backup_password:
        lines.append(f"BRIEVIE_BACKUP_PASSWORD={DERIVED_PW}")
    if installation_id:
        lines.append(f"BRIEVIE_INSTALLATION_ID={installation_id}")
    if project:
        lines.append(f"COMPOSE_PROJECT_NAME={project}")
    if image:
        lines.append(f"BRIEVIE_IMAGE={image}")
    return "\n".join(lines) + "\n"


def restored_env(*, project: str | None = None, installation_id: str | None = BACKUP_ID) -> str:
    lines = [
        "SECRET_KEY=" + "k" * 60,
        "BRIEVIE_HOSTNAME=oud.local",
        f"BRIEVIE_APP_PASSWORD={APP_PW}",
        f"BRIEVIE_BACKUP_DB_PASSWORD={BACKUP_DB_PW}",
        f"BRIEVIE_BACKUP_PASSWORD={DERIVED_PW}",
        "BRIEVIE_BACKUP_DIR=/Volumes/Backup/brievie",
    ]
    if installation_id:
        lines.append(f"BRIEVIE_INSTALLATION_ID={installation_id}")
    if project:
        lines.append(f"COMPOSE_PROJECT_NAME={project}")
    lines += [
        "# Added by the restore: the master key and new database passwords.",
        f"BRIEVIE_MASTER_KEY={MASTER}",
        f"POSTGRES_PASSWORD={NEW_POSTGRES_PW}",
        f"BRIEVIE_OWNER_PASSWORD={NEW_OWNER_PW}",
        f"DATABASE_OWNER_URL=postgres://brievie_owner:{NEW_OWNER_PW}@localhost:5432/brievie",
    ]
    return "\n".join(lines) + "\n"


def without(text: str, *keys: str) -> str:
    """`text` without the lines that set one of `keys`."""
    return "".join(
        line + "\n"
        for line in text.splitlines()
        if not line.startswith(tuple(k + "=" for k in keys))
    )


def setting(text: str, key: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith(key + "=")]


DESTINATION_KEYS = (
    "BRIEVIE_BACKUP_DIR",
    "BRIEVIE_BACKUP_REPOSITORY",
    "BRIEVIE_BACKUP_S3_ACCESS_KEY_ID",
    "BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY",
    "BRIEVIE_BACKUP_REST_USERNAME",
    "BRIEVIE_BACKUP_REST_PASSWORD",
)


class Install:
    """A copy of the wrapper and deploy/ in tmp_path, with a fake docker on PATH."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path.resolve()
        self.root = self.tmp / "brievie-install"
        self.root.mkdir()
        shutil.copy(SCRIPT, self.root / "brievie")
        shutil.copytree(
            REPO / "deploy",
            self.root / "deploy",
            ignore=shutil.ignore_patterns(".env", ".env.*", ".restore", "*.tmp*"),
        )
        shutil.copy(REPO / "deploy" / ".env.example", self.root / "deploy" / ".env.example")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        docker = self.bin / "docker"
        docker.write_text(FAKE_DOCKER.format(python=sys.executable, run_id=RUN_ID, new_id=NEW_ID))
        docker.chmod(0o755)
        self.log = self.tmp / "docker.log"
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.backup_folder = self.tmp / "backup-disk"
        self.backup_folder.mkdir()
        (self.backup_folder / "brievie-backup.json").write_text("{}")
        self.restored = self.tmp / "restored.env"
        self.restored.write_text(restored_env())
        self.env: dict[str, str] = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.home),
            "FAKE_DOCKER_LOG": str(self.log),
            "FAKE_DERIVED_PASSWORD": DERIVED_PW,
            "FAKE_SEPARATE_PASSWORD": SEPARATE_PW,
            "FAKE_RESTORED_ENV": str(self.restored),
            "FAKE_DOCKER_RUNNING": "postgres keeper api worker caddy",
        }

    @property
    def dotenv(self) -> Path:
        return self.root / "deploy" / ".env"

    def write_env(self, text: str) -> None:
        self.dotenv.write_text(text)
        self.dotenv.chmod(0o600)

    def run(
        self, *args: str, shell: str = "/bin/sh", stdin: str = "", **env: str
    ) -> subprocess.CompletedProcess[str]:
        script = self.root / "brievie"
        # The wrapper only ever runs as a copy inside the test's own directory.
        assert script.resolve().is_relative_to(self.tmp)
        assert shutil.which("docker", path=env.get("PATH", self.env["PATH"])) == str(
            self.bin / "docker"
        )
        return subprocess.run(  # noqa: S603
            [shell, str(script), *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            input=stdin,
            env={**self.env, **env},
            timeout=60,
            check=False,
        )

    def calls(self) -> list[Call]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def commands(self) -> list[str]:
        return [" ".join(call["argv"]) for call in self.calls()]

    def index(self, fragment: str) -> int:
        """The position of the first logged call that contains `fragment`."""
        for position, command in enumerate(self.commands()):
            if fragment in command:
                return position
        raise AssertionError(f"no call with {fragment!r} in {self.commands()}")

    def stdin_of(self, fragment: str) -> str:
        return next(c.get("stdin", "") for c in self.calls() if fragment in " ".join(c["argv"]))

    def assert_no_secret_in_argv(self) -> None:
        for call in self.calls():
            joined = " ".join(call["argv"])
            for secret in SECRETS:
                assert secret not in joined, f"a secret is in the argv of: {call['argv'][:4]}"


@pytest.fixture(autouse=True)
def _database() -> None:
    """Overrides the suite's database fixture: these tests run only shell and a fake."""


@pytest.fixture(scope="session", autouse=True)
def _test_role_is_subject_to_rls() -> None:
    """Overrides the suite's session check: no database is used here."""


@pytest.fixture
def install(tmp_path: Path) -> Install:
    return Install(tmp_path)


@pytest.fixture
def installed(install: Install) -> Install:
    install.write_env(current_env())
    return install


def restore_inputs(install: Install, **extra: str) -> dict[str, str]:
    return {
        "BRIEVIE_RESTORE_SOURCE": str(install.backup_folder),
        "BRIEVIE_RESTORE_MASTER_KEY": TYPED_MASTER,
        "BRIEVIE_RESTORE_CONFIRM": "WISSEN",
        **extra,
    }


# --- usage ------------------------------------------------------------------------------


def test_unknown_command_prints_usage_and_exits_2(installed: Install) -> None:
    result = installed.run("frobnicate")

    assert result.returncode == 2
    assert "./brievie backup" in result.stderr
    assert installed.calls() == []


@pytest.mark.parametrize("args", [(), ("help",)])
def test_help_prints_one_line_per_command(installed: Install, args: tuple[str, ...]) -> None:
    result = installed.run(*args)

    assert result.returncode == 0
    lines = result.stdout.splitlines()
    for command in ("start", "backup", "restore", "status", "sweep", "recovery-sheet", "help"):
        starting = [line for line in lines if line.strip().startswith(f"./brievie {command}")]
        assert starting, command
        assert all(len(line) <= 100 for line in starting)
    assert "halverwege" in result.stdout  # when --skip-safety-backup is meant for
    assert installed.calls() == []


def test_refuses_to_run_without_the_compose_file_next_to_it(installed: Install) -> None:
    (installed.root / "deploy" / "docker-compose.yml").unlink()

    result = installed.run("status")

    assert result.returncode == 1
    assert "docker-compose.yml" in result.stderr
    assert installed.calls() == []


def test_separate_password_needs_init(installed: Install) -> None:
    result = installed.run("backup", "--separate-password")

    assert result.returncode == 2
    assert installed.calls() == []


# --- bring-along ------------------------------------------------------------------------


def test_every_command_brings_env_and_role_along(install: Install) -> None:
    old = "\n".join(
        line
        for line in current_env().splitlines()
        if not line.startswith(("BRIEVIE_BACKUP_DB_PASSWORD", "DATABASE_BACKUP_URL"))
    )
    install.write_env(old + "\n")

    result = install.run("status")

    assert result.returncode == 0, result.stderr
    text = install.dotenv.read_text()
    assert "\nBRIEVIE_BACKUP_DB_PASSWORD=" in text
    assert "\nDATABASE_BACKUP_URL=" in text
    assert "backup-role.psql" in install.stdin_of("psql")
    assert install.index("psql") < install.index("backup_status")


def test_role_is_left_alone_when_postgres_does_not_run(installed: Install) -> None:
    result = installed.run("status", FAKE_DOCKER_RUNNING="keeper")

    assert result.returncode == 0, result.stderr
    assert not any("psql" in command for command in installed.commands())


# --- backup -----------------------------------------------------------------------------


def test_backup_without_a_running_keeper_says_so_and_runs_nothing(installed: Install) -> None:
    result = installed.run("backup", FAKE_DOCKER_RUNNING="postgres")

    assert result.returncode == 1
    assert "./brievie start" in result.stderr
    for call in installed.calls():
        assert not ("exec" in call["argv"] and "keeper" in call["argv"])
        assert "run" not in call["argv"]


def test_status_without_a_running_keeper_says_so(installed: Install) -> None:
    result = installed.run("status", FAKE_DOCKER_RUNNING="postgres")

    assert result.returncode == 1
    assert "./brievie start" in result.stderr


@pytest.mark.parametrize("shell", SHELLS)
def test_backup_never_gives_the_keeper_the_master_key(install: Install, shell: str) -> None:
    bom = "﻿"
    install.write_env(
        current_env()
        + "\n".join(
            [
                "# POSTGRES_PASSWORD=old-commented-value",
                f"export BRIEVIE_MASTER_KEY={MASTER}",
                f"  'BRIEVIE_OWNER_PASSWORD' : {OWNER_PW}",
                f'"database_owner_url"={OWNER_PW}',
                f"{bom}POSTGRES_PASSWORD={POSTGRES_PW}",
                f"#  #export POSTGRES_PASSWORD='{POSTGRES_PW}'",
                "BRIEVIE_MASTER_KEY_NOTE=kept",
            ]
        )
        + "\n"
    )

    result = install.run("backup", shell=shell)

    assert result.returncode == 0, result.stderr
    copy = install.stdin_of("store_env_copy")
    # The guard in the image would accept every line that reaches it.
    assert not any(_leaks(line) for line in copy.splitlines())
    for secret in (MASTER, POSTGRES_PW, OWNER_PW, "old-commented-value"):
        assert secret not in copy
    assert "SECRET_KEY=" in copy
    assert "BRIEVIE_MASTER_KEY_NOTE=kept" in copy
    assert install.index("up -d --no-deps keeper") < install.index("store_env_copy")
    assert install.index("store_env_copy") < install.index("exec -T keeper python manage.py backup")
    install.assert_no_secret_in_argv()


def test_backup_passes_the_exit_code_on(installed: Install) -> None:
    result = installed.run("backup", FAKE_DOCKER_FAIL="manage.py backup", FAKE_DOCKER_FAIL_CODE="2")

    assert result.returncode == 2


def test_backup_check_asks_the_keeper_for_a_check(installed: Install) -> None:
    result = installed.run("backup", "--check")

    assert result.returncode == 0, result.stderr
    assert "exec -T keeper python manage.py backup --check" in installed.commands()[-1]


def test_backup_derives_the_password_in_the_container_when_missing(install: Install) -> None:
    install.write_env(current_env(backup_password=False))

    result = install.run("backup")

    assert result.returncode == 0, result.stderr
    assert f"\nBRIEVIE_BACKUP_PASSWORD={DERIVED_PW}\n" in install.dotenv.read_text()
    assert stat.S_IMODE(install.dotenv.stat().st_mode) == 0o600
    assert install.index("derive_backup_password") < install.index("up -d --no-deps keeper")
    derive = install.commands()[install.index("derive_backup_password")]
    assert "run --rm --no-deps -T admin python manage.py derive_backup_password" in derive
    install.assert_no_secret_in_argv()


def test_backup_fills_an_empty_password_line(install: Install) -> None:
    install.write_env(current_env(backup_password=False) + "BRIEVIE_BACKUP_PASSWORD=\n")

    result = install.run("backup")

    assert result.returncode == 0, result.stderr
    lines = install.dotenv.read_text().splitlines()
    assert [line for line in lines if line.startswith("BRIEVIE_BACKUP_PASSWORD")] == [
        f"BRIEVIE_BACKUP_PASSWORD={DERIVED_PW}"
    ]


def test_init_runs_in_the_one_off_container(installed: Install) -> None:
    result = installed.run("backup", "--init", "--allow-same-disk")

    assert result.returncode == 0, result.stderr
    init = installed.index("run --rm --no-deps -T admin python manage.py backup_init")
    assert "--allow-same-disk" in installed.commands()[init]
    assert init < installed.index("exec -T keeper python manage.py backup")


def test_init_failure_stops_before_the_backup(installed: Install) -> None:
    result = installed.run("backup", "--init", FAKE_DOCKER_FAIL="backup_init")

    assert result.returncode == 1
    assert not any(c.endswith("python manage.py backup") for c in installed.commands())


def test_separate_password_is_made_in_a_container(installed: Install) -> None:
    result = installed.run("backup", "--init", "--separate-password")

    assert result.returncode == 0, result.stderr
    text = installed.dotenv.read_text()
    assert f"\nBRIEVIE_BACKUP_PASSWORD={SEPARATE_PW}\n" in text
    assert DERIVED_PW not in text
    assert installed.index("secrets") < installed.index("backup_init")
    assert installed.index("secrets") < installed.index("up -d --no-deps keeper")
    installed.assert_no_secret_in_argv()


def test_separate_password_is_undone_when_init_fails(installed: Install) -> None:
    result = installed.run(
        "backup", "--init", "--separate-password", FAKE_DOCKER_FAIL="backup_init"
    )

    assert result.returncode == 1
    text = installed.dotenv.read_text()
    assert f"\nBRIEVIE_BACKUP_PASSWORD={DERIVED_PW}\n" in text
    assert SEPARATE_PW not in text


# --- status, sweep, recovery sheet, start ---------------------------------------------------


def test_status_asks_the_keeper(installed: Install) -> None:
    result = installed.run("status")

    assert result.returncode == 0, result.stderr
    assert "exec -T keeper python manage.py backup_status" in installed.commands()[-1]


@pytest.mark.parametrize("args", [(), ("--delete",)])
def test_sweep_runs_as_the_app_user(installed: Install, args: tuple[str, ...]) -> None:
    result = installed.run("sweep", *args)

    assert result.returncode == 0, result.stderr
    last = installed.commands()[-1]
    assert "run --rm --no-deps -T --user 10001 admin python manage.py sweep_storage" in last
    assert last.endswith("--delete") == bool(args)


def test_recovery_sheet_is_written_with_mode_600(installed: Install) -> None:
    result = installed.run("recovery-sheet")

    assert result.returncode == 0, result.stderr
    sheet = installed.root / "brievie-herstelblad.html"
    assert sheet.read_text() == "<html>herstelblad</html>\n"
    assert stat.S_IMODE(sheet.stat().st_mode) == 0o600
    assert not (installed.root / "brievie-herstelblad.html.tmp").exists()
    assert "afdrukken" in result.stdout or "Druk" in result.stdout


def test_a_failed_recovery_sheet_leaves_no_file(installed: Install) -> None:
    result = installed.run("recovery-sheet", FAKE_DOCKER_FAIL="recovery_sheet")

    assert result.returncode == 1
    assert sorted(p.name for p in installed.root.iterdir()) == ["brievie", "deploy"]


def test_start_brings_everything_up_and_writes_the_sheet(install: Install) -> None:
    result = install.run("start")

    assert result.returncode == 0, result.stderr
    assert install.dotenv.exists()  # init-env.sh made it
    assert install.index("up -d --build --wait") < install.index("recovery_sheet")
    assert (install.root / "brievie-herstelblad.html").exists()
    install.assert_no_secret_in_argv()


def test_start_keeps_an_existing_sheet(installed: Install) -> None:
    sheet = installed.root / "brievie-herstelblad.html"
    sheet.write_text("old")

    result = installed.run("start")

    assert result.returncode == 0, result.stderr
    assert sheet.read_text() == "old"
    assert not any("recovery_sheet" in command for command in installed.commands())


# --- restore ----------------------------------------------------------------------------


def test_restore_refuses_an_installation_with_data(installed: Install) -> None:
    result = installed.run("restore", **restore_inputs(installed))

    assert result.returncode == 1
    assert "--replace" in result.stderr
    assert not any(" up " in f" {c} " for c in installed.commands())
    assert not any("restore_database" in c for c in installed.commands())
    assert installed.dotenv.read_text() == current_env()
    assert not (installed.root / "deploy" / ".restore").exists()


@pytest.mark.parametrize("flags", [(), ("--replace",), ("--replace", "--skip-safety-backup")])
def test_restore_refuses_volumes_without_an_env_of_its_own(
    install: Install, flags: tuple[str, ...]
) -> None:
    result = install.run(
        "restore",
        *flags,
        "--keep-hostname",
        FAKE_DOCKER_VOLUMES="brievie_pgdata",
        **restore_inputs(install),
    )

    assert result.returncode == 1
    assert "COMPOSE_PROJECT_NAME" in result.stderr
    assert "--skip-safety-backup" not in result.stderr
    assert not install.dotenv.exists()
    assert not (install.root / "deploy" / ".restore").exists()
    assert not any(" stop " in f" {c} " for c in install.commands())
    assert not any("restore_database" in c for c in install.commands())


def test_restore_with_a_wrong_key_creates_no_volume(install: Install) -> None:
    result = install.run(
        "restore",
        FAKE_DOCKER_FAIL="restore_fetch",
        FAKE_DOCKER_FAIL_CODE="2",
        **restore_inputs(install),
    )

    assert result.returncode == 2
    assert "./brievie restore" in result.stderr
    assert not any(c.startswith("compose") for c in install.commands())
    assert not install.dotenv.exists()
    assert not (install.root / "deploy" / ".restore").exists()


def test_restore_refuses_a_folder_without_a_backup(install: Install) -> None:
    (install.backup_folder / "brievie-backup.json").unlink()

    result = install.run("restore", **restore_inputs(install))

    assert result.returncode == 1
    # Only the questions who owns the project and whether its data exists.
    assert install.commands() == [OWNERSHIP_CALL, "volume inspect brievie_pgdata"]
    assert sorted(p.name for p in install.backup_folder.iterdir()) == []


@pytest.mark.parametrize("shell", SHELLS)
def test_restore_runs_the_steps_in_order(install: Install, shell: str) -> None:
    result = install.run(
        "restore", "--keep-hostname", shell=shell, FAKE_DOCKER_RUNNING="", **restore_inputs(install)
    )

    assert result.returncode == 0, result.stderr
    fetch = install.index("restore_fetch")
    stop = install.index("stop api worker caddy keeper")
    postgres = install.index("up -d --wait postgres")
    database = install.index("restore_database")
    files = install.index("restore_files")
    full_up = next(i for i, c in enumerate(install.commands()) if c.endswith("up -d --wait"))
    verify = install.index("verify_restore")
    assert fetch < stop < postgres < database < files < full_up < verify
    # The schema stays empty until pg_restore: nothing but postgres starts before it.
    for command in install.commands()[:database]:
        assert not command.endswith("up -d --wait")
        assert "migrate" not in command
    assert f"--run {RUN_ID} --dbname brievie" in install.commands()[database]
    assert "--replace" not in install.commands()[files]
    assert (
        "verify_restore --dbname brievie --storage-root /data/documents "
        "--manifest /data/backup-config/manifest.json "
        "--authority-dir /caddy-data/caddy/pki/authorities/local"
    ) in install.commands()[verify]
    assert install.dotenv.read_text() == without(restored_env(), *DESTINATION_KEYS)
    assert stat.S_IMODE(install.dotenv.stat().st_mode) == 0o600
    assert not (install.root / "deploy" / ".restore").exists()
    assert result.stdout.rstrip().splitlines()[-1] == "Herstel gelukt."


def test_restore_fetch_gets_the_folder_read_only_and_secrets_by_name(install: Install) -> None:
    result = install.run("restore", "--keep-hostname", **restore_inputs(install))

    assert result.returncode == 0, result.stderr
    fetch = next(c for c in install.calls() if "restore_fetch" in c["argv"])
    argv = fetch["argv"]
    assert argv[:2] == ["run", "--rm"]
    assert f"{install.backup_folder}:/backup:ro" in argv
    assert "brievie/backend:local" in argv
    for name in ("BRIEVIE_MASTER_KEY", "BRIEVIE_BACKUP_PASSWORD", "SECRET_KEY"):
        assert argv[argv.index(name) - 1] == "-e"
    assert not any(a.startswith(("BRIEVIE_MASTER_KEY=", "BRIEVIE_BACKUP_PASSWORD=")) for a in argv)
    assert fetch["env"]["BRIEVIE_MASTER_KEY"] == MASTER  # spaces, dashes, capitals gone
    assert fetch["env"]["BRIEVIE_BACKUP_PASSWORD"] == ""
    assert fetch["env"]["BRIEVIE_BACKUP_DIR_SET"] == "true"
    assert ["--backup-dir-host", str(install.backup_folder)] == argv[-2:] or (
        f"--backup-dir-host {install.backup_folder}" in " ".join(argv)
    )
    assert f"{install.root}/deploy/.restore:/out" in argv
    install.assert_no_secret_in_argv()


def test_restore_of_a_named_run_from_a_server(install: Install) -> None:
    result = install.run(
        "restore",
        RUN_ID,
        "--keep-hostname",
        "--separate-password",
        BRIEVIE_RESTORE_SOURCE="s3:https://s3.example.nl/bak/brievie",
        BRIEVIE_RESTORE_MASTER_KEY=MASTER,
        BRIEVIE_RESTORE_BACKUP_PASSWORD=TYPED_SEPARATE_PW,
        BRIEVIE_BACKUP_S3_ACCESS_KEY_ID="AKIAEXAMPLE",
        BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY=S3_SECRET,
    )

    assert result.returncode == 0, result.stderr
    fetch = next(c for c in install.calls() if "restore_fetch" in c["argv"])
    assert not any(a.endswith(":/backup:ro") for a in fetch["argv"])
    assert f"--run {RUN_ID}" in " ".join(fetch["argv"])
    assert fetch["env"]["BRIEVIE_BACKUP_REPOSITORY"] == "s3:https://s3.example.nl/bak/brievie"
    assert fetch["env"]["BRIEVIE_BACKUP_PASSWORD"] == TYPED_SEPARATE_PW
    assert fetch["env"]["BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY"] == S3_SECRET
    assert fetch["env"].get("BRIEVIE_BACKUP_DIR_SET", "") == ""
    install.assert_no_secret_in_argv()


def test_restore_secrets_do_not_override_the_restored_env(install: Install) -> None:
    """Compose prefers the caller's environment to deploy/.env: after the fetch no compose
    call may see the typed key or password, or an empty one would win over the file."""
    result = install.run(
        "restore",
        "--keep-hostname",
        "--separate-password",
        BRIEVIE_RESTORE_BACKUP_PASSWORD=TYPED_SEPARATE_PW,
        BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY=S3_SECRET,
        **restore_inputs(install),
    )

    assert result.returncode == 0, result.stderr
    compose_calls = [c for c in install.calls() if c["argv"][0] == "compose"]
    assert compose_calls
    for call in compose_calls:
        if "restore_database" in call["argv"] or "restore_files" in call["argv"]:
            # These read the back-up: they get the source's password on purpose.
            assert call["secret_env"] == ["BRIEVIE_BACKUP_PASSWORD"]
        else:
            assert call["secret_env"] == [], call["argv"]


def test_restore_with_a_new_host_name(install: Install) -> None:
    result = install.run("restore", stdin="n\nnieuw.local\n", **restore_inputs(install))

    assert result.returncode == 0, result.stderr
    lines = install.dotenv.read_text().splitlines()
    assert "BRIEVIE_HOSTNAME=nieuw.local" in lines
    assert "BRIEVIE_HOSTNAME=oud.local" not in lines
    assert "Blijft de servernaam oud.local?" in result.stdout + result.stderr
    assert "./brievie recovery-sheet" in result.stdout
    assert "opnieuw koppelen" in result.stdout + result.stderr


def test_replace_stops_backs_up_then_drops(installed: Install) -> None:
    result = installed.run("restore", "--replace", "--keep-hostname", **restore_inputs(installed))

    assert result.returncode == 0, result.stderr
    stop_services = installed.index("stop api worker caddy")
    safety = installed.index("exec -T keeper python manage.py backup")
    stop_keeper = next(i for i, c in enumerate(installed.commands()) if c.endswith("stop keeper"))
    drop = installed.index("psql -U postgres -d postgres")
    database = installed.index("restore_database")
    files = installed.index("restore_files")
    assert stop_services < safety < stop_keeper < drop < database < files
    script = installed.stdin_of("psql -U postgres -d postgres")
    assert "DROP DATABASE IF EXISTS brievie WITH (FORCE);" in script
    assert f"\\set postgres_password '{NEW_POSTGRES_PW}'" in script
    assert f"\\set owner_password '{NEW_OWNER_PW}'" in script
    assert f"\\set app_password '{APP_PW}'" in script
    assert "\\i /docker-entrypoint-initdb.d/database.psql" in script
    # The back-up role comes back after the database (ensure-backup-role.sh).
    role = max(
        i for i, c in enumerate(installed.calls()) if "backup-role.psql" in c.get("stdin", "")
    )
    assert drop < role < database
    assert installed.commands()[files].endswith("--replace")
    olds = list((installed.root / "deploy").glob(".env.before-restore-*"))
    assert len(olds) == 1
    assert olds[0].read_text() == current_env()
    assert stat.S_IMODE(olds[0].stat().st_mode) == 0o600
    final = installed.dotenv.read_text()
    assert setting(final, "BRIEVIE_INSTALLATION_ID") == [f"BRIEVIE_INSTALLATION_ID={CURRENT_ID}"]
    assert setting(final, "BRIEVIE_BACKUP_DIR") == ["BRIEVIE_BACKUP_DIR=/Volumes/Backup/brievie"]
    assert setting(final, "POSTGRES_PASSWORD") == [f"POSTGRES_PASSWORD={NEW_POSTGRES_PW}"]
    assert setting(final, "BRIEVIE_HOSTNAME") == ["BRIEVIE_HOSTNAME=oud.local"]
    installed.assert_no_secret_in_argv()
    assert compose_projects(installed) == {"brievie"}  # the child safety back-up too


def test_replace_stops_when_the_safety_backup_fails(installed: Install) -> None:
    result = installed.run(
        "restore",
        "--replace",
        "--keep-hostname",
        FAKE_DOCKER_FAIL="python manage.py backup",
        **restore_inputs(installed),
    )

    assert result.returncode == 1
    assert "--skip-safety-backup" not in result.stderr
    assert "./brievie start" in result.stderr
    assert installed.dotenv.read_text() == current_env()
    assert not (installed.root / "deploy" / ".restore").exists()
    assert not any(
        "restore_database" in c or "psql -U postgres -d postgres" in c for c in installed.commands()
    )


def test_replace_can_skip_the_safety_backup(installed: Install) -> None:
    result = installed.run(
        "restore",
        "--replace",
        "--skip-safety-backup",
        "--keep-hostname",
        **restore_inputs(installed),
    )

    assert result.returncode == 0, result.stderr
    assert not any(c.endswith("python manage.py backup") for c in installed.commands())


def test_a_failed_files_restore_says_what_to_type(install: Install) -> None:
    result = install.run(
        "restore", "--keep-hostname", FAKE_DOCKER_FAIL="restore_files", **restore_inputs(install)
    )

    assert result.returncode == 1
    assert f"./brievie restore {RUN_ID} --replace" in result.stderr
    assert "halverwege" in result.stderr
    assert "--skip-safety-backup" not in result.stderr
    assert not any(c.endswith("up -d --wait") for c in install.commands())


def compose_projects(install: Install) -> set[str]:
    """The -p value of every compose call (None when a call has none)."""
    projects = set()
    for call in install.calls():
        argv = call["argv"]
        if argv[0] == "compose":
            projects.add(argv[argv.index("-p") + 1] if "-p" in argv else "<none>")
    return projects


@pytest.mark.parametrize("restored_project", [None, "other"])
def test_restore_stays_in_the_installations_own_project(
    install: Install, restored_project: str | None
) -> None:
    install.write_env(current_env(project="test"))
    install.restored.write_text(restored_env(project=restored_project))

    result = install.run(
        "restore",
        "--replace",
        "--keep-hostname",
        FAKE_DOCKER_VOLUMES="brievie_pgdata other_pgdata",
        **restore_inputs(install),
    )

    assert result.returncode == 0, result.stderr
    assert compose_projects(install) == {"test"}
    assert not any("other" in argument for c in install.calls() for argument in c["argv"])
    lines = install.dotenv.read_text().splitlines()
    assert [line for line in lines if line.startswith("COMPOSE_PROJECT_NAME")] == [
        "COMPOSE_PROJECT_NAME=test"
    ]


def test_restore_into_the_default_project_drops_the_backups_name(install: Install) -> None:
    install.restored.write_text(restored_env(project="other"))

    result = install.run(
        "restore", "--keep-hostname", FAKE_DOCKER_VOLUMES="other_pgdata", **restore_inputs(install)
    )

    assert result.returncode == 0, result.stderr
    assert compose_projects(install) == {"brievie"}
    assert "volume inspect brievie_pgdata" in install.commands()
    assert "COMPOSE_PROJECT_NAME" not in install.dotenv.read_text()


def test_a_project_from_the_environment_is_written_into_the_restored_env(install: Install) -> None:
    result = install.run(
        "restore",
        "--keep-hostname",
        COMPOSE_PROJECT_NAME="naast",
        FAKE_DOCKER_VOLUMES="brievie_pgdata",
        **restore_inputs(install),
    )

    assert result.returncode == 0, result.stderr
    assert compose_projects(install) == {"naast"}
    assert "volume inspect naast_pgdata" in install.commands()
    assert "\nCOMPOSE_PROJECT_NAME=naast\n" in install.dotenv.read_text()


def test_restore_keeps_the_installations_image_and_ports(install: Install) -> None:
    install.write_env(
        current_env(image="eigen/brievie:2")
        + "POSTGRES_PORT=5433\nBRIEVIE_HTTP_PORT=8480\nBRIEVIE_HTTPS_PORT=8443\n"
    )
    install.restored.write_text(
        restored_env() + "BRIEVIE_IMAGE=oud/brievie:1\nPOSTGRES_PORT=5432\nBRIEVIE_HTTP_PORT=80\n"
    )

    result = install.run("restore", "--replace", "--keep-hostname", **restore_inputs(install))

    assert result.returncode == 0, result.stderr
    lines = install.dotenv.read_text().splitlines()
    assert "BRIEVIE_IMAGE=eigen/brievie:2" in lines
    assert "BRIEVIE_IMAGE=oud/brievie:1" not in lines
    assert "POSTGRES_PORT=5433" in lines
    assert "BRIEVIE_HTTP_PORT=8480" in lines
    assert "BRIEVIE_HTTPS_PORT=8443" in lines
    fetch = next(c for c in install.calls() if "restore_fetch" in c["argv"])
    assert "eigen/brievie:2" in fetch["argv"]


def test_a_fresh_restore_takes_the_ports_but_not_the_image_of_the_backup(install: Install) -> None:
    install.restored.write_text(
        restored_env() + "BRIEVIE_IMAGE=oud/brievie:1\nPOSTGRES_PORT=5433\n"
    )

    result = install.run(
        "restore", "--keep-hostname", BRIEVIE_IMAGE="eigen/brievie:1.4", **restore_inputs(install)
    )

    assert result.returncode == 0, result.stderr
    text = install.dotenv.read_text()
    assert "BRIEVIE_IMAGE" not in text
    assert "\nPOSTGRES_PORT=5433\n" in text
    fetch = next(c for c in install.calls() if "restore_fetch" in c["argv"])
    assert "eigen/brievie:1.4" in fetch["argv"]


@pytest.mark.parametrize(
    "args",
    [
        ("start",),
        ("backup",),
        ("backup", "--init"),
        ("status",),
        ("sweep",),
        ("sweep", "--delete"),
        ("recovery-sheet",),
        ("restore", "--replace"),
    ],
)
def test_refuses_when_another_checkout_owns_the_project(
    installed: Install, args: tuple[str, ...]
) -> None:
    result = installed.run(
        *args, FAKE_DOCKER_OWNERS="/srv/brievie-live/deploy", **restore_inputs(installed)
    )

    assert result.returncode == 1
    assert "/srv/brievie-live/deploy" in result.stderr
    assert "COMPOSE_PROJECT_NAME" in result.stderr
    assert installed.commands() == [OWNERSHIP_CALL]
    assert installed.dotenv.read_text() == current_env()


def test_its_own_containers_are_no_reason_to_refuse(installed: Install) -> None:
    link = installed.tmp / "via-link"
    link.symlink_to(installed.root)

    result = installed.run("status", FAKE_DOCKER_OWNERS=f"{link}/deploy|{installed.root}/deploy")

    assert result.returncode == 0, result.stderr


def test_replace_without_confirmation_changes_nothing(installed: Install) -> None:
    result = installed.run(
        "restore",
        "--replace",
        "--keep-hostname",
        **restore_inputs(installed, BRIEVIE_RESTORE_CONFIRM="ja"),
    )

    assert result.returncode == 1
    assert "WISSEN" in result.stdout + result.stderr
    assert not any(" stop " in f" {c} " or "psql -U postgres -d" in c for c in installed.commands())
    assert installed.dotenv.read_text() == current_env()
    assert not (installed.root / "deploy" / ".restore").exists()


def test_a_piped_word_never_confirms_a_replace(installed: Install) -> None:
    result = installed.run(
        "restore",
        "--replace",
        "--skip-safety-backup",
        "--keep-hostname",
        stdin="WISSEN\nWISSEN\n",
        **restore_inputs(installed, BRIEVIE_RESTORE_CONFIRM=""),
    )

    assert result.returncode == 1
    assert "BRIEVIE_RESTORE_CONFIRM=WISSEN" in result.stderr
    assert "veiligheidsback-up" in result.stdout  # the warning was shown
    assert not any(" stop " in f" {c} " or "psql" in c for c in installed.commands())
    assert installed.dotenv.read_text() == current_env()
    assert not (installed.root / "deploy" / ".restore").exists()


def test_a_failed_stop_before_the_swap_leaves_the_installation(installed: Install) -> None:
    result = installed.run(
        "restore",
        "--replace",
        "--keep-hostname",
        FAKE_DOCKER_FAIL="stop api worker caddy",
        **restore_inputs(installed),
    )

    assert result.returncode == 1
    assert "./brievie" in result.stderr
    assert installed.dotenv.read_text() == current_env()
    assert not (installed.root / "deploy" / ".restore").exists()
    assert not any("restore_database" in c for c in installed.commands())


def test_a_failed_stop_after_the_swap_says_it_stopped_halfway(install: Install) -> None:
    result = install.run(
        "restore",
        "--keep-hostname",
        FAKE_DOCKER_FAIL="stop api worker caddy keeper",
        **restore_inputs(install),
    )

    assert result.returncode == 1
    assert f"./brievie restore {RUN_ID} --replace" in result.stderr
    assert "halverwege" in result.stderr
    assert not any("restore_database" in c for c in install.commands())


@pytest.mark.parametrize(
    "run_id", ["../../etc", "0b6c3f7e", "", "0B6C3F7E-1D2A-4C5B-9E8F-7A6B5C4D3E2F x"]
)
def test_a_strange_run_id_from_the_fetch_stops_the_restore(install: Install, run_id: str) -> None:
    result = install.run(
        "restore", "--keep-hostname", FAKE_RUN_ID=run_id, **restore_inputs(install)
    )

    assert result.returncode == 1
    assert not any(c.startswith("compose") for c in install.commands())
    assert not install.dotenv.exists()
    assert not (install.root / "deploy" / ".restore").exists()


def test_a_typed_run_id_must_look_like_one(installed: Install) -> None:
    result = installed.run("restore", "abc-def", "--replace")

    assert result.returncode == 2
    assert installed.calls() == []


def test_the_restored_env_stays_private_after_a_new_host_name(installed: Install) -> None:
    watched = installed.root / "deploy" / ".restore" / "env"

    result = installed.run(
        "restore",
        "--replace",
        stdin="n\nnieuw.local\n",
        FAKE_WATCH=str(watched),
        **restore_inputs(installed),
    )

    assert result.returncode == 0, result.stderr
    modes = [c["watch_mode"] for c in installed.calls() if "watch_mode" in c]
    assert modes
    assert set(modes) == {"0o600"}


@pytest.mark.parametrize("bad", ["", "not-hex", "E5" * 32, "e5" * 31, "e5" * 32 + "\nx"])
def test_a_strange_derived_password_is_not_written(install: Install, bad: str) -> None:
    install.write_env(current_env(backup_password=False))

    result = install.run("backup", FAKE_DERIVED_PASSWORD=bad)

    assert result.returncode == 1
    assert install.dotenv.read_text() == current_env(backup_password=False)
    assert not any("store_env_copy" in c for c in install.commands())


def test_a_strange_separate_password_is_not_written(installed: Install) -> None:
    result = installed.run("backup", "--init", "--separate-password", FAKE_SEPARATE_PASSWORD="oops")

    assert result.returncode == 1
    assert installed.dotenv.read_text() == current_env()
    assert not any("backup_init" in c for c in installed.commands())


def test_the_image_in_env_is_used_for_the_fetch(install: Install) -> None:
    install.write_env(current_env(project="tweede", image="eigen/brievie:2"))
    result = install.run(
        "restore", "--replace", "--skip-safety-backup", "--keep-hostname", **restore_inputs(install)
    )

    assert result.returncode == 0, result.stderr
    fetch = next(c for c in install.calls() if "restore_fetch" in c["argv"])
    assert "eigen/brievie:2" in fetch["argv"]


def test_restore_never_puts_secrets_in_argv_with_typed_answers(install: Install) -> None:
    # No BRIEVIE_RESTORE_* variables: everything comes from stdin, as typed.
    stdin = f"{install.backup_folder}\n{TYPED_MASTER}{TYPED_SEPARATE_PW}\nj\n"

    result = install.run("restore", "--separate-password", stdin=stdin)

    assert result.returncode == 0, result.stderr
    fetch = next(c for c in install.calls() if "restore_fetch" in c["argv"])
    assert fetch["env"]["BRIEVIE_MASTER_KEY"] == MASTER
    assert fetch["env"]["BRIEVIE_BACKUP_PASSWORD"] == TYPED_SEPARATE_PW
    install.assert_no_secret_in_argv()
    assert MASTER not in result.stdout + result.stderr


# --- the script's text --------------------------------------------------------------------


def script_text() -> str:
    return SCRIPT.read_text()


def test_script_is_posix_sh_and_executable() -> None:
    text = script_text()
    assert text.startswith("#!/bin/sh\n")
    assert "\nset -eu\n" in text
    assert SCRIPT.stat().st_mode & stat.S_IXUSR
    for bashism in ("[[ ", "readlink -f", "sed -i", "eval ", "$'", "function ", "<<<", "pipefail"):
        assert bashism not in text, bashism
    assert not re.search(r"^\s*(local|source|declare|typeset)\s", text, re.MULTILINE)


def test_script_writes_and_removes_only_at_its_own_names() -> None:
    code = "\n".join(
        line for line in script_text().splitlines() if not line.lstrip().startswith("#")
    )
    for match in re.finditer(r"(?<![-\w])(rm|mv|mkdir) [^\n]*", code):
        command = match.group(0)
        assert '"${here:?}/' in command, command
        if command.startswith("rm -r"):
            assert command == 'rm -rf "${here:?}/deploy/.restore"', command
    # A redirect to a file names a fixed path under the script's folder.
    for match in re.finditer(r'(?<![<>&\d])>"?(\S+)', code):
        target = match.group(1)
        assert target.startswith(("${here:?}/", "&2", "/dev/null")), target
    assert 'rm -rf "$' not in code.replace('rm -rf "${here:?}/deploy/.restore"', "")


def test_shellcheck_when_available() -> None:
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck is not installed")
    result = subprocess.run(  # noqa: S603
        [shellcheck, "-s", "sh", str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout


def test_replace_drops_nothing_when_a_password_is_missing(installed: Install) -> None:
    installed.restored.write_text(
        "".join(
            line + "\n"
            for line in restored_env().splitlines()
            if not line.startswith("BRIEVIE_APP_PASSWORD=")
        )
    )

    result = installed.run(
        "restore",
        "--replace",
        "--skip-safety-backup",
        "--keep-hostname",
        **restore_inputs(installed),
    )

    assert result.returncode == 1
    assert "BRIEVIE_APP_PASSWORD" in result.stderr
    assert "./brievie restore" in result.stderr
    assert not any("psql -U postgres -d postgres" in c for c in installed.commands())
    assert not any("restore_database" in c for c in installed.commands())


# --- fix round 2: what the wrapper cannot prove is its own ---------------------------------


def test_a_failing_role_after_the_drop_says_how_to_go_on(installed: Install) -> None:
    result = installed.run(
        "restore",
        "--replace",
        "--skip-safety-backup",
        "--keep-hostname",
        FAKE_DOCKER_FAIL="psql -U postgres -v ON_ERROR_STOP",
        **restore_inputs(installed),
    )

    assert result.returncode == 1
    assert "back-uprol" in result.stderr
    assert f"./brievie restore {RUN_ID} --replace" in result.stderr
    assert not any("restore_database" in c for c in installed.commands())


@pytest.mark.parametrize("command", ["start", "backup", "status", "sweep", "recovery-sheet"])
def test_every_command_refuses_volumes_without_settings(install: Install, command: str) -> None:
    result = install.run(command, FAKE_DOCKER_VOLUMES="brievie_pgdata")

    assert result.returncode == 1
    assert "brievie" in result.stderr
    assert "geen instellingen" in result.stderr
    assert install.commands() == [OWNERSHIP_CALL, "volume inspect brievie_pgdata"]
    assert not install.dotenv.exists()  # init-env.sh never ran


def test_a_failing_docker_ps_refuses(installed: Install) -> None:
    result = installed.run("status", FAKE_DOCKER_FAIL="ps -a")

    assert result.returncode == 1
    assert "Docker" in result.stderr
    assert installed.commands() == [OWNERSHIP_CALL]


def test_a_container_without_a_folder_label_counts_as_foreign(installed: Install) -> None:
    result = installed.run("status", FAKE_DOCKER_OWNERS="<empty>")

    assert result.returncode == 1
    assert "COMPOSE_PROJECT_NAME" in result.stderr
    assert installed.commands() == [OWNERSHIP_CALL]


@pytest.mark.parametrize("name", ["Hoofdletter", "met spatie", "-streep", "a/b"])
def test_an_invalid_project_name_is_refused_but_help_works(installed: Install, name: str) -> None:
    refused = installed.run("status", COMPOSE_PROJECT_NAME=name)
    helped = installed.run("help", COMPOSE_PROJECT_NAME=name)

    assert refused.returncode == 1
    assert "COMPOSE_PROJECT_NAME" in refused.stderr
    assert helped.returncode == 0
    assert "./brievie restore" in helped.stdout
    assert installed.calls() == []


def test_an_unreadable_env_is_refused(installed: Install) -> None:
    installed.dotenv.chmod(0)
    try:
        result = installed.run("status")
    finally:
        installed.dotenv.chmod(0o600)

    assert result.returncode == 1
    assert "deploy/.env" in result.stderr
    assert installed.calls() == []


@pytest.mark.parametrize(
    "line", ["export COMPOSE_PROJECT_NAME=tweede", '  "COMPOSE_PROJECT_NAME" = tweede']
)
def test_every_spelling_of_the_project_line_is_understood(installed: Install, line: str) -> None:
    installed.write_env(current_env() + line + "\n")

    result = installed.run("status")

    assert result.returncode == 0, result.stderr
    assert compose_projects(installed) == {"tweede"}


def test_rewrites_catch_every_spelling_of_their_key(install: Install) -> None:
    install.restored.write_text(
        restored_env().replace("BRIEVIE_HOSTNAME=oud.local", "export BRIEVIE_HOSTNAME=oud.local")
        + "  'COMPOSE_PROJECT_NAME' = ander\nexport BRIEVIE_IMAGE=oud/brievie:1\n"
    )

    result = install.run("restore", stdin="n\nnieuw.local\n", **restore_inputs(install))

    assert result.returncode == 0, result.stderr
    text = install.dotenv.read_text()
    assert [line for line in text.splitlines() if "BRIEVIE_HOSTNAME" in line] == [
        "BRIEVIE_HOSTNAME=nieuw.local"
    ]
    assert "COMPOSE_PROJECT_NAME" not in text
    assert "BRIEVIE_IMAGE" not in text


def test_a_stale_restore_folder_is_removed_first(install: Install) -> None:
    stale = install.root / "deploy" / ".restore"
    stale.mkdir()
    (stale / "env").write_text("STALE=1\n")
    (stale / "left-over").write_text("x")

    result = install.run(
        "restore",
        "--keep-hostname",
        FAKE_WATCH=str(stale / "left-over"),
        **restore_inputs(install),
    )

    assert result.returncode == 0, result.stderr
    fetch = next(c for c in install.calls() if "restore_fetch" in c["argv"])
    assert "watch_mode" not in fetch
    assert "STALE" not in install.dotenv.read_text()


# --- the image tag of a checkout with its own name -----------------------------------------


def test_a_named_checkout_builds_its_own_image_tag(install: Install) -> None:
    install.write_env(current_env(project="thuis"))

    result = install.run("start")

    assert result.returncode == 0, result.stderr
    images = {c["image_env"] for c in install.calls() if c["argv"][0] == "compose"}
    assert images == {"brievie/backend:thuis"}


def test_the_default_checkout_keeps_the_local_tag(installed: Install) -> None:
    result = installed.run("start")

    assert result.returncode == 0, result.stderr
    images = {c["image_env"] for c in installed.calls() if c["argv"][0] == "compose"}
    assert images == {"brievie/backend:local"}


def test_a_named_fresh_restore_fetches_with_its_own_tag(install: Install) -> None:
    result = install.run(
        "restore", "--keep-hostname", COMPOSE_PROJECT_NAME="proef", **restore_inputs(install)
    )

    assert result.returncode == 0, result.stderr
    fetch = next(c for c in install.calls() if "restore_fetch" in c["argv"])
    assert "brievie/backend:proef" in fetch["argv"]
    assert "image inspect brievie/backend:proef" in install.commands()


# --- the installation id --------------------------------------------------------------------


def id_writes(install: Install) -> list[str]:
    return [
        c.get("stdin", "")
        for c in install.calls()
        if "cat > /data/backup-config/installation-id" in " ".join(c["argv"])
    ]


def test_start_gives_an_old_installation_an_id(install: Install) -> None:
    install.write_env(current_env(installation_id=None))

    result = install.run("start")

    assert result.returncode == 0, result.stderr
    assert setting(install.dotenv.read_text(), "BRIEVIE_INSTALLATION_ID") == [
        f"BRIEVIE_INSTALLATION_ID={NEW_ID}"
    ]
    assert id_writes(install) == [NEW_ID + "\n"]
    assert stat.S_IMODE(install.dotenv.stat().st_mode) == 0o600


def test_start_writes_its_id_into_an_unmarked_volume(installed: Install) -> None:
    result = installed.run("start")

    assert result.returncode == 0, result.stderr
    assert id_writes(installed) == [CURRENT_ID + "\n"]


def test_start_accepts_its_own_marked_volume(installed: Install) -> None:
    result = installed.run("start", FAKE_VOLUME_ID=CURRENT_ID + "\n")

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "args",
    [("start",), ("backup", "--init"), ("sweep", "--delete"), ("restore", "--replace")],
)
def test_a_volume_of_another_installation_is_refused(
    installed: Install, args: tuple[str, ...]
) -> None:
    result = installed.run(*args, FAKE_VOLUME_ID="ee" * 16, **restore_inputs(installed))

    assert result.returncode == 1
    assert "hoort bij een andere installatie" in result.stderr
    assert "brievie" in result.stderr
    for command in installed.commands():
        assert " stop " not in f" {command} "
        assert "up -d" not in command
        assert "backup_init" not in command
        assert "sweep_storage" not in command
        assert "psql -U postgres -d postgres" not in command
    assert id_writes(installed) == []
    assert installed.dotenv.read_text() == current_env()


def test_a_fresh_restore_keeps_the_backups_id(install: Install) -> None:
    result = install.run("restore", "--keep-hostname", **restore_inputs(install))

    assert result.returncode == 0, result.stderr
    assert setting(install.dotenv.read_text(), "BRIEVIE_INSTALLATION_ID") == [
        f"BRIEVIE_INSTALLATION_ID={BACKUP_ID}"
    ]
    assert id_writes(install) == [BACKUP_ID + "\n"]
    assert install.index("cat > /data/backup-config/installation-id") > install.index(
        "verify_restore"
    )


def test_a_fresh_restore_without_an_id_gets_a_new_one(install: Install) -> None:
    install.restored.write_text(restored_env(installation_id=None))

    result = install.run("restore", "--keep-hostname", **restore_inputs(install))

    assert result.returncode == 0, result.stderr
    assert id_writes(install) == [NEW_ID + "\n"]
    assert f"BRIEVIE_INSTALLATION_ID={NEW_ID}" in install.dotenv.read_text()


def test_a_replace_keeps_the_installations_id(installed: Install) -> None:
    result = installed.run(
        "restore",
        "--replace",
        "--keep-hostname",
        FAKE_VOLUME_ID=CURRENT_ID,
        **restore_inputs(installed),
    )

    assert result.returncode == 0, result.stderr
    assert id_writes(installed)[-1] == CURRENT_ID + "\n"
    check = installed.index("cat /data/backup-config/installation-id")
    assert check < installed.index("stop api worker caddy")


# --- the back-up destination after a restore ------------------------------------------------


def test_a_fresh_restore_makes_no_backups_unless_asked(install: Install) -> None:
    result = install.run("restore", "--keep-hostname", **restore_inputs(install))

    assert result.returncode == 0, result.stderr
    text = install.dotenv.read_text()
    for key in DESTINATION_KEYS:
        assert setting(text, key) == []
    assert "./brievie backup --init" in result.stdout
    assert result.stdout.rstrip().splitlines()[-1] == "Herstel gelukt."
    # The restore itself still reads the back-up it came from.
    for name in ("restore_database", "restore_files"):
        call = next(c for c in install.calls() if name in c["argv"])
        assert call["backup_dir_env"] == str(install.backup_folder)
    keeper_up = next(c for c in install.calls() if c["argv"][-3:] == ["up", "-d", "--wait"])
    assert keeper_up["backup_dir_env"] == "<unset>"


def test_a_fresh_restore_can_keep_backing_up_to_the_folder(install: Install) -> None:
    result = install.run(
        "restore",
        "--keep-hostname",
        **restore_inputs(install, BRIEVIE_RESTORE_KEEP_DESTINATION="ja"),
    )

    assert result.returncode == 0, result.stderr
    text = install.dotenv.read_text()
    assert setting(text, "BRIEVIE_BACKUP_DIR") == [f"BRIEVIE_BACKUP_DIR={install.backup_folder}"]
    assert setting(text, "BRIEVIE_BACKUP_REPOSITORY") == []


def test_a_fresh_restore_asks_about_the_destination(install: Install) -> None:
    result = install.run("restore", "--keep-hostname", stdin="j\n", **restore_inputs(install))

    assert result.returncode == 0, result.stderr
    assert "dezelfde plek" in result.stdout
    assert setting(install.dotenv.read_text(), "BRIEVIE_BACKUP_DIR") == [
        f"BRIEVIE_BACKUP_DIR={install.backup_folder}"
    ]


def test_a_fresh_restore_can_keep_backing_up_to_a_server(install: Install) -> None:
    result = install.run(
        "restore",
        "--keep-hostname",
        BRIEVIE_RESTORE_SOURCE="s3:https://s3.example.nl/bak/brievie",
        BRIEVIE_RESTORE_MASTER_KEY=MASTER,
        BRIEVIE_RESTORE_KEEP_DESTINATION="ja",
        BRIEVIE_BACKUP_S3_ACCESS_KEY_ID="AKIAEXAMPLE",
        BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY=S3_SECRET,
    )

    assert result.returncode == 0, result.stderr
    text = install.dotenv.read_text()
    assert setting(text, "BRIEVIE_BACKUP_REPOSITORY") == [
        "BRIEVIE_BACKUP_REPOSITORY=s3:https://s3.example.nl/bak/brievie"
    ]
    assert setting(text, "BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY") == [
        f"BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY={S3_SECRET}"
    ]
    assert setting(text, "BRIEVIE_BACKUP_DIR") == []
    install.assert_no_secret_in_argv()


def test_a_replace_keeps_the_installations_destination(installed: Install) -> None:
    installed.write_env(
        current_env().replace(
            "BRIEVIE_BACKUP_DIR=/Volumes/Backup/brievie", "BRIEVIE_BACKUP_DIR=/Volumes/Eigen"
        )
    )

    result = installed.run("restore", "--replace", "--keep-hostname", **restore_inputs(installed))

    assert result.returncode == 0, result.stderr
    text = installed.dotenv.read_text()
    assert setting(text, "BRIEVIE_BACKUP_DIR") == ["BRIEVIE_BACKUP_DIR=/Volumes/Eigen"]
    assert setting(text, "BRIEVIE_BACKUP_PASSWORD") == [f"BRIEVIE_BACKUP_PASSWORD={DERIVED_PW}"]
    restore_database = next(c for c in installed.calls() if "restore_database" in c["argv"])
    assert restore_database["backup_dir_env"] == str(installed.backup_folder)
    assert "dezelfde plek" in result.stdout


def test_a_failed_swap_puts_the_old_settings_back(installed: Install) -> None:
    # The fake takes the prepared file away while the keeper stops, so the second move of
    # the swap fails after the first one moved the old deploy/.env aside.
    result = installed.run(
        "restore",
        "--replace",
        "--keep-hostname",
        FAKE_REMOVE_AT="stop keeper",
        FAKE_REMOVE_PATH=str(installed.root / "deploy" / ".restore" / "env"),
        **restore_inputs(installed),
    )

    assert result.returncode == 1
    assert "./brievie start" in result.stderr
    assert installed.dotenv.read_text() == current_env()
    assert list((installed.root / "deploy").glob(".env.before-restore-*")) == []
    assert not (installed.root / "deploy" / ".restore").exists()
    assert not any("postgres" in c and "up -d" in c for c in installed.commands())
