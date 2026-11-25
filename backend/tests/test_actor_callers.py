"""Who may run as the system (PB T2, plan Task 4): a closed list of files.

`system_actor()` and `actor=SYSTEM` see every letter of the workspace. A new caller is a
decision: it is added here in the same commit, with the reason in its own code.
"""

import re
from pathlib import Path

APPS = Path(__file__).resolve().parent.parent / "apps"

# Grows to the list in the plan's Global Constraints as the tasks add their files.
ALLOWED_SYSTEM_CALLERS = {
    # WorkspaceModel.save: the workspace of a related letter the actor may not see.
    "apps/common/models.py",
    "apps/pipeline/steps.py",
    "apps/pipeline/jobs.py",
    "apps/documents/export.py",
    # The one writer of addressee_ids and reader rows, and the nightly check.
    "apps/documents/visibility.py",
    # Confirm: the repeat is recognised and the status written as the system (S 4.9.3).
    "apps/documents/review.py",
    # Deletes, archiving and a repeated upload reach letters the caller cannot see,
    # after the authorisation as the caller (plan Task 10).
    "apps/people/services.py",
    "apps/labels/services.py",
    "apps/rules/services.py",
    "apps/documents/intake.py",
    # Intake: the pipeline's first step reads and writes the uploaded file's pages.
    "apps/intake/prepare.py",
    # The purge of the trash reaches every trashed letter and its reader rows (it marks
    # what expired and deletes marked letters); the request paths of the trash never do.
    "apps/documents/purge.py",
    # Mijn taken: an assignment is written after the reader check, and released when the
    # assignee loses sight of the letter, which the current actor may not see (S 15).
    "apps/tasks/assignment.py",
    # The private-post seams answer yes or no about letters the caller may not see
    # (invitations, member removal, account actions), PB T8.
    "apps/workspaces/access.py",
    # Who sees what: the snapshot, the preview's viewer count and the release after a
    # change of sight read every member's and letter's facts (plan Task 37).
    "apps/workspaces/visibility_settings.py",
    # "Ongelezen": whether an addressee opened a letter is written as the system after the
    # member's own mark, and recomputed when its addressees change (navigation S 5.5).
    "apps/documents/reading.py",
    # The end-date reminder of an own document is made as the system, with no actor
    # (archive spec S 7.1, AR6), after the job or the editor's change was authorised.
    "apps/documents/reminders.py",
    # Fills `addressee_opened_at` of every confirmed letter (documents/0022).
    "apps/documents/migrations/0022_read_marks.py",
    # Letter status: completing a task deletes every member's snooze row of it (the own-rows
    # policy would let the caller delete only their own).
    "apps/tasks/services.py",
    # Letter status: the one writer of a letter's task facts reads all its tasks and
    # writes them after the caller was authorised (a confirm may hand the letter away),
    # and the nightly repair; the migration fills them for every letter.
    "apps/tasks/letter_sync.py",
    "apps/documents/migrations/0024_letter_status.py",
    # The same statement once more, joining no task of an own document (archive AR7).
    "apps/documents/migrations/0030_letter_facts_letters_only.py",
    # Fills the derived automatic flag of every letter (documents/0014).
    "apps/documents/migrations/0014_document_has_automatic_link.py",
}


def _sources() -> dict[str, str]:
    root = APPS.parent
    return {str(p.relative_to(root)): p.read_text() for p in sorted(APPS.rglob("*.py"))}


# Running as the system needs `system_actor` or `SYSTEM` from the context module.
_CALLS = re.compile(r"system_actor\(|actor=SYSTEM")
_IMPORTS = re.compile(r"from apps\.workspaces\.context import (\([^)]*\)|[^\n]*)")


def _uses_system(text: str) -> bool:
    if _CALLS.search(text):
        return True
    names = {
        name.strip()
        for found in _IMPORTS.finditer(text)
        for name in found.group(1).strip("()").replace("\n", " ").split(",")
    }
    return bool(names & {"SYSTEM", "system_actor"})


def test_system_callers_are_the_known_list() -> None:
    callers = {
        name
        for name, text in _sources().items()
        if name != "apps/workspaces/context.py" and _uses_system(text)
    }
    assert callers == ALLOWED_SYSTEM_CALLERS


def test_actor_setting_is_written_only_by_context() -> None:
    writers = {
        name
        for name, text in _sources().items()
        if "set_config(" in text and ("brievie.actor" in text or "ACTOR_SETTING" in text)
    }
    assert writers == {"apps/workspaces/context.py"}
