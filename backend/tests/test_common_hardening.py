"""Processes that hold secrets are not dumpable (re-review of PR #58, point 3): another
process of the same user, such as a parser child taken over by a crafted file, cannot
read their environment or memory through /proc."""

import importlib
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import manage
from apps.common import hardening
from brievie import gunicorn_config

BACKEND = Path(__file__).resolve().parents[1]
DOCKERFILE = BACKEND.parent / "deploy" / "Dockerfile"
linux_only = pytest.mark.skipif(sys.platform != "linux", reason="prctl is Linux")


def _child(code: str) -> list[str]:
    """A fresh interpreter with the backend on its path (never the test process itself)."""
    return [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(BACKEND)!r}); {code}"]


@linux_only
def test_forbid_inspection_makes_the_process_not_dumpable() -> None:
    code = (
        "from apps.common import hardening;"
        " before = hardening.is_dumpable(); applied = hardening.forbid_inspection();"
        " print(before, applied, hardening.is_dumpable())"
    )

    out = subprocess.run(_child(code), capture_output=True, text=True, check=True).stdout  # noqa: S603

    assert out.split() == ["True", "True", "False"]


@linux_only
@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every process's /proc files")
def test_another_process_of_the_user_cannot_read_its_environment() -> None:
    code = (
        "import time; from apps.common import hardening; hardening.forbid_inspection();"
        " print('ready', flush=True); time.sleep(30)"
    )
    env = {"PATH": "/usr/bin:/bin", "BRIEVIE_MASTER_KEY": "secret-for-the-test"}
    child = subprocess.Popen(_child(code), stdout=subprocess.PIPE, text=True, env=env)  # noqa: S603
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(PermissionError):
            Path(f"/proc/{child.pid}/environ").read_bytes()
        with pytest.raises(PermissionError):
            Path(f"/proc/{child.pid}/mem").open("rb").close()
    finally:
        child.kill()
        child.wait()
        if child.stdout is not None:
            child.stdout.close()


@linux_only
def test_a_process_without_it_is_readable_by_the_same_user() -> None:
    # The control for the test above: same user, not hardened, readable.
    child = subprocess.Popen(
        [sys.executable, "-I", "-c", "import time; time.sleep(30)"],
        env={"PATH": "/usr/bin:/bin", "MARKER": "visible"},
    )
    try:
        deadline = time.monotonic() + 5
        environ = b""
        while b"MARKER" not in environ and time.monotonic() < deadline:
            environ = Path(f"/proc/{child.pid}/environ").read_bytes()
        assert b"MARKER=visible" in environ
    finally:
        child.kill()
        child.wait()


@pytest.mark.skipif(sys.platform == "linux", reason="elsewhere than Linux")
def test_elsewhere_it_does_nothing() -> None:
    assert hardening.forbid_inspection() is False
    assert hardening.is_dumpable() is None


def test_the_worker_command_applies_it_before_django_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    monkeypatch.setattr(hardening, "forbid_inspection", lambda: order.append("hardened"))
    monkeypatch.setattr(
        "django.core.management.execute_from_command_line",
        lambda argv: order.append("command"),
    )

    manage.main()

    assert order == ["hardened", "command"]


def test_gunicorn_applies_it_in_the_master_before_the_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[bool] = []
    monkeypatch.setattr(gunicorn_config, "forbid_inspection", lambda: called.append(True))

    gunicorn_config.on_starting(object())

    assert called == [True]
    command = next(line for line in DOCKERFILE.read_text().splitlines() if line.startswith("CMD"))
    assert "exec gunicorn --config python:brievie.gunicorn_config " in command


def test_the_wsgi_module_applies_it(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []
    monkeypatch.setattr(hardening, "forbid_inspection", lambda: called.append(True))
    import brievie.wsgi  # with the stand-in: this test process itself stays as it is

    called.clear()
    importlib.reload(brievie.wsgi)

    assert called == [True]
