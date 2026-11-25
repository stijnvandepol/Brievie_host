"""Keep other processes of the same user out of a process that holds secrets (Linux).

The web and worker processes hold the master key, `SECRET_KEY` and the database URLs in
their environment and memory. A process of the same user, such as the parser child of
`apps.intake.sandbox` taken over by a crafted file, could read them from
`/proc/<pid>/environ` or `/proc/<pid>/mem`. `PR_SET_DUMPABLE` 0 closes that: the kernel
then lets only a process with `CAP_SYS_PTRACE` read those files (and writes no core
dump). Children inherit it through `fork` but not through `exec`, so a parser child
started by `exec` stays a separate, unprivileged process.

Called first thing in `manage.py` (the workers: `procrastinate worker`), in gunicorn's
`on_starting` hook (`brievie.gunicorn_config`, before the master forks its workers) and in
`brievie.wsgi`. No dependency: `prctl` through `ctypes`. Elsewhere than Linux it does
nothing.
"""

import ctypes
import sys

PR_GET_DUMPABLE = 3
PR_SET_DUMPABLE = 4


def _prctl() -> "ctypes._NamedFuncPointer":
    function = ctypes.CDLL(None, use_errno=True).prctl
    function.restype = ctypes.c_int
    function.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    return function


def forbid_inspection() -> bool:
    """Make this process not dumpable; True when applied (Linux), False elsewhere.

    Raises `OSError` when the kernel refuses: a process that should be closed and is not
    must not start."""
    if not sys.platform.startswith("linux"):
        return False
    if _prctl()(PR_SET_DUMPABLE, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_DUMPABLE, 0) failed")
    return True


def is_dumpable() -> bool | None:
    """This process's dumpable flag; None elsewhere than Linux."""
    if not sys.platform.startswith("linux"):
        return None
    return int(_prctl()(PR_GET_DUMPABLE, 0, 0, 0)) != 0
