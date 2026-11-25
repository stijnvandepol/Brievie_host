"""Run a parser of an untrusted file in a child process with limits (intake spec S 1.2).

PDFium and the image decoders are C code fed with a stranger's bytes. They run in a child
started as Tesseract is (`apps.intake.ocr`): `python -I apps/intake/parsers.py`, a fresh
interpreter that ignores `PYTHON*` variables and the user's site, with an environment of
`PATH` and `LANG` only (so none of the worker's secrets: master key, `SECRET_KEY`,
database URLs), no inherited descriptors, the file on stdin. The child sets its own
address-space, CPU, output-size and process limits before it reads a byte
(`parsers.set_limits`; it cannot fork) and answers one JSON object on stdout, written to a
temporary file the parent measures before it reads; the parent kills its process group
after `timeout`, and when it ends. Nothing is unpickled: a hijacked
child can only send JSON, which the parent checks field by field.

A crash, a kill, an answer too large or of the wrong shape is `IsolationError("")`; a
parser's refusal is `IsolationError(code)` for a code the user may see. Nothing of the
file, the answer or the child's error output is logged.

This is a process boundary, not a container: the child still runs as the worker's user
with its network, so a child taken over by a crafted file could still read what that user
can read, such as the parent's `/proc/<pid>/environ` or a key file. A separate user or
container closes that; it is a follow-up (DECISIONS, "Security review of PR #58").
"""

import base64
import binascii
import contextlib
import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from apps.intake.parsers import ParsedPage

# The parser script; it imports no Django and nothing of this project.
CHILD = Path(__file__).with_name("parsers.py")
# PDFium rendering a 2500 px page needs well under this; a decompression bomb hits it.
MEMORY_LIMIT = 1536 * 1024 * 1024
# A 2500 px JPEG at quality 85 stays far below this, base64 included; a page over it is
# refused.
ANSWER_BYTES_PER_PAGE = 8 * 1024 * 1024
# The whole answer, whatever BRIEVIE_MAX_PAGES says: the parent holds it in memory (read,
# parsed, decoded). 30 letter pages of 1.6 MB each fit; a scan's whole upload is 32 MiB.
ANSWER_BYTES_MAX = 64 * 1024 * 1024
COUNT_ANSWER_BYTES = 4096
# The codes a parser may report; anything else from the child is not passed on.
KNOWN_CODES = frozenset({"file_unreadable", "file_encrypted", "too_many_pages", "image_too_large"})
_JPEG_START = b"\xff\xd8"


class IsolationError(Exception):
    """The child did not answer usefully: crashed, killed, over a limit, a malformed
    answer, or a parser's refusal (then `str()` is its code, else empty)."""


def _child_env() -> dict[str, str]:
    """Only what an interpreter needs to start; never a secret of the worker."""
    return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}


def _kill_group(process: "subprocess.Popen[bytes]") -> None:
    """Kill the child's process group (the child and all it forked, also after it
    answered) and reap the child. The parser child cannot fork at all (`RLIMIT_NPROC`
    in `parsers.set_limits`), so nothing of it can leave the group first."""
    # ProcessLookupError: the group is gone already.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def execute(
    command: list[str], data: bytes, *, timeout: float, answer_limit: int
) -> dict[str, object]:
    """Run `command` with `data` on stdin; its stdout as one JSON object.

    `IsolationError("")` when it does not exit 0 within `timeout`, writes more than
    `answer_limit` bytes, or writes anything but a JSON object with a boolean `ok`. The
    child runs in a session of its own; its whole process group is killed when it ends or
    its time is up.
    """
    try:
        with tempfile.TemporaryFile() as out:
            process = subprocess.Popen(  # noqa: S603 - our interpreter and script, no shell
                command,
                stdin=subprocess.PIPE,
                stdout=out,
                stderr=subprocess.DEVNULL,
                env=_child_env(),
                close_fds=True,
                # Its own process group: whatever it forks is killed with it.
                start_new_session=True,
            )
            try:
                process.communicate(input=data, timeout=timeout)
            finally:
                _kill_group(process)
            if process.returncode != 0 or os.fstat(out.fileno()).st_size > answer_limit:
                raise IsolationError("")
            out.seek(0)
            raw = out.read(answer_limit + 1)
    except (OSError, subprocess.TimeoutExpired):
        # Also a temporary directory that cannot be written: no answer, not a 500.
        raise IsolationError("") from None
    try:
        answer = json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError):
        # RecursionError: an answer nested deeper than the parser's stack (re-review of
        # PR #58, point 1), a refusal and not a 500.
        raise IsolationError("") from None
    if not isinstance(answer, dict) or not isinstance(answer.get("ok"), bool):
        raise IsolationError("")
    return answer


def _refusal(answer: dict[str, object]) -> IsolationError:
    code = answer.get("code")
    return IsolationError(code if isinstance(code, str) and code in KNOWN_CODES else "")


def _command(*arguments: str) -> list[str]:
    return [sys.executable, "-I", str(CHILD), *arguments]


def _cpu_seconds(timeout: float) -> str:
    return str(max(1, int(timeout) + 1))


def count_from(answer: dict[str, object]) -> int:
    if answer.get("ok") is not True:
        raise _refusal(answer)
    pages = answer.get("pages")
    if type(pages) is not int or pages < 0:
        raise IsolationError("")
    return pages


def pages_from(answer: dict[str, object], *, max_pages: int) -> list[ParsedPage]:
    if answer.get("ok") is not True:
        raise _refusal(answer)
    items = answer.get("pages")
    if not isinstance(items, list) or not 1 <= len(items) <= max_pages:
        raise IsolationError("")
    pages: list[ParsedPage] = []
    for item in items:
        if not isinstance(item, dict):
            raise IsolationError("")
        encoded = item.get("jpeg")
        if not isinstance(encoded, str) or len(encoded) > ANSWER_BYTES_PER_PAGE:
            raise IsolationError("")
        try:
            jpeg = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise IsolationError("") from None
        if not jpeg.startswith(_JPEG_START):
            raise IsolationError("")
        pages.append(ParsedPage(jpeg=jpeg))
    return pages


def count_pdf_pages(data: bytes, *, timeout: float) -> int:
    """PDFium counts a PDF's pages in the child."""
    command = _command("count", _cpu_seconds(timeout), str(MEMORY_LIMIT), str(COUNT_ANSWER_BYTES))
    return count_from(execute(command, data, timeout=timeout, answer_limit=COUNT_ANSWER_BYTES))


def render(data: bytes, *, kind: str, max_pages: int, timeout: float) -> list[ParsedPage]:
    """The pages of a PDF (`kind` "pdf") or a JPEG/PNG as JPEGs, rendered in the child."""
    pages = max_pages if kind == "pdf" else 1
    # The child's RLIMIT_FSIZE and the parent's read are the same number.
    limit = min(ANSWER_BYTES_PER_PAGE * pages, ANSWER_BYTES_MAX)
    operation = "pdf" if kind == "pdf" else "image"
    command = _command(
        operation, _cpu_seconds(timeout), str(MEMORY_LIMIT), str(limit), str(max_pages)
    )
    return pages_from(execute(command, data, timeout=timeout, answer_limit=limit), max_pages=pages)
