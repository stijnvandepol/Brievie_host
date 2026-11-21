"""One seam for every query that shows letters (S 4.10, plan Task 23).

Search, the trash and duplicate detection came on their own branches; whatever comes
next must obey the same rule. These tests make forgetting it fail:

- the member-facing document modules read letters only through
  `visibility.visible_documents` (and its trash siblings); a direct `Document.objects`
  there is a known row lock for writing by id, under the caller's actor;
- raw SQL that names the letter table lives only in known modules, each of which runs it
  as the system on purpose or as the caller under the policies;
- a new table with a letter key without the visibility policies fails the coverage.
"""

import ast
import re
from pathlib import Path
from typing import Final

from apps.documents import visibility
from tests.test_rls_coverage import missing_visibility_policies

APPS: Final = Path(__file__).resolve().parent.parent / "apps"

# Member-facing modules: what they read, they read for one member.
MEMBER_FACING: Final = ("documents/services.py", "documents/search.py", "documents/trash.py")

# Functions there that name `Document.objects` directly: each locks one letter by id
# for a write, as the caller (the policies answer a hidden letter like a missing one).
DIRECT_LOCKS: Final = {
    ("documents/services.py", "retry_processing"),
    ("documents/services.py", "reopen_document"),
    ("documents/services.py", "_lock_for_organising"),
}

# The reads that start from the seam.
SEAM_READS: Final = {
    ("documents/services.py", "list_documents"),
    ("documents/services.py", "inbox_counts"),
    ("documents/services.py", "get_document"),
    ("documents/search.py", "_filtered"),
    ("documents/trash.py", "trash_document"),
    ("documents/trash.py", "restore_document"),
    ("documents/trash.py", "list_trash"),
    ("documents/trash.py", "count_trash"),
    ("documents/trash.py", "purge_now"),
    ("documents/trash.py", "empty_trash"),
}
SEAMS: Final = {"visible_documents", "trashed_documents", "readable_documents"}

# Every module with raw SQL on the letter table, and why it may.
RAW_SQL: Final = {
    # The policies and the visibility functions themselves.
    "workspaces/rls.py",
    "workspaces/visibility_sql.py",
    # The one writer of addressee_ids and the nightly check, as the system.
    "documents/visibility.py",
    # Duplicate candidates, as the system; a pair is shown through `visible_pairs`.
    "documents/duplicates.py",
    # Grouped counts of the persons and labels lists, as the caller under the policies.
    "people/services.py",
    "labels/services.py",
    # The back-up's counts and the check of a restored copy, by the back-up role.
    "backup/manifest.py",
    "backup/verify.py",
}
_TABLE: Final = re.compile(r"\bdocuments_document\b")


def _tree(relative: str) -> ast.Module:
    return ast.parse((APPS / relative).read_text(encoding="utf-8"))


def _functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}


def _names_document_objects(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Attribute)
        and n.attr in {"objects", "_default_manager"}
        and isinstance(n.value, ast.Name)
        and n.value.id == "Document"
        for n in ast.walk(node)
    )


def _calls_a_seam(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Attribute) and n.attr in SEAMS and isinstance(n.value, ast.Name)
        for n in ast.walk(node)
    ) or any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "get_document"
        for n in ast.walk(node)
    )


def test_document_lists_start_from_visible_documents() -> None:
    direct = set()
    for module in MEMBER_FACING:
        tree = _tree(module)
        functions = _functions(tree)
        direct |= {(module, name) for name, f in functions.items() if _names_document_objects(f)}
        # Nothing outside a function either (a module-level query would have no actor).
        top = [n for n in tree.body if not isinstance(n, ast.FunctionDef | ast.ClassDef)]
        assert not any(_names_document_objects(n) for n in top), module
    assert direct == DIRECT_LOCKS

    for module, name in SEAM_READS:
        function = _functions(_tree(module)).get(name)
        assert function is not None, (module, name)
        assert _calls_a_seam(function), (module, name)


def test_raw_sql_on_documents_runs_with_an_actor() -> None:
    found = set()
    for path in sorted(APPS.rglob("*.py")):
        relative = str(path.relative_to(APPS))
        if "/migrations/" in f"/{relative}":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(n, ast.Constant) and isinstance(n.value, str) and _TABLE.search(n.value)
            for n in ast.walk(tree)
        ):
            found.add(relative)
    assert found == RAW_SQL


def test_a_new_document_child_table_without_policies_fails_coverage() -> None:
    # A workspace table without a letter policy stands in for a forgotten one.
    missing = missing_visibility_policies(["documents_folder"])
    assert missing
    assert {table for table, _policy in missing} == {"documents_folder"}
    # A table that follows its letter passes.
    assert missing_visibility_policies(["documents_documentlabel"]) == []


def test_the_seam_says_what_it_promises() -> None:
    doc = visibility.visible_documents.__doc__ or ""
    for phrase in ("search", "trash", "duplicate", "never"):
        assert phrase in doc.lower(), phrase
