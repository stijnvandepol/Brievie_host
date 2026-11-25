"""The child process that parses an untrusted file (`apps.intake.sandbox`, security review
of PR #58, points 2 and 6): it gets no secret from the environment, answers in JSON only
(never pickle), and a malformed, oversized, slow or greedy child is refused."""

import dataclasses
import json
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


def _limited(statement: str, *, cpu: int = 30, memory: int = 256 * 1024 * 1024) -> list[str]:
    """A child that applies the parser's own limits first, then runs `statement`."""
    folder = str(Path(parsers.__file__).parent)
    return _python(
        f"import sys; sys.path.insert(0, {folder!r}); import parsers;"
        f" parsers.set_limits(cpu_seconds={cpu}, memory_bytes={memory}, answer_bytes=1024);"
        f" {statement}; import json; print(json.dumps({{'ok': True}}))"
    )


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
