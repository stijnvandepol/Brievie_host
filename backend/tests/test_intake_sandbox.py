"""The child process that parses an untrusted file (`apps.intake.sandbox`, security review
of PR #58, points 2 and 6): it gets no secret from the environment, answers in JSON only
(never pickle), and a malformed, oversized, slow or greedy child is refused."""

import base64
import dataclasses
import json
import os
import sys
import time
from pathlib import Path

import pytest

from apps.intake import parsers, sandbox
from apps.intake.sandbox import IsolationError
from tests.intake_fixtures import make_encrypted_pdf, make_jpeg, make_pdf

# The names a worker's environment holds that must never reach a parser of a stranger's file.
SECRET_NAMES = (
    "BRIEVIE_MASTER_KEY",
    "SECRET_KEY",
    "DATABASE_URL",
    "DATABASE_OWNER_URL",
    "DATABASE_BACKUP_URL",
    "BRIEVIE_APP_PASSWORD",
)


def _python(code: str) -> list[str]:
    """A child started the way the sandbox starts its parser, running `code` instead."""
    return [sys.executable, "-I", "-c", code]


def test_the_child_sees_none_of_the_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in SECRET_NAMES:
        monkeypatch.setenv(name, "secret-value-for-the-test")
    probe = "import json, os; print(json.dumps({'ok': True, 'env': sorted(os.environ)}))"

    answer = sandbox.execute(_python(probe), b"", timeout=10, answer_limit=64 * 1024)

    seen = answer["env"]
    assert isinstance(seen, list)
    assert not set(SECRET_NAMES) & set(seen)
    assert set(seen) <= {"PATH", "LANG", "LC_CTYPE", "__CF_USER_TEXT_ENCODING"}


def test_the_child_reads_its_input_from_stdin() -> None:
    probe = (
        "import json, sys; data = sys.stdin.buffer.read();"
        " print(json.dumps({'ok': True, 'size': len(data)}))"
    )

    answer = sandbox.execute(_python(probe), b"x" * 300_000, timeout=10, answer_limit=1024)

    assert answer == {"ok": True, "size": 300_000}


@pytest.mark.parametrize(
    "output",
    [
        "print('not json')",
        "print('[1, 2]')",
        "print(json.dumps({'pages': 1}))",
        "import pickle, sys; sys.stdout.buffer.write(pickle.dumps({'ok': True}))",
        "pass",
    ],
)
def test_a_malformed_answer_is_refused(output: str) -> None:
    with pytest.raises(IsolationError):
        sandbox.execute(_python(f"import json; {output}"), b"", timeout=10, answer_limit=1024)


def test_a_deeply_nested_answer_is_refused() -> None:
    # Re-review of PR #58, point 1: json.loads raised RecursionError (a 500), not a refusal.
    probe = "import sys; sys.stdout.write('[' * 200000 + ']' * 200000)"

    with pytest.raises(IsolationError):
        sandbox.execute(_python(probe), b"", timeout=10, answer_limit=1024 * 1024)


def test_an_oversized_answer_is_refused() -> None:
    probe = "import json; print(json.dumps({'ok': True, 'fill': 'x' * 5000}))"

    with pytest.raises(IsolationError):
        sandbox.execute(_python(probe), b"", timeout=10, answer_limit=1024)


def test_a_child_that_fails_is_refused() -> None:
    with pytest.raises(IsolationError):
        sandbox.execute(_python("raise SystemExit(3)"), b"", timeout=10, answer_limit=1024)


def test_a_slow_child_is_killed_on_time() -> None:
    started = time.monotonic()

    with pytest.raises(IsolationError):
        sandbox.execute(_python("import time; time.sleep(30)"), b"", timeout=1, answer_limit=1024)

    assert time.monotonic() - started < 10


def _gone(pid: int, *, within: float = 10.0) -> bool:
    """True once `pid` no longer runs (gone, or a zombie waiting for init to reap it)."""
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        status = Path(f"/proc/{pid}/status")
        try:
            if "\tZ" in status.read_text().split("State:", 1)[1].split("\n", 1)[0]:
                return True
        except (OSError, IndexError):
            pass
        time.sleep(0.1)
    return False


def _forking(pid_file: Path, then: str) -> list[str]:
    """A child that forks a grandchild sleeping for a minute (its pid in `pid_file`), then
    runs `then` itself."""
    return _python(
        "import os, sys, time\n"
        "if os.fork() == 0:\n"
        f"    open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        "    time.sleep(60)\n"
        "    os._exit(0)\n"
        f"while not os.path.exists({str(pid_file)!r}): time.sleep(0.01)\n"
        f"{then}\n"
    )


def _pid_in(pid_file: Path) -> int:
    for _ in range(100):
        text = pid_file.read_text() if pid_file.exists() else ""
        if text:
            return int(text)
        time.sleep(0.05)
    raise AssertionError("the grandchild never started")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_a_timeout_kills_the_childs_whole_process_group(tmp_path: Path) -> None:
    # Re-review of PR #58, point 2: the timeout killed the child only; what it forked lived on.
    pid_file = tmp_path / "grandchild"

    with pytest.raises(IsolationError):
        sandbox.execute(_forking(pid_file, "time.sleep(60)"), b"", timeout=2, answer_limit=1024)

    assert _gone(_pid_in(pid_file))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_what_a_child_left_behind_is_killed_after_its_answer(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild"
    answer = "import json; print(json.dumps({'ok': True}))"

    assert sandbox.execute(_forking(pid_file, answer), b"", timeout=10, answer_limit=1024) == {
        "ok": True
    }

    assert _gone(_pid_in(pid_file))


@pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() == 0,
    reason="RLIMIT_NPROC binds a non-root user on Linux",
)
def test_the_parser_child_cannot_fork() -> None:
    # A grandchild could leave the process group (setsid); one that cannot be made cannot.
    with pytest.raises(IsolationError):
        sandbox.execute(_limited("import os; os.fork()"), b"", timeout=10, answer_limit=1024)


def _limited(statement: str, *, cpu: int = 30, memory: int = 256 * 1024 * 1024) -> list[str]:
    """A child that applies the parser's own limits first, then runs `statement`."""
    folder = str(Path(parsers.__file__).parent)
    return _python(
        f"import sys; sys.path.insert(0, {folder!r}); import parsers;"
        f" parsers.set_limits(cpu_seconds={cpu}, memory_bytes={memory}, answer_bytes=1024);"
        f" {statement}; import json; print(json.dumps({{'ok': True}}))"
    )


@pytest.mark.parametrize(
    "statement",
    [
        f"open({str(Path(__file__))!r}, 'rb')",
        "import socket; socket.socket()",
        "import os; os.open('/proc/self/environ', os.O_RDONLY)",
        # Closing a descriptor first must not make room for another.
        f"import os; os.close(0); open({str(Path(__file__))!r}, 'rb')",
        "import os; os.close(0); os.close(2); os.open('/proc/self/environ', os.O_RDONLY)",
        "import os, socket; os.close(0); socket.socket()",
    ],
)
def test_the_counting_child_can_open_nothing_new(statement: str) -> None:
    # Security review of PR #64 (M2): no key file, no parent's /proc, no share, no socket.
    # Every module is imported before: what fails is the new descriptor, not an import.
    command = _limited(f"import json, os, socket; parsers.forbid_new_files(); {statement}")
    with pytest.raises(IsolationError):
        sandbox.execute(command, b"", timeout=10, answer_limit=1024)


def test_the_counting_child_still_answers_after_closing_the_door() -> None:
    command = _limited("import json; parsers.forbid_new_files()")
    assert sandbox.execute(command, b"", timeout=10, answer_limit=1024) == {"ok": True}


@pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_AS is a Linux limit")
def test_the_memory_limit_stops_a_greedy_child() -> None:
    with pytest.raises(IsolationError):
        sandbox.execute(
            _limited("bytearray(1024 * 1024 * 1024)"), b"", timeout=20, answer_limit=1024
        )


@pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_CPU is enforced on Linux")
def test_the_cpu_limit_stops_a_busy_child() -> None:
    started = time.monotonic()

    with pytest.raises(IsolationError):
        sandbox.execute(_limited("while True: pass", cpu=1), b"", timeout=20, answer_limit=1024)

    assert time.monotonic() - started < 15


def test_the_parser_counts_pages_in_the_child() -> None:
    assert sandbox.count_pdf_pages(make_pdf(["een", "twee"]), timeout=10) == 2


def test_the_parsers_code_reaches_the_parent() -> None:
    with pytest.raises(IsolationError) as caught:
        sandbox.count_pdf_pages(make_encrypted_pdf(), timeout=10)

    assert str(caught.value) == "file_encrypted"


def test_the_parser_renders_pages_in_the_child() -> None:
    pages = sandbox.render(make_jpeg(400, 300), kind="jpeg", max_pages=30, timeout=30)

    assert len(pages) == 1
    assert pages[0].jpeg.startswith(b"\xff\xd8")


def test_the_child_sends_no_text_of_a_pdf() -> None:
    # Point 4: the PDF's own text layer never leaves the child; every page is OCR'd.
    pages = sandbox.render(
        make_pdf(["Gemeente Voorbeeldstad, aanslag 2026"]), kind="pdf", max_pages=30, timeout=30
    )

    assert [field.name for field in dataclasses.fields(pages[0])] == ["jpeg"]


@pytest.mark.parametrize(
    "answer",
    [
        {"ok": True, "pages": "x"},
        {"ok": True, "pages": ["/9j/"]},
        {"ok": True, "pages": [{"jpeg": 3}]},
        {"ok": True, "pages": [{"jpeg": "not base64!"}]},
        {"ok": True, "pages": [{"jpeg": "aGVsbG8="}]},
        {"ok": True, "pages": [{"jpeg": "/9j/"}] * 3},
        {"ok": True, "pages": []},
    ],
)
def test_a_render_answer_of_the_wrong_shape_is_refused(answer: dict[str, object]) -> None:
    with pytest.raises(IsolationError):
        sandbox.pages_from(json.loads(json.dumps(answer)), max_pages=2)


@pytest.mark.parametrize("answer", [{"ok": True, "pages": True}, {"ok": True, "pages": -1}])
def test_a_count_answer_of_the_wrong_shape_is_refused(answer: dict[str, object]) -> None:
    with pytest.raises(IsolationError):
        sandbox.count_from(answer)


def test_an_unknown_error_code_from_the_child_is_not_passed_on() -> None:
    with pytest.raises(IsolationError) as caught:
        sandbox.count_from({"ok": False, "code": "<script>"})

    assert str(caught.value) == ""


def _capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Stand in for the child: record the command and answer limit, answer one page."""
    seen: dict[str, object] = {}

    def fake(command: list[str], data: bytes, *, timeout: float, answer_limit: int) -> object:
        seen["command"], seen["answer_limit"] = command, answer_limit
        return {"ok": True, "pages": [{"jpeg": "/9j/"}]}

    monkeypatch.setattr(sandbox, "execute", fake)
    return seen


def test_the_answer_has_a_ceiling_whatever_the_page_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Re-review of PR #58, point 6: the limit was pages x 8 MiB, so a large
    # BRIEVIE_MAX_PAGES let the child send (and the parent hold) gigabytes.
    seen = _capture(monkeypatch)

    sandbox.render(b"%PDF", kind="pdf", max_pages=10_000, timeout=1)

    assert seen["answer_limit"] == sandbox.ANSWER_BYTES_MAX
    command = seen["command"]
    assert isinstance(command, list)
    # The child's own RLIMIT_FSIZE (its answer argument) is the same number.
    assert command[6] == str(sandbox.ANSWER_BYTES_MAX)


def test_a_few_pages_keep_their_own_smaller_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture(monkeypatch)

    sandbox.render(b"%PDF", kind="pdf", max_pages=3, timeout=1)

    assert seen["answer_limit"] == 3 * sandbox.ANSWER_BYTES_PER_PAGE
    assert sandbox.ANSWER_BYTES_MAX < 30 * sandbox.ANSWER_BYTES_PER_PAGE


def test_a_page_over_its_own_limit_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "ANSWER_BYTES_PER_PAGE", 64)
    small = base64.b64encode(b"\xff\xd8" + b"x" * 40).decode()
    large = base64.b64encode(b"\xff\xd8" + b"x" * 60).decode()

    assert len(sandbox.pages_from({"ok": True, "pages": [{"jpeg": small}]}, max_pages=2)) == 1
    with pytest.raises(IsolationError):
        sandbox.pages_from({"ok": True, "pages": [{"jpeg": small}, {"jpeg": large}]}, max_pages=2)
