"""Run a parser of an untrusted file in a child process with limits (intake spec S 1.2).

PDFium and the image decoders are C code fed with a stranger's bytes. They run in a
spawned child (a fresh interpreter: no Django, no database connection, no keys in its
memory) with an address-space and CPU limit, and the parent kills it after `timeout`.
A crash, a kill or any exception in the child is `IsolationError`: the caller decides
what code the user sees. Nothing of the file or its error text is logged.

The child functions live in modules that import no Django (`apps.intake.parsers`), so
the spawned interpreter stays small.
"""

import multiprocessing
from collections.abc import Callable
from multiprocessing.connection import Connection
from typing import cast

# PDFium rendering a 2500 px page needs well under this; a decompression bomb hits it.
MEMORY_LIMIT = 1536 * 1024 * 1024


class IsolationError(Exception):
    """The child did not answer: crashed, killed, over a limit, or raised."""


def _limit(cpu_seconds: int) -> None:
    try:
        import resource
    except ImportError:  # pragma: no cover - not on Linux or macOS
        return
    for limit, value in ((resource.RLIMIT_AS, MEMORY_LIMIT), (resource.RLIMIT_CPU, cpu_seconds)):
        try:
            resource.setrlimit(limit, (value, value))
        except (ValueError, OSError):
            # macOS refuses RLIMIT_AS; the parent's timeout still holds.
            continue


def _child[T](
    target: Callable[[bytes], T], data: bytes, cpu_seconds: int, answer: Connection
) -> None:
    _limit(cpu_seconds)
    try:
        answer.send(("ok", target(data)))
    except Exception as error:  # noqa: BLE001 - only a code leaves the child
        answer.send(("error", getattr(error, "code", "")))
    finally:
        answer.close()


def run_isolated[T](target: Callable[[bytes], T], data: bytes, *, timeout: float) -> T:
    """`target(data)` in a spawned child; `IsolationError(code)` when it did not succeed
    (`code` is a `code` attribute of the child's exception, else empty)."""
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(
        target=_child, args=(target, data, max(1, int(timeout) + 1), send), daemon=True
    )
    process.start()
    send.close()
    try:
        if not receive.poll(timeout):
            raise IsolationError("")
        try:
            status, value = receive.recv()
        except (EOFError, OSError):
            raise IsolationError("") from None
    finally:
        if process.is_alive():
            process.kill()
        process.join(1)
        receive.close()
    if status != "ok":
        raise IsolationError(str(value))
    return cast("T", value)
