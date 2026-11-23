"""settings.py reads its secrets from X_FILE (a fresh interpreter; files only under tmp_path)."""

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

_SCRIPT = """
import django
from django.conf import settings
print(settings.SECRET_KEY[:4], settings.BRIEVIE_MASTER_KEY[:4])
print(settings.DATABASES["default"]["PASSWORD"])
"""


def run(environ: dict[str, str]) -> subprocess.CompletedProcess[str]:
    # Empty values count as unset for a secret, and stop deploy/.env from filling them in.
    base = {"PATH": os.environ.get("PATH", ""), "SECRET_KEY": "", "BRIEVIE_MASTER_KEY": ""}
    env = {**base, "DJANGO_SETTINGS_MODULE": "brievie.settings", **environ}
    return subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-c", _SCRIPT],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def secret_file(directory: Path, name: str, text: str) -> str:
    path = directory / name
    path.write_text(text + "\n")
    path.chmod(0o400)
    return str(path)


def test_settings_read_the_files(tmp_path: Path) -> None:
    result = run(
        {
            "SECRET_KEY_FILE": secret_file(tmp_path, "sk", "s" * 64),
            "BRIEVIE_MASTER_KEY_FILE": secret_file(tmp_path, "mk", "ab" * 32),
            "DATABASE_URL": "postgres://brievie_app@postgres:5432/brievie",
            "DATABASE_PASSWORD": "",
            "DATABASE_PASSWORD_FILE": secret_file(tmp_path, "pw", "pw1234"),
        }
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["ssss", "abab", "pw1234"]


def test_placeholder_secret_key_in_a_file_is_still_refused(tmp_path: Path) -> None:
    result = run(
        {
            "SECRET_KEY_FILE": secret_file(tmp_path, "sk", "change-me-" + "x" * 60),
            "DATABASE_URL": "postgres://u:p@h:5432/d",
        }
    )
    assert result.returncode != 0
    assert "placeholder" in result.stderr
    assert "change-me-" + "x" * 60 not in result.stderr


def test_unreadable_secret_file_names_a_code_not_content(tmp_path: Path) -> None:
    result = run(
        {
            "SECRET_KEY_FILE": str(tmp_path / "missing"),
            "DATABASE_URL": "postgres://u:p@h:5432/d",
        }
    )
    assert result.returncode != 0
    assert "unreadable" in result.stderr
