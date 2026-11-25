"""The covering indexes hold every column the letter policy reads (DECISIONS 2026-11-25).

Pure: reads the policy's SQL text and the model's indexes. A column added to the policy
but not to `POLICY_COLUMNS` would not change a result, only send every count over the
letters back to the wide rows (index-only scans need every column the query touches).
"""

import re

from django.db.models import Field

from apps.documents.models import COUNTED_COLUMNS, LIVE, POLICY_COLUMNS, Document
from apps.workspaces.visibility_sql import document_condition


def _db_columns(fields: tuple[str, ...] | list[str]) -> set[str]:
    columns = set()
    for name in fields:
        field = Document._meta.get_field(name)
        assert isinstance(field, Field)
        columns.add(str(field.column))
    return columns


def _policy_columns() -> set[str]:
    condition = document_condition(
        user="u", sees_all="s", my_ids="m", sorts="o", covered="c", table="d"
    )
    # Columns of the letter only: `d.<name>`, not the reader table's `r.<name>`.
    return set(re.findall(r"\bd\.([a-z_]+)", condition))


def test_policy_columns_cover_the_policy_and_the_trash() -> None:
    held = _db_columns(POLICY_COLUMNS) | {"id"}
    assert _policy_columns() <= held, _policy_columns() - held
    trash = {child[0].split("__")[0] for child in LIVE.children if isinstance(child, tuple)}
    assert _db_columns(tuple(trash)) <= held


def test_policy_indexes_hold_the_policy_columns() -> None:
    indexes = {index.name: index for index in Document._meta.indexes}
    needed = _db_columns(POLICY_COLUMNS) | {"id"}
    for name in (
        "documents_document_policy",
        "documents_document_review",
        "documents_document_unassigned",
    ):
        index = indexes[name]
        held = _db_columns((*(field for field, _ in index.fields_orders), *index.include))
        assert needed <= held, (name, needed - held)
    policy = indexes["documents_document_policy"]
    assert _db_columns(COUNTED_COLUMNS) <= _db_columns(tuple(policy.include))
