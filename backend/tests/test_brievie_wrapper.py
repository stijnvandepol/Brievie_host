"""./brievie: the shell wrapper a home user types, run against a fake `docker`.

Every test copies the wrapper and deploy/ into tmp_path and runs it there, with a fake
`docker` first on PATH and HOME inside tmp_path. No test reaches the real docker, and
no test runs the wrapper from the repository.
"""

import json
import os
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
SAFETY_ID = "5afe0000-1d2a-4c5b-9e8f-7a6b5c4d3e2f"
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
    mount_mode_env: str
    source_env: list[str]
    wrapper_env: dict[str, str]
    created: list[str]
    recreated: list[str]


# The fake: logs each call as a JSON line, answers from FAKE_* variables, and writes the
# restored files of `restore_fetch` into the folder mounted at /out. It reads stdin only
# for the calls that pipe something in (store_env_copy, psql), never the user's answers.
FAKE_DOCKER = """#!{python}
import json, os, re, signal, sys
from pathlib import Path


def compose_up(argv):
    # What `compose up` creates or recreates, decided like compose: a service is
    # recreated when its definition changed since the last `up`, its text in the compose
    # file with the values of the variables it names (the environment wins over the
    # env file). The content of a bind-mounted file is no part of it.
    compose_file = Path(argv[argv.index("-f") + 1])
    project_dir = Path(argv[argv.index("--project-directory") + 1])
    if "--env-file" in argv:
        env_file = Path(argv[argv.index("--env-file") + 1])
    else:
        env_file = project_dir / ".env"
    dotenv = dict()
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                dotenv[key.strip()] = value
    blocks = dict()
    inside, name = False, None
    for line in compose_file.read_text().splitlines():
        if line and not line.startswith(" "):
            inside, name = line == "services:", None
        elif inside and re.match(r"^  [a-z][a-z-]*:$", line):
            name = line.strip()[:-1]
            blocks[name] = []
        elif inside and name:
            blocks[name].append(line)
    wanted = [a for a in argv[argv.index("up") + 1:] if not a.startswith("-")]
    state_path = Path(os.environ["FAKE_DOCKER_LOG"] + ".services")
    state = json.loads(state_path.read_text()) if state_path.exists() else dict()
    created, recreated = [], []
    for name, lines in blocks.items():
        text = "\\n".join(lines)
        if (wanted and name not in wanted) or "profiles:" in text:
            continue
        names = sorted(set(re.findall(r"\\$\\{{([A-Z0-9_]+)", text)))
        values = [n + "=" + os.environ.get(n, dotenv.get(n, "")) for n in names]
        definition = "\\n".join([text] + values)
        if name not in state:
            created.append(name)
        elif state[name] != definition:
            recreated.append(name)
        state[name] = definition
    state_path.write_text(json.dumps(state))
    return created, recreated


argv = sys.argv[1:]
# The quieting pairs the wrapper adds are recorded apart, so argv stays comparable.
wrapper_env = {{}}
for name in ("BRIEVIE_WRAPPER", "BRIEVIE_LOG_LEVEL"):
    for i in range(len(argv) - 1):
        if argv[i] == "-e" and argv[i + 1].startswith(name + "="):
            wrapper_env[name] = argv[i + 1].split("=", 1)[1]
            del argv[i:i + 2]
            break
joined = " ".join(argv)
entry = {{"argv": argv}}
if wrapper_env:
    entry["wrapper_env"] = wrapper_env
piped = ("store_env_copy" in argv or "psql" in argv
         or "installation-id.tmp" in joined)
if piped:
    entry["stdin"] = sys.stdin.read()
secret_names = ("BRIEVIE_MASTER_KEY", "BRIEVIE_BACKUP_PASSWORD", "BRIEVIE_RESTORE_MASTER_KEY",
                "BRIEVIE_RESTORE_BACKUP_PASSWORD", "BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY")
if argv[:1] == ["compose"]:
    entry["secret_env"] = [name for name in secret_names if os.environ.get(name)]
    entry["image_env"] = os.environ.get("BRIEVIE_IMAGE", "")
    entry["backup_dir_env"] = os.environ.get("BRIEVIE_BACKUP_DIR", "<unset>")
    entry["mount_mode_env"] = os.environ.get("BRIEVIE_BACKUP_MOUNT_MODE", "<unset>")
    source_names = ("BRIEVIE_BACKUP_S3_ACCESS_KEY_ID", "BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY",
                    "BRIEVIE_BACKUP_REST_USERNAME", "BRIEVIE_BACKUP_REST_PASSWORD",
                    "BRIEVIE_BACKUP_REPOSITORY")
    entry["source_env"] = [name for name in source_names if os.environ.get(name)]
    if "up" in argv:
        entry["created"], entry["recreated"] = compose_up(argv)
if "restore_fetch" in argv:
    names = ("BRIEVIE_MASTER_KEY", "BRIEVIE_BACKUP_PASSWORD", "BRIEVIE_BACKUP_DIR_SET",
             "BRIEVIE_BACKUP_REPOSITORY", "BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY", "SECRET_KEY")
    entry["env"] = {{name: os.environ[name] for name in names if name in os.environ}}
watch = os.environ.get("FAKE_WATCH", "")
if watch and os.path.exists(watch):
    entry["watch_mode"] = oct(os.stat(watch).st_mode & 0o777)
with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
    log.write(json.dumps(entry) + "\\n")

gone_marker = os.environ.get("FAKE_DOCKER_LOG", "") + ".keeper-gone"
gone_after = os.environ.get("FAKE_KEEPER_GONE_AFTER", "")
if gone_after and gone_after in joined:
    Path(gone_marker).write_text("gone")
kill_at = os.environ.get("FAKE_KILL_AT", "")
if kill_at and kill_at in joined:
    # Ctrl-C (TERM) to the wrapper while this step runs.
    os.kill(os.getppid(), signal.SIGTERM)
stderr_at = os.environ.get("FAKE_STDERR_AT", "")
if stderr_at and stderr_at in joined:
    sys.stderr.write(os.environ.get("FAKE_STDERR_TEXT", ""))
wanted = os.environ.get("FAKE_FETCH_PASSWORD", "")
if "restore_fetch" in argv and wanted and os.environ.get("BRIEVIE_BACKUP_PASSWORD", "") != wanted:
    sys.stderr.write("CommandError: Het wachtwoord van de back-up klopt niet.\\n")
    sys.exit(2)
remove_at = os.environ.get("FAKE_REMOVE_AT", "")
if remove_at and remove_at in joined:
    os.remove(os.environ["FAKE_REMOVE_PATH"])
fail = os.environ.get("FAKE_DOCKER_FAIL", "")
noisy = os.environ.get("FAKE_NOISE") == "1" and argv[0] in ("compose", "run", "build")
if noisy:
    # What a real run printed between the Dutch sentences.
    sys.stderr.write(" Container brievie-admin-run-1  Creating\\n")
    sys.stderr.write(" Network brievie_default  Created\\n")
    sys.stderr.write('time="2026-11-17T22:20:33Z" level=warning msg="volume x_backup-unset'
                     ' already exists but was not created by Docker Compose"\\n')
    sys.stderr.write('time="2026-11-17T22:20:34Z" level=info msg="noise of: '
                     + joined[-120:] + '"\\n')
    if wrapper_env.get("BRIEVIE_LOG_LEVEL") != "WARNING":
        sys.stderr.write("2026-11-17 22:20:33,614 INFO procrastinate.jobs Deferred 1 job\\n")
if fail and fail in joined:
    sys.stderr.write(os.environ.get("FAKE_DOCKER_FAIL_STDERR", ""))
    sys.exit(int(os.environ.get("FAKE_DOCKER_FAIL_CODE", "1")))

running = os.environ.get("FAKE_DOCKER_RUNNING", "").split()
if argv[:2] == ["ps", "-a"]:
    template = argv[argv.index("--format") + 1]
    label = '{{{{.Label "com.docker.compose.project.working_dir"}}}}'
    owners = [o for o in os.environ.get("FAKE_DOCKER_OWNERS", "").split("|") if o]
    for owner in owners:
        sys.stdout.write(template.replace(label, "" if owner == "<empty>" else owner) + "\\n")
    sys.exit(0)
if os.path.exists(gone_marker):
    running = [name for name in running if name != "keeper"]
if argv[:1] == ["compose"] and "ps" in argv:
    if "--services" in argv:
        sys.stdout.write("".join(name + "\\n" for name in running))
    elif "--quiet" in argv and argv[-1] in running:
        sys.stdout.write("0123abcd\\n")
    sys.exit(0)
if argv[:2] == ["volume", "inspect"]:
    name = argv[-1]
    if name not in os.environ.get("FAKE_DOCKER_VOLUMES", "").split():
        sys.exit(1)
    if "--format" in argv:
        pairs = os.environ.get("FAKE_VOLUME_LABELS", "").split()
        labels = dict(pair.split("=", 1) for pair in pairs)
        sys.stdout.write(labels.get(name, "") + "\\n")
    sys.exit(0)
if "derive_backup_password" in argv:
    sys.stdout.write(os.environ["FAKE_DERIVED_PASSWORD"] + "\\n")
elif "token_hex(16)" in joined:
    sys.stdout.write(os.environ.get("FAKE_INSTALLATION_ID", "{new_id}") + "\\n")
elif "installation-id.tmp" in joined:
    pass
elif "/data/backup-config/installation-id" in joined:
    if "FAKE_VOLUME_RAW" in os.environ:
        sys.stdout.write(os.environ["FAKE_VOLUME_RAW"])
    elif os.environ.get("FAKE_VOLUME_ID"):
        sys.stdout.write("ID=" + os.environ["FAKE_VOLUME_ID"])
    else:
        sys.stdout.write("NONE\\n")
elif "-c" in argv and "secrets" in joined:
    sys.stdout.write(os.environ["FAKE_SEPARATE_PASSWORD"] + "\\n")
elif "recovery_sheet" in argv:
    sys.stdout.write("<html>herstelblad</html>\\n")
elif "restore_database" in argv:
    sys.stdout.write("interrupted_runs=1\\n")
elif "verify_restore" in argv:
    sys.stdout.write("workspaces=2\\npages_opened=2\\n")
    sys.stdout.write("Het herstel is gecontroleerd. Alles klopt.\\n")
elif "backup_status" in argv:
    sys.stdout.write("Brievie: 1.4.0\\nBestemming: map (schijf)\\n")
elif "manage.py" in argv and argv[-1] == "--safety":
    sys.stdout.write("safety_run_id={safety_id}\\n")
    sys.stdout.write("Back-up gestart…\\nDe back-up is gelukt (1 MB).\\n")
elif "manage.py" in argv and argv[-1] in ("backup", "--check"):
    sys.stdout.write("Back-up gestart…\\nDe back-up is gelukt (1,4 MB).\\n")
elif "psql" in argv and "accounts_user" in entry.get("stdin", ""):
    sys.stdout.write(os.environ.get("FAKE_DATA_ROWS", "5") + "\\n")
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
        docker.write_text(
            FAKE_DOCKER.format(
                python=sys.executable, run_id=RUN_ID, new_id=NEW_ID, safety_id=SAFETY_ID
            )
        )
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
        "BRIEVIE_ADOPT_INSTALLATION": "JA",
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


def services_touched(install: Install) -> tuple[list[str], list[str]]:
    """The services the logged `compose up` calls created and recreated, in order."""
    created: list[str] = []
    recreated: list[str] = []
    for call in install.calls():
        created += call.get("created", [])
        recreated += call.get("recreated", [])
    return created, recreated


def test_a_first_start_creates_caddy_with_the_rest(installed: Install) -> None:
    result = installed.run("start")

    assert result.returncode == 0, result.stderr
    created, recreated = services_touched(installed)
    assert {"postgres", "migrate", "api", "worker", "keeper", "caddy"} <= set(created)
    assert recreated == []


def test_a_start_with_the_same_caddy_configuration_recreates_nothing(installed: Install) -> None:
    assert installed.run("start").returncode == 0
    installed.log.unlink()

    result = installed.run("start")

    assert result.returncode == 0, result.stderr
    assert services_touched(installed) == ([], [])


@pytest.mark.parametrize("name", ["Caddyfile", "caddy-publish-root.sh"])
def test_a_changed_caddy_configuration_recreates_caddy_alone(installed: Install, name: str) -> None:
    assert installed.run("start").returncode == 0
    installed.log.unlink()
    # What `git pull` does to a file Caddy reads only when it starts.
    config = installed.root / "deploy" / name
    config.write_text(config.read_text() + "# a newer version\n")

    result = installed.run("start")

    assert result.returncode == 0, result.stderr
    assert services_touched(installed) == ([], ["caddy"])
    # Applied by compose's own comparison: never a restart that runs on every start.
    assert not any(
        "restart" in call["argv"] or "--force-recreate" in call["argv"]
        for call in installed.calls()
    )
    assert "a newer version" not in result.stdout + result.stderr


def test_an_older_installation_gets_its_caddy_recreated_once(installed: Install) -> None:
    # The stack as an older ./brievie started it: compose without the fingerprint.
    deploy = installed.root / "deploy"
    older_up = subprocess.run(  # noqa: S603
        [
            str(installed.bin / "docker"),
            "compose",
            "-p",
            "brievie",
            "--project-directory",
            str(deploy),
            "-f",
            str(deploy / "docker-compose.yml"),
            "up",
            "-d",
            "--build",
            "--wait",
        ],
        env={**installed.env, "BRIEVIE_IMAGE": "brievie/backend:local"},
        check=False,
    )
    assert older_up.returncode == 0
    installed.log.unlink()

    assert installed.run("start").returncode == 0
    assert services_touched(installed) == ([], ["caddy"])
    installed.log.unlink()

    assert installed.run("start").returncode == 0
    assert services_touched(installed) == ([], [])


def test_the_restore_starts_caddy_with_the_current_configuration(installed: Install) -> None:
    assert installed.run("start").returncode == 0
    caddyfile = installed.root / "deploy" / "Caddyfile"
    caddyfile.write_text(caddyfile.read_text() + "# a newer version\n")

    result = installed.run("restore", "--replace", **restore_inputs(installed))

    assert result.returncode == 0, result.stderr
    # The restore stops caddy and brings it up with the configuration as it is now ...
    assert installed.index("stop api worker caddy") < installed.index("up -d --wait")
    installed.log.unlink()
    # ... so the next start finds nothing left to apply.
    assert installed.run("start").returncode == 0
    assert services_touched(installed) == ([], [])


LOOPBACK_WARNING = (
    "Let op: met BRIEVIE_HOSTNAME={host} kan een iPhone deze server niet bereiken; zet in "
    "deploy/.env de naam of het IP-adres waarmee je telefoon deze computer in je thuisnetwerk "
    "vindt (zoals mijnmac.local of 192.168.1.20) en typ opnieuw ./brievie start."
)


@pytest.mark.parametrize(
    "host", ["localhost", "LocalHost", "127.0.0.1", "127.1.2.3", "::1", "[::1]", "app.localhost"]
)
def test_start_with_a_loopback_host_name_says_a_phone_cannot_reach_it(
    installed: Install, host: str
) -> None:
    installed.write_env(
        current_env().replace("BRIEVIE_HOSTNAME=brievie.local", f"BRIEVIE_HOSTNAME={host}")
    )

    result = installed.run("start")

    # A developer may want it: the start goes on.
    assert result.returncode == 0, result.stderr
    assert installed.index("up -d --build --wait") >= 0
    lines = result.stdout.splitlines()
    address = next(line for line in lines if line.startswith("Brievie draait op"))
    assert address.endswith(", alleen in een browser op deze computer.")
    assert lines[lines.index(address) + 1] == LOOPBACK_WARNING.format(host=host)


@pytest.mark.parametrize("host", ["brievie.local", "192.168.1.20", "localhost.example.nl", "nas"])
def test_start_with_a_network_host_name_gives_no_warning(installed: Install, host: str) -> None:
    installed.write_env(
        current_env().replace("BRIEVIE_HOSTNAME=brievie.local", f"BRIEVIE_HOSTNAME={host}")
    )

    result = installed.run("start")

    assert result.returncode == 0, result.stderr
    assert "iPhone" not in result.stdout + result.stderr
    assert f"Brievie draait op https://{host}." in result.stdout


def test_a_hand_install_is_told_its_phone_cannot_pair(install: Install) -> None:
    # git clone + ./brievie start: deploy/.env comes from the example.
    result = install.run("start")

    assert result.returncode == 0, result.stderr
    assert LOOPBACK_WARNING.format(host="localhost") in result.stdout


def test_a_host_name_given_at_the_first_start_lands_in_the_new_settings(install: Install) -> None:
    result = install.run("start", BRIEVIE_HOSTNAME="mijnnas.local")

    assert result.returncode == 0, result.stderr
    assert setting(install.dotenv.read_text(), "BRIEVIE_HOSTNAME") == [
        "BRIEVIE_HOSTNAME=mijnnas.local"
    ]
    assert "iPhone" not in result.stdout
    assert "Brievie draait op https://mijnnas.local." in result.stdout


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
    # A new machine is a new installation: the back-up's id is dropped, a new one added.
    assert install.dotenv.read_text() == (
        without(restored_env(), *DESTINATION_KEYS, "BRIEVIE_INSTALLATION_ID")
        + f"BRIEVIE_INSTALLATION_ID={NEW_ID}\n"
    )
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
        if any(n in call["argv"] for n in ("restore_check", "restore_database", "restore_files")):
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
    # Nothing was here before, so no safety back-up is needed on the next attempt.
    assert f"./brievie restore {RUN_ID} --replace --skip-safety-backup" in result.stderr
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
    # (The read-only count of an installation's data runs as `psql … -d brievie`.)
    assert not any(
        " stop " in f" {c} " or "psql -U postgres -d postgres" in c for c in installed.commands()
    )
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
    assert not any(
        " stop " in f" {c} " or "psql -U postgres -d postgres" in c for c in installed.commands()
    )
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
    # The one command inside a container: fixed paths in the back-up configuration volume.
    container_mv = "mv /data/backup-config/installation-id.tmp /data/backup-config/installation-id'"
    for match in re.finditer(r"(?<![-\w])(rm|mv|mkdir) [^\n]*", code):
        command = match.group(0)
        if command == container_mv:
            continue
        assert '"${here:?}/' in command, command
        if command.startswith("rm -r"):
            assert command == 'rm -rf "${here:?}/deploy/.restore"', command
    # A redirect to a file names a fixed path under the script's folder.
    for match in re.finditer(r'(?<![<>&\d])>>?"?(\S+)', code):
        target = match.group(1)
        # &9 is the terminal's stderr, saved at the start (exec 9>&2).
        assert target.startswith(("${here:?}/", "&2", "&9", "/dev/null")), target
    assert 'rm -rf "$' not in code.replace('rm -rf "${here:?}/deploy/.restore"', "")


def test_shellcheck_when_available() -> None:
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        if os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail("shellcheck must be installed in CI")
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
        c.get("stdin", "") for c in install.calls() if "installation-id.tmp" in " ".join(c["argv"])
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
    check = installed.index("UNREADABLE")
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


# --- fix round 3: a volume as source, and the last ownership gaps --------------------------


def volume_inputs(source: str, **extra: str) -> dict[str, str]:
    return {
        "BRIEVIE_RESTORE_SOURCE": source,
        "BRIEVIE_RESTORE_MASTER_KEY": MASTER,
        "BRIEVIE_RESTORE_CONFIRM": "WISSEN",
        "BRIEVIE_ADOPT_INSTALLATION": "JA",
        **extra,
    }


@pytest.mark.parametrize("source", ["volume:backup-unset", "volume:brievie_backup-unset"])
def test_a_volume_of_the_project_is_a_restore_source(install: Install, source: str) -> None:
    result = install.run(
        "restore",
        "--keep-hostname",
        FAKE_DOCKER_VOLUMES="brievie_backup-unset",
        FAKE_VOLUME_LABELS="brievie_backup-unset=brievie",
        **volume_inputs(source, BRIEVIE_RESTORE_KEEP_DESTINATION="ja"),
    )

    assert result.returncode == 0, result.stderr
    fetch = next(c for c in install.calls() if "restore_fetch" in c["argv"])
    assert "brievie_backup-unset:/backup:ro" in fetch["argv"]
    assert fetch["env"]["BRIEVIE_BACKUP_DIR_SET"] == "true"
    assert "--backup-dir-host" not in fetch["argv"]
    for name in ("restore_database", "restore_files"):
        call = next(c for c in install.calls() if name in c["argv"])
        assert call["backup_dir_env"] == "backup-unset"
    assert setting(install.dotenv.read_text(), "BRIEVIE_BACKUP_DIR") == [
        "BRIEVIE_BACKUP_DIR=backup-unset"
    ]


@pytest.mark.parametrize(
    ("source", "volumes", "labels"),
    [
        # a volume of another project, by its full name
        ("volume:live_backup-unset", "live_backup-unset", "live_backup-unset=live"),
        # the right name, but compose says it belongs to another project
        ("volume:backup-unset", "brievie_backup-unset", "brievie_backup-unset=live"),
        # no such volume
        ("volume:backup-unset", "", ""),
        # the installation's own data
        ("volume:pgdata", "brievie_pgdata", ""),
        ("volume:documents", "brievie_documents", ""),
        # characters outside [a-zA-Z0-9_.-]
        ("volume:../backup", "", ""),
        ("volume:a b", "", ""),
        ("volume:x:/etc", "", ""),
        ("volume:", "", ""),
    ],
)
def test_other_volumes_are_refused_as_source(
    install: Install, source: str, volumes: str, labels: str
) -> None:
    result = install.run(
        "restore",
        "--keep-hostname",
        FAKE_DOCKER_VOLUMES=volumes,
        FAKE_VOLUME_LABELS=labels,
        **volume_inputs(source),
    )

    assert result.returncode == 1
    assert not any("restore_fetch" in c for c in install.commands())
    assert not any(c.startswith("compose") for c in install.commands())
    assert not install.dotenv.exists()


def test_the_id_is_written_as_the_app_user_without_following_a_link(installed: Install) -> None:
    result = installed.run("start")

    assert result.returncode == 0, result.stderr
    write = next(c for c in installed.calls() if "installation-id.tmp" in " ".join(c["argv"]))
    argv = write["argv"]
    assert argv[argv.index("--user") + 1] == "10001"
    script = argv[-1]
    assert "umask 077" in script
    assert "[ ! -L /data/backup-config/installation-id ]" in script
    assert (
        "mv /data/backup-config/installation-id.tmp /data/backup-config/installation-id" in script
    )
    read = next(c for c in installed.calls() if "UNREADABLE" in " ".join(c["argv"]))
    assert read["argv"][read["argv"].index("--user") + 1] == "10001"


@pytest.mark.parametrize(
    "raw", ["LINK\n", "UNREADABLE\n", "ID=xyz\n", "ID=" + "C0" * 16, "ID=" + "c0" * 17, "huh\n", ""]
)
def test_a_strange_id_in_the_volume_is_refused(installed: Install, raw: str) -> None:
    result = installed.run("start", FAKE_VOLUME_RAW=raw)

    assert result.returncode == 1
    assert not any("up -d" in c for c in installed.commands())
    assert id_writes(installed) == []


def test_an_admin_container_that_cannot_start_is_refused(installed: Install) -> None:
    result = installed.run("start", FAKE_DOCKER_FAIL="UNREADABLE")

    assert result.returncode == 1
    assert not any("up -d" in c for c in installed.commands())


def test_old_data_without_an_id_needs_a_typed_yes(installed: Install) -> None:
    refused = installed.run("start", FAKE_DOCKER_VOLUMES="brievie_pgdata", stdin="JA\n")

    assert refused.returncode == 1
    assert "BRIEVIE_ADOPT_INSTALLATION=JA" in refused.stderr
    assert "nog geen kenmerk" in refused.stdout + refused.stderr
    assert str(installed.root) in refused.stdout + refused.stderr
    assert not any("up -d" in c for c in installed.commands())
    assert id_writes(installed) == []

    adopted = installed.run(
        "start", FAKE_DOCKER_VOLUMES="brievie_pgdata", BRIEVIE_ADOPT_INSTALLATION="JA"
    )

    assert adopted.returncode == 0, adopted.stderr
    assert id_writes(installed) == [CURRENT_ID + "\n"]


def test_a_new_installation_needs_no_typed_yes(installed: Install) -> None:
    result = installed.run("start")

    assert result.returncode == 0, result.stderr
    assert id_writes(installed) == [CURRENT_ID + "\n"]


def test_replace_of_data_without_an_id_needs_a_typed_yes(installed: Install) -> None:
    result = installed.run(
        "restore",
        "--replace",
        "--keep-hostname",
        **restore_inputs(installed, BRIEVIE_ADOPT_INSTALLATION=""),
    )

    assert result.returncode == 1
    assert "BRIEVIE_ADOPT_INSTALLATION=JA" in result.stderr
    assert not any(" stop " in f" {c} " for c in installed.commands())


def test_without_an_image_start_builds_then_checks_then_starts(installed: Install) -> None:
    result = installed.run("start", FAKE_DOCKER_FAIL="image inspect")

    assert result.returncode == 0, result.stderr
    build = installed.index(" build")
    check = installed.index("UNREADABLE")
    up = installed.index("up -d --build --wait")
    assert build < check < up


def test_the_default_image_tag_is_refused_in_another_project(installed: Install) -> None:
    result = installed.run(
        "status", COMPOSE_PROJECT_NAME="thuis", BRIEVIE_IMAGE="brievie/backend:local"
    )

    assert result.returncode == 1
    assert "brievie/backend:local" in result.stderr
    assert installed.calls() == []


def test_the_restored_env_loses_docker_settings_and_follows_the_port(installed: Install) -> None:
    installed.write_env(current_env() + "POSTGRES_PORT=5433\n")
    installed.restored.write_text(
        restored_env()
        + "COMPOSE_FILE=/elders/compose.yml\n"
        + "export COMPOSE_PROFILES=admin\n"
        + "DOCKER_HOST=tcp://elders:2375\n"
        + "  DOCKER_CONTEXT = ander\n"
        + f"DATABASE_URL=postgres://brievie_app:{APP_PW}@localhost:5432/brievie\n"
        + f"DATABASE_BACKUP_URL=postgres://brievie_backup:{BACKUP_DB_PW}@localhost/brievie\n"
    )

    result = installed.run("restore", "--replace", "--keep-hostname", **restore_inputs(installed))

    assert result.returncode == 0, result.stderr
    text = installed.dotenv.read_text()
    for gone in ("COMPOSE_FILE", "COMPOSE_PROFILES", "DOCKER_HOST", "DOCKER_CONTEXT"):
        assert gone not in text
    assert setting(text, "DATABASE_URL") == [
        f"DATABASE_URL=postgres://brievie_app:{APP_PW}@localhost:5433/brievie"
    ]
    assert setting(text, "DATABASE_BACKUP_URL") == [
        f"DATABASE_BACKUP_URL=postgres://brievie_backup:{BACKUP_DB_PW}@localhost:5433/brievie"
    ]
    assert setting(text, "DATABASE_OWNER_URL") == [
        f"DATABASE_OWNER_URL=postgres://brievie_owner:{NEW_OWNER_PW}@localhost:5433/brievie"
    ]
    installed.assert_no_secret_in_argv()


def test_a_fresh_restore_takes_the_host_ports_from_the_environment(install: Install) -> None:
    result = install.run(
        "restore",
        "--keep-hostname",
        POSTGRES_PORT="5434",
        BRIEVIE_HTTP_PORT="8481",
        BRIEVIE_HTTPS_PORT="8444",
        **restore_inputs(install),
    )

    assert result.returncode == 0, result.stderr
    text = install.dotenv.read_text()
    assert setting(text, "POSTGRES_PORT") == ["POSTGRES_PORT=5434"]
    assert setting(text, "BRIEVIE_HTTP_PORT") == ["BRIEVIE_HTTP_PORT=8481"]
    assert setting(text, "BRIEVIE_HTTPS_PORT") == ["BRIEVIE_HTTPS_PORT=8444"]
    assert setting(text, "DATABASE_OWNER_URL") == [
        f"DATABASE_OWNER_URL=postgres://brievie_owner:{NEW_OWNER_PW}@localhost:5434/brievie"
    ]


def test_halfway_after_a_safety_backup_names_both_commands(installed: Install) -> None:
    result = installed.run(
        "restore",
        "--replace",
        "--keep-hostname",
        FAKE_DOCKER_FAIL="restore_files",
        **restore_inputs(installed),
    )

    assert result.returncode == 1
    assert f"./brievie restore {RUN_ID} --replace --skip-safety-backup" in result.stderr
    assert f"./brievie restore {SAFETY_ID} --replace --skip-safety-backup" in result.stderr
    assert "veiligheidsback-up" in result.stderr


def test_ensure_backup_role_script_follows_the_project(installed: Install) -> None:
    installed.write_env(current_env(project="tweede"))

    result = subprocess.run(
        ["/bin/sh", "deploy/ensure-backup-role.sh"],
        cwd=installed.root,
        capture_output=True,
        text=True,
        env={**installed.env, "FAKE_DOCKER_RUNNING": "postgres"},
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert compose_projects(installed) == {"tweede"}


def test_ensure_backup_role_script_defaults_to_brievie(installed: Install) -> None:
    result = subprocess.run(
        ["/bin/sh", "deploy/ensure-backup-role.sh"],
        cwd=installed.root,
        capture_output=True,
        text=True,
        env={**installed.env, "FAKE_DOCKER_RUNNING": "postgres"},
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert compose_projects(installed) == {"brievie"}


@pytest.mark.parametrize(
    ("env_line", "environment", "expected"),
    [
        ("COMPOSE_PROJECT_NAME=tweede\n", {}, "tweede"),
        ("", {}, "brievie"),
        ("COMPOSE_PROJECT_NAME=tweede\n", {"COMPOSE_PROJECT_NAME": "derde"}, "derde"),
    ],
)
def test_make_passes_the_project(
    installed: Install, env_line: str, environment: dict[str, str], expected: str
) -> None:
    make = shutil.which("make")
    if make is None:
        pytest.skip("make is not installed")
    shutil.copy(REPO / "Makefile", installed.root / "Makefile")
    installed.write_env(current_env() + env_line)

    # -n prints the commands without running them.
    result = subprocess.run(  # noqa: S603
        [make, "-n", "dev"],
        cwd=installed.root,
        capture_output=True,
        text=True,
        env={**installed.env, **environment},
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert f"docker compose -p {expected} --project-directory deploy" in result.stdout


# --- fix round 4: the back-up is read read-only while restoring ----------------------------


READ_ONLY_STEPS = ("restore_check", "restore_database", "restore_files", "verify_restore")


@pytest.mark.parametrize("flags", [(), ("--replace",)])
def test_only_the_restore_steps_mount_the_source_read_only(
    installed: Install, flags: tuple[str, ...]
) -> None:
    if not flags:
        installed.dotenv.unlink()

    result = installed.run(
        "restore",
        *flags,
        "--keep-hostname",
        BRIEVIE_BACKUP_MOUNT_MODE="rw",  # a stray value in the shell never counts
        **restore_inputs(installed),
    )

    assert result.returncode == 0, result.stderr
    compose_calls = [c for c in installed.calls() if c["argv"][0] == "compose"]
    for call in compose_calls:
        read_only = any(step in call["argv"] for step in READ_ONLY_STEPS)
        assert call["mount_mode_env"] == ("ro" if read_only else "<unset>"), call["argv"]
    assert sum(any(s in c["argv"] for s in READ_ONLY_STEPS) for c in compose_calls) == 4


def test_backup_init_keeps_the_default_mount(installed: Install) -> None:
    result = installed.run("backup", "--init", BRIEVIE_BACKUP_MOUNT_MODE="ro")

    assert result.returncode == 0, result.stderr
    init = next(c for c in installed.calls() if "backup_init" in c["argv"])
    assert init["mount_mode_env"] == "<unset>"


def test_a_fresh_restore_checks_no_id_before_postgres(install: Install) -> None:
    result = install.run("restore", "--keep-hostname", **restore_inputs(install))

    assert result.returncode == 0, result.stderr
    postgres = install.index("up -d --wait postgres")
    commands = install.commands()
    # The data volume is looked for right before postgres starts; there is none, so no
    # id is read (a volume without deploy/.env was refused at the start).
    assert "volume inspect brievie_pgdata" in commands[postgres - 1]
    assert not any("UNREADABLE" in c for c in commands[:postgres])


def test_the_images_own_sentence_comes_before_the_next_command(install: Install) -> None:
    sentence = "Er staat al een snelkoppeling in de doelmap; gebruik --replace."
    result = install.run(
        "restore",
        "--keep-hostname",
        FAKE_DOCKER_FAIL="restore_files",
        FAKE_DOCKER_FAIL_STDERR=f"CommandError: {sentence}\n",
        **restore_inputs(install),
    )

    assert result.returncode == 1
    stderr = result.stderr
    assert sentence in stderr
    assert stderr.index(sentence) < stderr.index(f"./brievie restore {RUN_ID} --replace")
    assert "niet compleet" not in stderr


# --- fix round 5: what the user sees ------------------------------------------------------


NOISE = (" Container ", "level=warning", " INFO ", "CommandError:", "noise of:", "Network ")
MACHINE_LINE = re.compile(r"^\s*[a-z_]+=\S", re.MULTILINE)


def assert_clean(text: str) -> None:
    for noise in NOISE:
        assert noise not in text, noise
    assert not MACHINE_LINE.search(text), MACHINE_LINE.search(text)


def test_start_shows_only_sentences(installed: Install) -> None:
    result = installed.run("start", FAKE_NOISE="1")

    assert result.returncode == 0, result.stderr
    assert_clean(result.stdout + result.stderr)
    assert "Brievie wordt gestart" in result.stdout


def test_backup_and_status_show_only_sentences(installed: Install) -> None:
    backup = installed.run("backup", FAKE_NOISE="1")
    status = installed.run("status", FAKE_NOISE="1")

    assert backup.returncode == 0, backup.stderr
    assert status.returncode == 0, status.stderr
    assert_clean(backup.stdout + backup.stderr)
    assert_clean(status.stdout + status.stderr)
    assert "De back-up is gelukt (1,4 MB)." in backup.stdout
    assert "Bestemming: map (schijf)" in status.stdout


def test_a_fresh_restore_shows_only_sentences(install: Install) -> None:
    result = install.run("restore", "--keep-hostname", FAKE_NOISE="1", **restore_inputs(install))

    assert result.returncode == 0, result.stderr
    assert_clean(result.stdout + result.stderr)
    assert "Het herstel is gecontroleerd: 2 werkruimtes, van elk is een pagina geopend." in (
        result.stdout
    )
    assert "Alles klopt" not in result.stdout
    assert result.stdout.rstrip().splitlines()[-1] == "Herstel gelukt."


@pytest.mark.parametrize(
    ("code", "sentence", "exit_code", "next_step"),
    [
        (
            "destination_not_ready",
            "De bestemming voor de back-up is nog niet klaar voor gebruik.",
            "1",
            "De plek voor de back-up is niet klaar. Is de schijf aangesloten? "
            "Is dit de eerste keer: typ ./brievie backup --init.",
        ),
        (
            "not_configured",
            "Er is nog geen back-up ingesteld.",
            "1",
            "Zet eerst in deploy/.env waar de back-up heen moet (zie docs/SELFHOST.md) "
            "en typ daarna ./brievie backup --init.",
        ),
        (
            "not_picked_up",
            "De back-up is niet opgepakt. Draait de back-updienst (keeper)?",
            "2",
            "./brievie status",
        ),
    ],
)
def test_a_failing_backup_says_what_to_type(
    installed: Install, code: str, sentence: str, exit_code: str, next_step: str
) -> None:
    result = installed.run(
        "backup",
        FAKE_NOISE="1",
        FAKE_DOCKER_FAIL="manage.py backup",
        FAKE_DOCKER_FAIL_CODE=exit_code,
        FAKE_DOCKER_FAIL_STDERR=f"code={code}\nCommandError: {sentence}\n",
    )

    assert result.returncode == int(exit_code)
    out = result.stdout + result.stderr
    assert sentence in result.stderr
    assert next_step in result.stderr
    assert result.stderr.index(sentence) < result.stderr.index(next_step)
    assert "De laatste regels van de techniek:" in result.stderr
    assert "deploy/.brievie-last.log" in result.stderr
    assert "CommandError:" not in out
    assert "code=" not in out
    assert "Dat is niet gelukt" not in out


def test_a_failing_init_says_what_to_type(installed: Install) -> None:
    result = installed.run(
        "backup",
        "--init",
        FAKE_DOCKER_FAIL="backup_init",
        FAKE_DOCKER_FAIL_STDERR=(
            "code=not_configured\nCommandError: Er is nog geen back-up ingesteld.\n"
        ),
    )

    assert result.returncode == 1
    assert "Er is nog geen back-up ingesteld." in result.stderr
    assert "docs/SELFHOST.md" in result.stderr
    assert "CommandError:" not in result.stderr


def test_verbose_shows_everything(installed: Install) -> None:
    result = installed.run("status", FAKE_NOISE="1", BRIEVIE_VERBOSE="1")

    assert result.returncode == 0, result.stderr
    assert " Container " in result.stderr
    assert "level=warning" in result.stderr


def test_the_log_is_private_and_holds_the_last_command_only(installed: Install) -> None:
    log = installed.root / "deploy" / ".brievie-last.log"
    installed.run("status", FAKE_NOISE="1")
    installed.run("sweep", FAKE_NOISE="1")

    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    text = log.read_text()
    assert "sweep_storage" in text
    assert "backup_status" not in text
    assert " Container " in text
    assert not (installed.root / "deploy" / ".brievie-step.log").exists()


def test_management_commands_run_quietly_and_services_do_not(installed: Install) -> None:
    result = installed.run("start", FAKE_NOISE="1")

    assert result.returncode == 0, result.stderr
    for call in installed.calls():
        argv = call["argv"]
        if "manage.py" in argv:
            assert call.get("wrapper_env") == {
                "BRIEVIE_WRAPPER": "1",
                "BRIEVIE_LOG_LEVEL": "WARNING",
            }, argv
        if "up" in argv:
            assert "wrapper_env" not in call


def test_start_ends_with_address_backup_and_sheet(installed: Install) -> None:
    installed.write_env(current_env() + "BRIEVIE_HTTPS_PORT=8443\n")
    (installed.root / "brievie-herstelblad.html").write_text("old")

    result = installed.run("start")

    assert result.returncode == 0, result.stderr
    last = result.stdout.rstrip().splitlines()[-3:]
    assert last[0] == "Brievie draait op https://brievie.local:8443."
    assert last[1].startswith("Back-up: ingesteld")
    assert last[2].startswith("Herstelblad: brievie-herstelblad.html")
    assert "druk het af" in last[2].lower()
    assert "verwijder" in last[2].lower()


def test_start_without_a_destination_names_the_one_command(installed: Install) -> None:
    installed.write_env(current_env().replace("BRIEVIE_BACKUP_DIR=/Volumes/Backup/brievie\n", ""))

    result = installed.run("start")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.rstrip().splitlines()
    assert lines[-3] == "Brievie draait op https://brievie.local."
    assert lines[-2].startswith("Back-up: nog niet ingesteld")
    assert "./brievie backup --init" in lines[-2]


# --- fix round 6: a restore checks its back-up again before it drops anything -----------

RUN_NOT_FOUND = (
    "Deze back-up is niet gevonden. Typ ./brievie status --list om te zien welke back-ups er zijn."
)


def test_replace_checks_the_backup_after_the_safety_backup_and_before_the_drop(
    installed: Install,
) -> None:
    prepared = installed.root / "deploy" / ".restore" / "env"
    result = installed.run(
        "restore",
        "--replace",
        "--keep-hostname",
        FAKE_WATCH=str(prepared),
        **restore_inputs(installed),
    )

    assert result.returncode == 0, result.stderr
    safety = installed.index("--safety")
    check = installed.index("restore_check")
    drop = installed.index("psql -U postgres -d postgres")
    database = installed.index("restore_database")
    assert safety < check < drop < database
    call = installed.calls()[check]
    assert call["argv"][-2:] == ["--run", RUN_ID]
    # Before the swap: the prepared settings were still waiting in deploy/.restore.
    assert "watch_mode" in call
    assert call["mount_mode_env"] == "ro"
    assert call["backup_dir_env"] == str(installed.backup_folder)
    assert call["secret_env"] == ["BRIEVIE_BACKUP_PASSWORD"]


def test_a_vanished_backup_stops_the_replace_before_anything_is_destroyed(
    installed: Install,
) -> None:
    result = installed.run(
        "restore",
        "--replace",
        "--keep-hostname",
        FAKE_DOCKER_FAIL="restore_check",
        FAKE_DOCKER_FAIL_STDERR=f"CommandError: {RUN_NOT_FOUND}\n",
        **restore_inputs(installed),
    )

    assert result.returncode == 1
    assert RUN_NOT_FOUND in result.stderr
    assert "Er is niets vervangen" in result.stderr
    assert "CommandError:" not in result.stderr
    commands = installed.commands()
    assert not any("psql -U postgres -d postgres" in c for c in commands)
    assert not any("restore_database" in c or "restore_files" in c for c in commands)
    assert installed.dotenv.read_text() == current_env()
    assert list((installed.root / "deploy").glob(".env.before-restore-*")) == []
    assert not (installed.root / "deploy" / ".restore").exists()
    # The services that were stopped run again.
    check = installed.index("restore_check")
    assert any(c.endswith("up -d") for c in commands[check:])


def test_a_vanished_backup_stops_a_fresh_restore_before_postgres(install: Install) -> None:
    result = install.run(
        "restore",
        "--keep-hostname",
        FAKE_DOCKER_FAIL="restore_check",
        FAKE_DOCKER_FAIL_STDERR=f"CommandError: {RUN_NOT_FOUND}\n",
        **restore_inputs(install),
    )

    assert result.returncode == 1
    assert "./brievie restore" in result.stderr
    assert not install.dotenv.exists()
    assert not any("postgres" in c and "up -d" in c for c in install.commands())
    assert not (install.root / "deploy" / ".restore").exists()


def test_a_backup_gone_after_the_drop_says_so_honestly(installed: Install) -> None:
    result = installed.run(
        "restore",
        "--replace",
        "--keep-hostname",
        FAKE_DOCKER_FAIL="restore_database",
        FAKE_DOCKER_FAIL_STDERR=f"CommandError: {RUN_NOT_FOUND}\n",
        **restore_inputs(installed),
    )

    assert result.returncode == 1
    stderr = result.stderr
    assert "./brievie status --list" in stderr
    assert "./brievie restore <ander nummer> --replace --skip-safety-backup" in stderr
    assert "de oude database is al leeggemaakt" in stderr
    assert "veiligheidsback-up" in stderr


def test_a_backup_gone_on_a_new_machine_says_nothing_was_lost(install: Install) -> None:
    result = install.run(
        "restore",
        "--keep-hostname",
        FAKE_DOCKER_FAIL="restore_database",
        FAKE_DOCKER_FAIL_STDERR=f"CommandError: {RUN_NOT_FOUND}\n",
        **restore_inputs(install),
    )

    assert result.returncode == 1
    assert "./brievie status --list" in result.stderr
    assert "leeggemaakt" not in result.stderr
    assert "hier stonden nog geen gegevens" in result.stderr.lower()


def test_status_list_shows_the_runs_without_the_keeper(installed: Install) -> None:
    result = installed.run("status", "--list", FAKE_DOCKER_RUNNING="postgres")

    assert result.returncode == 0, result.stderr
    call = next(c for c in installed.calls() if "backup_status" in c["argv"])
    assert call["argv"][-2:] == ["backup_status", "--list"]
    assert "admin" in call["argv"]
    assert call["mount_mode_env"] == "ro"


def test_help_mentions_status_list(installed: Install) -> None:
    result = installed.run("help")

    lines = [line for line in result.stdout.splitlines() if "./brievie status --list" in line]
    assert len(lines) == 1


def test_status_takes_no_other_word(installed: Install) -> None:
    result = installed.run("status", "--alles")

    assert result.returncode == 2
    assert installed.calls() == []


def test_s3_credentials_reach_the_fetch_and_every_restore_step(install: Install) -> None:
    result = install.run(
        "restore",
        "--keep-hostname",
        BRIEVIE_RESTORE_SOURCE="s3:https://s3.example.nl/bak/brievie",
        BRIEVIE_RESTORE_MASTER_KEY=MASTER,
        BRIEVIE_BACKUP_S3_ACCESS_KEY_ID="AKIAEXAMPLE",
        BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY=S3_SECRET,
    )

    assert result.returncode == 0, result.stderr
    fetch = next(c for c in install.calls() if "restore_fetch" in c["argv"])
    assert fetch["env"]["BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY"] == S3_SECRET
    for name in ("restore_check", "restore_database", "restore_files"):
        call = next(c for c in install.calls() if name in c["argv"])
        assert call["source_env"] == [
            "BRIEVIE_BACKUP_S3_ACCESS_KEY_ID",
            "BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY",
            "BRIEVIE_BACKUP_REPOSITORY",
        ], name
    # And no other compose call sees them.
    for call in install.calls():
        if call["argv"][0] == "compose" and not any(
            n in call["argv"] for n in ("restore_check", "restore_database", "restore_files")
        ):
            assert call["source_env"] == [], call["argv"]
    install.assert_no_secret_in_argv()


def test_typed_rest_credentials_reach_the_restore_steps(install: Install) -> None:
    result = install.run(
        "restore",
        "--keep-hostname",
        stdin="gebruiker\nrest-wachtwoord-77\n",
        BRIEVIE_RESTORE_SOURCE="rest:https://rest.example.nl/brievie",
        BRIEVIE_RESTORE_MASTER_KEY=MASTER,
    )

    assert result.returncode == 0, result.stderr
    call = next(c for c in install.calls() if "restore_database" in c["argv"])
    assert call["source_env"] == [
        "BRIEVIE_BACKUP_REST_USERNAME",
        "BRIEVIE_BACKUP_REST_PASSWORD",
        "BRIEVIE_BACKUP_REPOSITORY",
    ]
    assert not any("rest-wachtwoord-77" in " ".join(c["argv"]) for c in install.calls())


def test_a_known_installation_gets_no_new_sheet_on_start(installed: Install) -> None:
    result = installed.run("start")

    assert result.returncode == 0, result.stderr
    assert not (installed.root / "brievie-herstelblad.html").exists()
    assert not any("recovery_sheet" in c for c in installed.commands())
    assert "./brievie recovery-sheet" in result.stdout.rstrip().splitlines()[-1]


def test_the_first_start_of_an_old_installation_writes_the_sheet(install: Install) -> None:
    install.write_env(current_env(installation_id=None))

    result = install.run("start")

    assert result.returncode == 0, result.stderr
    assert (install.root / "brievie-herstelblad.html").exists()


def test_ci_has_shellcheck_for_the_backend_tests() -> None:
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    backend = ci.split("\n  backend:\n", 1)[1].split("\n  web:\n", 1)[0]
    assert "shellcheck" in backend
    assert backend.index("shellcheck") < backend.index("run: uv run pytest")
    assert "working-directory: backend" in backend


# --- fix round 7: final reviews --------------------------------------------------------------

OWN_PW = "mijn eigen wachtwoord 42"
MARKER = "deploy/.restore-in-progress"


def sheet_path(install: Install) -> Path:
    return install.root / "brievie-herstelblad.html"


def test_separate_password_keeps_the_users_own(installed: Install) -> None:
    installed.write_env(current_env().replace(DERIVED_PW, f"'{OWN_PW}'"))

    result = installed.run("backup", "--init", "--separate-password")

    assert result.returncode == 0, result.stderr
    assert f"BRIEVIE_BACKUP_PASSWORD='{OWN_PW}'" in installed.dotenv.read_text()
    assert not any("token_hex(32)" in c for c in installed.commands())
    assert sheet_path(installed).exists()
    assert installed.index("backup_init") < installed.index("recovery_sheet")
    assert "Druk het nu af" in result.stdout
    assert not any(OWN_PW in " ".join(c["argv"]) for c in installed.calls())


def test_separate_password_is_made_when_only_the_derived_one_is_set(installed: Install) -> None:
    result = installed.run("backup", "--init", "--separate-password")

    assert result.returncode == 0, result.stderr
    assert f"BRIEVIE_BACKUP_PASSWORD={SEPARATE_PW}" in installed.dotenv.read_text()
    assert installed.index("token_hex(32)") < installed.index("backup_init")
    assert installed.index("backup_init") < installed.index("recovery_sheet")
    assert sheet_path(installed).exists()
    assert "Druk het nu af" in result.stdout
    installed.assert_no_secret_in_argv()


def test_init_without_a_separate_password_writes_no_sheet(installed: Install) -> None:
    result = installed.run("backup", "--init")

    assert result.returncode == 0, result.stderr
    assert not sheet_path(installed).exists()


def test_restore_asks_for_the_separate_password_when_the_key_alone_does_not_open(
    install: Install,
) -> None:
    result = install.run(
        "restore",
        "--keep-hostname",
        stdin=TYPED_SEPARATE_PW + "\n",
        FAKE_FETCH_PASSWORD=TYPED_SEPARATE_PW,
        **restore_inputs(install),
    )

    assert result.returncode == 0, result.stderr
    fetches = [c for c in install.calls() if "restore_fetch" in c["argv"]]
    assert [f["env"]["BRIEVIE_BACKUP_PASSWORD"] for f in fetches] == ["", TYPED_SEPARATE_PW]
    assert "Deze back-up heeft een apart wachtwoord" in result.stdout
    install.assert_no_secret_in_argv()


def test_a_wrong_key_names_the_separate_password(install: Install) -> None:
    result = install.run(
        "restore",
        FAKE_DOCKER_FAIL="restore_fetch",
        FAKE_DOCKER_FAIL_CODE="2",
        **restore_inputs(install),
    )

    assert result.returncode == 2
    assert "aparte" in result.stderr


def test_an_empty_installation_is_replaced_without_replace(installed: Install) -> None:
    result = installed.run(
        "restore",
        "--keep-hostname",
        FAKE_DATA_ROWS="0",
        **restore_inputs(installed, BRIEVIE_RESTORE_CONFIRM="", BRIEVIE_RESTORE_EMPTY_OK="ja"),
    )

    assert result.returncode == 0, result.stderr
    assert "lege installatie" in result.stdout
    commands = installed.commands()
    assert not any("--safety" in c for c in commands)
    drop = installed.index("psql -U postgres -d postgres")
    assert drop < installed.index("restore_database")
    assert installed.commands()[installed.index("restore_files")].endswith("--replace")
    assert result.stdout.rstrip().splitlines()[-1] == "Herstel gelukt."


def test_an_empty_installation_needs_a_yes(installed: Install) -> None:
    result = installed.run(
        "restore",
        "--keep-hostname",
        stdin="ja\n",
        FAKE_DATA_ROWS="0",
        **restore_inputs(installed, BRIEVIE_RESTORE_CONFIRM=""),
    )

    assert result.returncode == 1
    assert "BRIEVIE_RESTORE_EMPTY_OK=ja" in result.stderr
    assert not any(" stop " in f" {c} " for c in installed.commands())
    assert installed.dotenv.read_text() == current_env()


def test_an_installation_with_data_still_needs_replace(installed: Install) -> None:
    result = installed.run("restore", "--keep-hostname", **restore_inputs(installed))

    assert result.returncode == 1
    assert "./brievie restore --replace" in result.stderr
    assert "heeft al gegevens" in result.stderr
    assert not any("restore_fetch" in c for c in installed.commands())


def test_the_safety_backup_is_asked_with_safety_and_its_id_is_named(installed: Install) -> None:
    result = installed.run(
        "restore",
        "--replace",
        "--keep-hostname",
        FAKE_DOCKER_FAIL="restore_files",
        **restore_inputs(installed),
    )

    assert result.returncode == 1
    safety = next(c for c in installed.calls() if "--safety" in c["argv"])
    assert safety["argv"][-3:] == ["manage.py", "backup", "--safety"]
    assert not any("--no-prune" in c for c in installed.commands())
    assert SAFETY_ID in result.stderr
    assert "30 dagen" in result.stderr


def test_the_marker_records_the_restore_and_goes_on_success(installed: Install) -> None:
    marker = installed.root / MARKER
    result = installed.run(
        "restore",
        "--replace",
        "--keep-hostname",
        FAKE_WATCH=str(marker),
        **restore_inputs(installed),
    )

    assert result.returncode == 0, result.stderr
    database = next(c for c in installed.calls() if "restore_database" in c["argv"])
    assert database.get("watch_mode") == "0o600"
    check = next(c for c in installed.calls() if "restore_check" in c["argv"])
    assert "watch_mode" not in check  # written after the last check, before the swap
    assert not marker.exists()


@pytest.mark.parametrize("command", [("start",), ("backup",), ("status",), ("sweep",)])
def test_a_halfway_restore_blocks_the_other_commands(
    installed: Install, command: tuple[str, ...]
) -> None:
    failed = installed.run(
        "restore",
        "--replace",
        "--keep-hostname",
        FAKE_DOCKER_FAIL="restore_files",
        **restore_inputs(installed),
    )
    assert failed.returncode == 1
    assert (installed.root / MARKER).exists()
    before = len(installed.calls())

    result = installed.run(*command)

    assert result.returncode == 1
    assert "halverwege gestopt" in result.stderr
    assert f"./brievie restore {RUN_ID} --replace --skip-safety-backup" in result.stderr
    assert f"./brievie restore {SAFETY_ID} --replace --skip-safety-backup" in result.stderr
    assert installed.calls()[before:] == []


def test_a_halfway_restore_resumes_with_the_same_run(installed: Install) -> None:
    installed.run(
        "restore",
        "--replace",
        "--keep-hostname",
        FAKE_DOCKER_FAIL="restore_files",
        **restore_inputs(installed),
    )
    before = len(installed.calls())

    result = installed.run(
        "restore", RUN_ID, "--replace", "--keep-hostname", **restore_inputs(installed)
    )

    assert result.returncode == 0, result.stderr
    later = [" ".join(c["argv"]) for c in installed.calls()[before:]]
    assert not any("--safety" in c for c in later)
    assert any("restore_files" in c for c in later)
    assert not (installed.root / MARKER).exists()


def test_a_halfway_restore_refuses_another_run(installed: Install) -> None:
    installed.run(
        "restore",
        "--replace",
        "--keep-hostname",
        FAKE_DOCKER_FAIL="restore_files",
        **restore_inputs(installed),
    )
    other = "1b6c3f7e-1d2a-4c5b-9e8f-7a6b5c4d3e2f"

    result = installed.run(
        "restore", other, "--replace", "--keep-hostname", **restore_inputs(installed)
    )

    assert result.returncode == 1
    assert "halverwege gestopt" in result.stderr


def test_an_interrupt_after_the_drop_says_where_it_stopped(installed: Install) -> None:
    result = installed.run(
        "restore",
        "--replace",
        "--keep-hostname",
        FAKE_KILL_AT="restore_files",
        **restore_inputs(installed),
    )

    assert result.returncode != 0
    assert "halverwege gestopt" in result.stderr
    assert SAFETY_ID in result.stderr
    assert (installed.root / MARKER).exists()
    assert not (installed.root / "deploy" / ".brievie-lock").exists()


def test_the_lock_refuses_a_second_run(installed: Install) -> None:
    lock = installed.root / "deploy" / ".brievie-lock"
    lock.mkdir()
    (lock / "started").write_text(f"{int(__import__('time').time())}\n")

    result = installed.run("status")

    assert result.returncode == 1
    assert "bezig" in result.stderr
    assert installed.calls() == []
    assert lock.exists()


def test_a_stale_lock_needs_a_typed_yes(installed: Install) -> None:
    lock = installed.root / "deploy" / ".brievie-lock"
    lock.mkdir()
    (lock / "started").write_text("1000\n")

    refused = installed.run("status", stdin="JA\n")

    assert refused.returncode == 1
    assert "12 uur" in refused.stderr
    assert lock.exists()


def test_the_lock_is_gone_after_a_run(installed: Install) -> None:
    result = installed.run("status")

    assert result.returncode == 0, result.stderr
    assert not (installed.root / "deploy" / ".brievie-lock").exists()
    leftovers = [p.name for p in (installed.root / "deploy").iterdir() if ".new" in p.name]
    assert leftovers == []


def test_a_fresh_restore_gets_a_new_id_before_the_first_up(install: Install) -> None:
    result = install.run("restore", "--keep-hostname", **restore_inputs(install))

    assert result.returncode == 0, result.stderr
    assert setting(install.dotenv.read_text(), "BRIEVIE_INSTALLATION_ID") == [
        f"BRIEVIE_INSTALLATION_ID={NEW_ID}"
    ]
    write = install.index("installation-id.tmp")
    assert install.index("restore_files") < write
    full_up = next(i for i, c in enumerate(install.commands()) if c.endswith("up -d --wait"))
    assert write < full_up
    assert BACKUP_ID not in " ".join(c.get("stdin", "") for c in install.calls())


def test_start_stores_the_settings_copy(installed: Install) -> None:
    result = installed.run("start")

    assert result.returncode == 0, result.stderr
    assert installed.index("up -d --build --wait") < installed.index("store_env_copy")


@pytest.mark.parametrize(
    ("code", "running", "expected"),
    [
        ("not_picked_up", "postgres keeper", "draait, maar"),
        ("not_picked_up", "postgres", "Typ ./brievie start"),
        ("still_running", "postgres keeper", "Er loopt al een back-up"),
        ("env_copy_missing", "postgres keeper", "Typ ./brievie start en probeer het daarna"),
        ("destination_not_ready", "postgres keeper", "Is de schijf aangesloten?"),
    ],
)
def test_backup_failures_name_the_honest_next_step(
    installed: Install, code: str, running: str, expected: str
) -> None:
    def keeper_answers(fake_running: str) -> dict[str, str]:
        return {"FAKE_DOCKER_RUNNING": fake_running}

    # The keeper must run for the back-up to be asked at all; the second ps answers.
    result = installed.run(
        "backup",
        FAKE_DOCKER_FAIL="manage.py backup",
        FAKE_DOCKER_FAIL_CODE="2" if code in ("not_picked_up", "still_running") else "1",
        FAKE_DOCKER_FAIL_STDERR=f"code={code}\nCommandError: zin\n",
        FAKE_KEEPER_GONE_AFTER="manage.py backup" if "keeper" not in running else "",
        **keeper_answers("postgres keeper"),
    )

    assert expected in result.stderr


def test_a_clock_warning_is_shown_after_a_good_backup(installed: Install) -> None:
    result = installed.run(
        "backup",
        FAKE_STDERR_AT="manage.py backup",
        FAKE_STDERR_TEXT="code=clock_skew\n",
    )

    assert result.returncode == 0, result.stderr
    assert "klok" in result.stdout + result.stderr
    assert "code=" not in result.stdout + result.stderr


def test_keeping_the_destination_warns_about_the_original_server(install: Install) -> None:
    result = install.run(
        "restore",
        "--keep-hostname",
        **restore_inputs(install, BRIEVIE_RESTORE_KEEP_DESTINATION="ja"),
    )

    assert result.returncode == 0, result.stderr
    assert "oorspronkelijke server" in result.stdout
