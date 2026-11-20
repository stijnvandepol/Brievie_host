"""Who may run as the system (PB T2, plan Task 4): a closed list of files.

`system_actor()` and `actor=SYSTEM` see every letter of the workspace. A new caller is a
decision: it is added here in the same commit, with the reason in its own code.
"""

from pathlib import Path

APPS = Path(__file__).resolve().parent.parent / "apps"

# Grows to the list in the plan's Global Constraints as the tasks add their files.
ALLOWED_SYSTEM_CALLERS = {
    "apps/pipeline/steps.py",
    "apps/pipeline/jobs.py",
    "apps/documents/export.py",
}


def _sources() -> dict[str, str]:
    root = APPS.parent
    return {str(p.relative_to(root)): p.read_text() for p in sorted(APPS.rglob("*.py"))}


def test_system_callers_are_the_known_list() -> None:
    callers = {
        name
        for name, text in _sources().items()
        if name != "apps/workspaces/context.py"
        and ("system_actor(" in text or "actor=SYSTEM" in text)
    }
    assert callers == ALLOWED_SYSTEM_CALLERS


def test_actor_setting_is_written_only_by_context() -> None:
    writers = {
        name
        for name, text in _sources().items()
        if "set_config(" in text and ("brievie.actor" in text or "ACTOR_SETTING" in text)
    }
    assert writers == {"apps/workspaces/context.py"}
