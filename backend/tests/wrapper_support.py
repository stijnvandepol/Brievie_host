"""A throwaway installation for `./brievie update`: real git, a fake docker.

`UpdateInstall` makes, under the test's own tmp_path, a bare `origin` repository with two
releases (tags v0.2.0 and v0.3.0, each with this repository's `brievie` and deploy files)
and a clone `install` checked out at v0.2.0 with a `deploy/.env` that pins
BRIEVIE_VERSION=0.2.0. A fake `docker` first on PATH logs every call as a JSON line and
answers from FAKE_* variables. No test reaches the real docker or this repository's
checkout; every path is under tmp_path.
"""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

REPO = Path(__file__).resolve().parents[2]
RUN_ID = "0b6c3f7e-1d2a-4c5b-9e8f-7a6b5c4d3e2f"
SAFETY_ID = "5afe0000-1d2a-4c5b-9e8f-7a6b5c4d3e2f"
INSTALLATION_ID = "c0" * 16
MASTER = "5a" * 32
POSTGRES_PW = "a1" * 24
OWNER_PW = "b2" * 24
APP_PW = "c3" * 24
BACKUP_DB_PW = "d4" * 24
BACKUP_PW = "e5" * 32
SECRETS = [MASTER, POSTGRES_PW, OWNER_PW, APP_PW, BACKUP_DB_PW, BACKUP_PW]


# The deploy files a release carries (enough for the wrapper's own steps).
class Call(TypedDict, total=False):
    argv: list[str]
    stdin: str | int
    image_env: str
    backup_dir_env: str
    attest_env: str


DEPLOY_FILES = (
    "docker-compose.yml",
    "docker-compose.dev.yml",
    "init-env.sh",
    ".env.example",
    "ensure-backup-role.sh",
    "Caddyfile",
    "caddy-publish-root.sh",
    "Dockerfile",
)

FAKE_DOCKER = """#!{python}
import json, os, signal, sys, time

argv = sys.argv[1:]
joined = " ".join(argv)
entry = {{"argv": argv}}
if "-i" in argv or "store_env_copy" in argv or "psql" in argv or argv[:1] == ["build"]:
    data = sys.stdin.buffer.read()
    entry["stdin"] = data.decode("utf-8", "replace") if argv[:1] != ["build"] else len(data)
entry["attest_env"] = os.environ.get("BUILDX_NO_DEFAULT_ATTESTATIONS", "<unset>")
if argv[:1] == ["compose"]:
    entry["image_env"] = os.environ.get("BRIEVIE_IMAGE", "")
    entry["backup_dir_env"] = os.environ.get("BRIEVIE_BACKUP_DIR", "<unset>")
with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
    log.write(json.dumps(entry) + "\\n")

sleep_at = os.environ.get("FAKE_SLEEP_AT", "")
if sleep_at and sleep_at in joined:
    # A Docker that hangs for a while (a credential helper that does not answer).
    time.sleep(float(os.environ.get("FAKE_SLEEP_SECONDS", "3")))
    with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
        log.write(json.dumps({{"argv": ["<done>", *argv]}}) + "\\n")
kill_at = os.environ.get("FAKE_KILL_AT", "")
if kill_at and kill_at in joined:
    # Ctrl-C: the terminal sends SIGINT to the whole process group of ./brievie.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    os.killpg(os.getpgid(0), signal.SIGINT)
    sys.exit(130)
for rule in filter(None, os.environ.get("FAKE_FAIL", "").split("|")):
    needle, _, code = rule.partition("=")
    if needle in joined:
        sys.stderr.write(os.environ.get("FAKE_FAIL_STDERR", ""))
        sys.exit(int(code or 1))

if argv[:2] == ["ps", "-a"]:
    sys.exit(0)
if argv[:2] == ["volume", "inspect"]:
    sys.exit(0 if argv[-1].endswith(("_pgdata", "_update-backup")) else 1)
if argv[:2] == ["image", "inspect"]:
    missing = os.environ.get("FAKE_MISSING_IMAGES", "").split()
    sys.exit(1 if argv[-1] in missing else 0)
if argv[:1] == ["compose"] and "ps" in argv:
    running = os.environ.get("FAKE_RUNNING", "postgres api worker keeper caddy").split()
    if "--services" in argv:
        sys.stdout.write("".join(name + "\\n" for name in running))
    sys.exit(0)
if "PG_VERSION" in joined:
    sys.stdout.write(os.environ.get("FAKE_PG_MAJOR", "18") + "\\n")
elif "/data/backup-config/installation-id" in joined and "installation-id.tmp" not in joined:
    sys.stdout.write("ID={installation_id}")
elif "update_plan" in argv:
    plan = os.environ.get("FAKE_PLAN", "allowed")
    if plan.startswith("refuse:"):
        sys.stderr.write("CommandError: " + plan[len("refuse:"):] + "\\n")
        sys.exit(1)
    sys.stdout.write(plan + "\\n")
elif "update_backup" in argv:
    sys.stdout.write("run_id={run_id}\\nDe noodback-up is gemaakt (1 MB).\\n")
elif "manage.py" in argv and "--safety" in argv:
    sys.stdout.write("safety_run_id={safety_id}\\nBack-up gestart…\\n"
                     "De back-up is gelukt (1 MB).\\n")
elif "restore_fetch" in argv:
    out = next(argv[i + 1].rsplit(":/out", 1)[0] for i, a in enumerate(argv)
               if a == "-v" and argv[i + 1].endswith(":/out"))
    with open(os.path.join(out, "env"), "w") as handle:
        handle.write(open(os.environ["FAKE_RESTORED_ENV"]).read())
    with open(os.path.join(out, "manifest.json"), "w") as handle:
        handle.write("{{}}")
    sys.stdout.write("run_id={run_id}\\ncreated_at=2026-11-19T03:00:00+00:00\\n"
                     "hostname=brievie.local\\nbrievie_version=0.2.0\\n")
elif "restore_database" in argv:
    sys.stdout.write("interrupted_runs=0\\n")
elif "verify_restore" in argv:
    sys.stdout.write("workspaces=1\\npages_opened=1\\n")
elif "update_status" in argv:
    sys.stdout.write("Laatst bijgewerkt: 0.2.0 → 0.3.0 op 19-11-2026 21:00\\n")
sys.exit(0)
"""


def env_text(*, version: str | None = "0.2.0", destination: bool = False) -> str:
    lines = [
        f"POSTGRES_PASSWORD={POSTGRES_PW}",
        f"BRIEVIE_OWNER_PASSWORD={OWNER_PW}",
        f"BRIEVIE_APP_PASSWORD={APP_PW}",
        f"DATABASE_URL=postgres://brievie_app:{APP_PW}@localhost:5432/brievie",
        f"DATABASE_OWNER_URL=postgres://brievie_owner:{OWNER_PW}@localhost:5432/brievie",
        "SECRET_KEY=" + "k" * 60,
        "BRIEVIE_HOSTNAME=brievie.local",
        f"BRIEVIE_MASTER_KEY={MASTER}",
        f"BRIEVIE_BACKUP_DB_PASSWORD={BACKUP_DB_PW}",
        f"DATABASE_BACKUP_URL=postgres://brievie_backup:{BACKUP_DB_PW}@localhost:5432/brievie",
        f"BRIEVIE_BACKUP_PASSWORD={BACKUP_PW}",
        f"BRIEVIE_INSTALLATION_ID={INSTALLATION_ID}",
    ]
    if destination:
        lines.append("BRIEVIE_BACKUP_DIR=/Volumes/Backup/brievie")
    if version is not None:
        lines.append(f"BRIEVIE_VERSION={version}")
    return "\n".join(lines) + "\n"


class UpdateInstall:
    """A git origin with releases, a clone of it with a fake docker, all under tmp_path."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path.resolve()
        self.origin = self.tmp / "origin.git"
        self.source = self.tmp / "source"
        self.root = self.tmp / "install"
        self.bin = self.tmp / "bin"
        self.home = self.tmp / "home"
        self.log = self.tmp / "docker.log"
        for directory in (self.source, self.bin, self.home):
            directory.mkdir()
        docker = self.bin / "docker"
        docker.write_text(
            FAKE_DOCKER.format(
                python=sys.executable,
                run_id=RUN_ID,
                safety_id=SAFETY_ID,
                installation_id=INSTALLATION_ID,
            )
        )
        docker.chmod(0o755)
        self.restored = self.tmp / "restored.env"
        self.restored.write_text(env_text())
        self.env: dict[str, str] = {
            "PATH": f"{self.bin}:/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(self.home),
            "FAKE_DOCKER_LOG": str(self.log),
            "FAKE_RESTORED_ENV": str(self.restored),
            "GIT_CONFIG_GLOBAL": str(self.tmp / "gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
            "BRIEVIE_UPDATE_CONFIRM": "BIJWERKEN",
        }
        (self.tmp / "gitconfig").write_text("[advice]\n\tdetachedHead = false\n")
        self.git("init", "-q", "-b", "main", cwd=self.source)
        self.release("0.2.0")
        self.release("0.3.0", minimum="0.2.0")
        self.git("init", "-q", "--bare", str(self.origin), cwd=self.tmp)
        self.git("push", "-q", str(self.origin), "main", "--tags", cwd=self.source)
        self.git("clone", "-q", str(self.origin), str(self.root), cwd=self.tmp)
        self.git("checkout", "-q", "v0.2.0")
        self.write_env(env_text())

    # --- the repositories --------------------------------------------------------------

    def git(self, *args: str, cwd: Path | None = None) -> str:
        done = subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=cwd or self.root,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        )
        return done.stdout.strip()

    def release(
        self,
        version: str,
        *,
        minimum: str | None = None,
        wrapper: str | None = None,
        tag: bool = True,
    ) -> None:
        """A commit in `source` that is release `version` (tagged v<version>)."""
        source = self.source
        (source / "deploy" / "postgres-init").mkdir(parents=True, exist_ok=True)
        (source / "backend").mkdir(exist_ok=True)
        text = wrapper if wrapper is not None else (REPO / "brievie").read_text()
        (source / "brievie").write_text(text)
        (source / "brievie").chmod(0o755)
        shutil.copy(REPO / ".gitignore", source / ".gitignore")
        for name in DEPLOY_FILES:
            shutil.copy(REPO / "deploy" / name, source / "deploy" / name)
        for script in (REPO / "deploy" / "postgres-init").iterdir():
            shutil.copy(script, source / "deploy" / "postgres-init" / script.name)
        (source / "backend" / "pyproject.toml").write_text(
            f'[project]\nname = "brievie"\nversion = "{version}"\n'
        )
        release = {
            "format": 1,
            "version": version,
            "min_upgrade_from": minimum or version,
            "postgres_major": 18,
        }
        (source / "deploy" / "release.json").write_text(json.dumps(release) + "\n")
        self.git("add", "-A", cwd=source)
        self.git("commit", "-q", "--allow-empty", "-m", f"release {version}", cwd=source)
        if tag:
            self.git("tag", f"v{version}", cwd=source)

    def publish(self) -> None:
        """Push what `source` has to origin (new releases)."""
        self.git("push", "-q", "--force", str(self.origin), "main", "--tags", cwd=self.source)

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def tag_commit(self, tag: str) -> str:
        return self.git("rev-parse", f"{tag}^{{commit}}")

    # --- the installation --------------------------------------------------------------

    @property
    def dotenv(self) -> Path:
        return self.root / "deploy" / ".env"

    @property
    def marker(self) -> Path:
        return self.root / "deploy" / ".update" / "in-progress"

    def write_env(self, text: str) -> None:
        self.dotenv.write_text(text)
        self.dotenv.chmod(0o600)

    def write_marker(self, **values: str) -> None:
        self.marker.parent.mkdir(exist_ok=True)
        self.marker.write_text("".join(f"{key}={value}\n" for key, value in values.items()))

    def tracked_state(self) -> tuple[str, str]:
        return self.head(), hashlib.sha256(self.dotenv.read_bytes()).hexdigest()

    def run(
        self, *args: str, stdin: str = "", shell: str = "/bin/sh", **env: str
    ) -> subprocess.CompletedProcess[str]:
        script = self.root / "brievie"
        assert script.resolve().is_relative_to(self.tmp)
        assert shutil.which("docker", path=env.get("PATH", self.env["PATH"])) == str(
            self.bin / "docker"
        )
        full = {**self.env, **env}
        for name, value in env.items():
            if value == "<unset>":
                del full[name]
        return subprocess.run(  # noqa: S603
            [shell, str(script), *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            input=stdin,
            env=full,
            timeout=120,
            check=False,
            # Its own process group: a simulated Ctrl-C never reaches the test runner.
            start_new_session=True,
        )

    def calls(self) -> list[Call]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def commands(self) -> list[str]:
        return [" ".join(call["argv"]) for call in self.calls()]

    def index(self, fragment: str, *, after: int = -1) -> int:
        for position, command in enumerate(self.commands()):
            if position > after and fragment in command:
                return position
        raise AssertionError(f"no call with {fragment!r} in {self.commands()}")

    def has(self, fragment: str) -> bool:
        return any(fragment in command for command in self.commands())

    def stdin_of(self, fragment: str) -> str:
        for call in self.calls():
            if fragment in " ".join(call["argv"]):
                return str(call.get("stdin", ""))
        raise AssertionError(fragment)

    def assert_no_secret_in_argv(self) -> None:
        for command in self.commands():
            for secret in SECRETS:
                assert secret not in command, command[:80]


def order_of(install: UpdateInstall, *needles: str) -> list[int]:
    """The position of each needle's first call, each after the one before."""
    positions: list[int] = []
    after = -1
    for needle in needles:
        after = install.index(needle, after=after)
        positions.append(after)
    return positions
