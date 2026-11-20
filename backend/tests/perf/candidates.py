"""Proposed forms of the slow list queries, measured next to today's services.

Each returns what the service returns (the measurement checks equal results on every
run) and needs `addressee_ids` (Task 7) where it reads it. They become the services in
Task 11 (lists, counts) and Task 37 (the preview) if the measurement bears them out.

- `list_documents`: today's service annotates `has_addressee` and `has_automatic` on
  every list, also when no filter reads them; PostgreSQL then builds both as hashed
  subplans over every addressee and label link of the workspace (each link checked
  against its letter's policy). Here they are only computed for the filter that needs
  them; "unassigned" reads `addressee_ids = '{}'` (the partial index
  `documents_document_unassigned`), a person filter `addressee_ids @> ARRAY[person]`
  (the GIN index).
- `inbox_counts`: one pass, no correlated subquery per letter: `addressee_ids = '{}'`
  for "no addressee", and the automatic links as two uncorrelated `IN` subqueries over
  the links marked automatic (hashed once).
- `list_persons`: the persons without the per-link count, then one grouped count over
  the letters the policy shows (`unnest(addressee_ids)`), instead of joining every
  addressee link and checking its letter's policy link by link. The count is the
  same: a link is visible exactly when its letter is.
"""

from typing import Final
from uuid import UUID

from django.db import connection, models
from django.db.models.expressions import RawSQL
from django.db.models.functions import Lower

from apps.documents.models import Document
from apps.documents.services import SUGGESTIONS
from apps.people.models import Person
from apps.workspaces.models import Workspace

_T: Final = '"documents_document"'
NO_ADDRESSEE: Final = f"{_T}.\"addressee_ids\" = '{{}}'::uuid[]"
AUTOMATIC: Final = (
    f'({_T}."confirmed_by_ai"'  # noqa: S608 - fixed fragments, no input
    f' OR {_T}."id" IN (SELECT l.document_id FROM public.documents_documentlabel l'
    " WHERE l.automatic)"
    f' OR {_T}."id" IN (SELECT a.document_id FROM public.documents_documentaddressee a'
    " WHERE a.automatic))"
)


def _raw(sql: str, params: list[str] | None = None) -> RawSQL:
    # Fixed fragments from this module; values only as parameters.
    return RawSQL(sql, params or [], output_field=models.BooleanField())  # noqa: S611


def list_documents(
    workspace: Workspace, *, inbox_state: str = "", person_id: UUID | None = None
) -> list[Document]:
    documents = Document.objects.filter(workspace=workspace)
    if inbox_state == "unassigned":
        documents = documents.filter(_raw(f"{_T}.\"status\" = 'done' AND {NO_ADDRESSEE}"))
    elif inbox_state:
        raise ValueError("Only the unassigned state is measured here.")
    if person_id is not None:
        documents = documents.filter(
            _raw(f'{_T}."addressee_ids" @> ARRAY[%s]::uuid[]', [str(person_id)])
        )
    return list(
        documents.prefetch_related("addressee_links", "label_links", SUGGESTIONS).order_by(
            "-created_at", "-id"
        )[:200]
    )


def inbox_counts(workspace: Workspace) -> dict[str, int]:
    counts = Document.objects.filter(workspace=workspace).aggregate(
        new=models.Count("id", filter=models.Q(status=Document.Status.PROCESSING)),
        review=models.Count(
            "id",
            filter=models.Q(status__in=(Document.Status.NEEDS_REVIEW, Document.Status.FAILED)),
        ),
        unassigned=models.Count(
            "id", filter=models.Q(status=Document.Status.DONE) & models.Q(_raw(NO_ADDRESSEE))
        ),
        automatic=models.Count("id", filter=models.Q(_raw(AUTOMATIC))),
    )
    return {name: int(counts[name]) for name in ("new", "review", "unassigned", "automatic")}


def list_persons(workspace: Workspace) -> list[Person]:
    persons = list(
        Person.objects.filter(workspace=workspace).order_by(
            models.ExpressionWrapper(
                models.Q(archived_at__isnull=False), output_field=models.BooleanField()
            ).asc(),
            Lower("display_name"),
            "id",
        )
    )
    with connection.cursor() as c:
        c.execute(
            "SELECT a.id, pg_catalog.count(*) FROM public.documents_document d,"
            " LATERAL pg_catalog.unnest(d.addressee_ids) AS a(id)"
            " WHERE d.workspace_id = %s GROUP BY a.id",
            [workspace.id],
        )
        counts = {row[0]: int(row[1]) for row in c.fetchall()}
    for person in persons:
        person.document_count = counts.get(person.id, 0)  # type: ignore[attr-defined]
    return persons
