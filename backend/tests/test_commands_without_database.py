"""The commands ./brievie runs outside the running stack need no database for their checks.

`./brievie` starts them with `compose run --no-deps` or a bare `docker run`, sometimes
while PostgreSQL is down on purpose (restore). Django's system checks read database
features for some fields (`GeneratedField` asks the server version), so a command that
runs them would stop with a connection error before doing its work.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
WRAPPER = BACKEND.parent / "brievie"
# A port nobody listens on: any connection attempt is refused at once.
CLOSED_DATABASE = "postgres://nobody:nothing@127.0.0.1:1/nothing"

# Runs each command's own check step exactly as BaseCommand.execute does.
_CHECK_SCRIPT = """
import sys
import django
django.setup()
from django.core.management import get_commands, load_command_class
commands = get_commands()
for name in sys.argv[1:]:
    command = load_command_class(commands[name], name)
    if command.requires_system_checks:
        command.check(**command.get_check_kwargs({}))
    print("ok", name)
"""


def offline_commands() -> list[str]:
    """Every manage.py command the wrapper starts in a new container (not `exec`)."""
    names: set[str] = set()
    for line in WRAPPER.read_text().splitlines():
        found = re.search(r"python manage\.py (\w+)", line)
        if found and not re.search(r"\bexec\b", line):
            names.add(found.group(1))
    return sorted(names)


def test_the_wrapper_starts_commands_in_new_containers() -> None:
    names = offline_commands()
    assert "restore_check" in names
    assert "restore_fetch" in names
    assert "update_preflight" in names
    assert "backup" not in names  # runs with exec in the keeper


def test_their_checks_need_no_database() -> None:
    names = offline_commands()
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "brievie.settings",
        "DATABASE_URL": CLOSED_DATABASE,
        "BRIEVIE_DB_ROLE": "app",
    }
    result = subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-c", _CHECK_SCRIPT, *names],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    assert result.stdout.split() == [word for name in names for word in ("ok", name)]
