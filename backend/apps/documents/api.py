from uuid import UUID

from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from ninja import File, Form, Router, Status
from ninja.files import UploadedFile
from pydantic import ValidationError

from apps.accounts.auth import session_auth
from apps.accounts.services import current_user
from apps.audit.services import actor_from_request
from apps.common.errors import ApiError
from apps.common.schemas import AUTH_ERRORS, BODY_ERRORS, SESSION_WRITE_ERRORS, ErrorOut
from apps.common.throttling import CallerThrottle
from apps.documents import (
    handle,
    intake,
    member_export,
    own,
    reading,
    review,
    search,
    services,
    trash,
)
from apps.documents.models import DocumentEvent, Folder, MemberExport
from apps.documents.schemas import (
    AddresseesIn,
    ArchiveFacetsOut,
    ConfirmIn,
    DocumentAccessOut,
    DocumentDetailsIn,
    DocumentEventOut,
    DocumentOut,
    DocumentSearchIn,
    DocumentSearchOut,
    DocumentSummaryOut,
    DocumentViewerOut,
    DocumentVisibilityIn,
    DocumentVisibilityOut,
    EmptyTrashIn,
    EmptyTrashOut,
    FacetOut,
    FolderIn,
    FolderOut,
    HandleDocumentIn,
    InboxCountsOut,
    LabelsIn,
    LetterStatusCountsOut,
    LingeringOut,
    MarkReadIn,
    MarkReadOut,
    MenuCountsOut,
    MyExportOut,
    TrashedDocumentOut,
    TrashListOut,
    UploadMetadataIn,
)
from apps.documents.upload_limits import check_upload_limits
from apps.workspaces.deps import workspace_member
from apps.workspaces.models import Membership

router = Router(tags=["documents"])

_MY_EXPORT = "/workspaces/{workspace_id}/my-export"
WEB_SESSION_ONLY = "Web session only: a bearer token answers 401 not_authenticated."


@router.get(
    "/workspaces/{workspace_id}/folders",
    response={200: list[FolderOut], 404: ErrorOut, **AUTH_ERRORS},
    operation_id="listFolders",
)
def list_folders(request: HttpRequest, workspace_id: UUID) -> list[Folder]:
    membership = workspace_member(request, workspace_id)
    return services.list_folders(membership.workspace)


@router.post(
    "/workspaces/{workspace_id}/folders",
    response={201: FolderOut, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS, **BODY_ERRORS},
    operation_id="createFolder",
)
def create_folder(request: HttpRequest, workspace_id: UUID, payload: FolderIn) -> Status[Folder]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    folder = services.create_folder(
        workspace=membership.workspace, name=payload.name, parent_id=payload.parent_id
    )
    return Status(201, folder)


@router.post(
    "/workspaces/{workspace_id}/documents",
    response={
        201: DocumentOut,
        200: DocumentOut,
        404: ErrorOut,
        409: ErrorOut,
        411: ErrorOut,
        429: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    operation_id="createDocument",
    description=(
        "Upload a scanned document as multipart/form-data: `metadata` is a JSON string "
        "(schema `UploadMetadataIn`), followed by one `page` file (image/jpeg) per page "
        "in page order. Repeating an upload with the same `client_id` and pages answers "
        "200 with the existing document. 429 `too_many_uploads` with Retry-After when the "
        "member or the workspace uploaded too much in a short time (by default 60 per "
        "member per 10 minutes, 600 per workspace per hour). With `kind` `document` in the "
        "metadata it is an own document for the archive: `title` and `document_type` (an own "
        "document's type) are required, `person_ids` and `label_ids` organise it at once, "
        "`private` makes it private with the uploader as a reader; it skips the Inbox, gets "
        "no tasks and ends `done` after processing. A letter with any of these fields is 422 "
        "`validation_error`; a read-only member gets 403."
    ),
)
def create_document(
    request: HttpRequest,
    workspace_id: UUID,
    metadata: Form[str],
    page: File[list[UploadedFile]],
) -> Status[DocumentOut]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    user = current_user(request)
    check_upload_limits(request, workspace_id=workspace_id, user_id=user.id)
    try:
        parsed = UploadMetadataIn.model_validate_json(metadata)
    except ValidationError:
        # The error text would repeat the input (page text): not logged, not returned.
        raise ApiError(
            422, "validation_error", _("De gegevens bij deze upload kloppen niet.")
        ) from None
    document, created = intake.create_document_from_upload(
        workspace=membership.workspace,
        user=user,
        metadata=parsed,
        pages=[file.read() for file in page],
        actor=actor_from_request(request),
    )
    return Status(201 if created else 200, services.document_out(document))


@router.get(
    "/workspaces/{workspace_id}/documents",
    response={200: list[DocumentSummaryOut], 404: ErrorOut, 422: ErrorOut, **AUTH_ERRORS},
    operation_id="listDocuments",
    description=(
        "The newest 200 documents. `inbox_state` (`new`, `review` or `unassigned`), "
        "`person_id`, `label_id` and `automatic` narrow the list; together they all apply. "
        "`automatic=true` keeps the documents the AI confirmed itself or put a label or "
        "addressee on (see `confirmed_by_ai`, `auto_label_ids`, `auto_addressee_ids`). "
        '`addressed_to_me=true` keeps "Mijn post": the letters on the caller\'s own persons '
        "(their own and those they manage), with `include_departments=true` also on their "
        "departments; empty for a member without a person. `unread_only=true` keeps the "
        "letters the caller has not read (`unread`). `letter_status` (`new`, `open`, `waiting` or "
        "`handled`; 422 for another) and `overdue=true` keep the letters in that status, "
        'with the marker "Te laat"; `new` here is the letter not checked yet, not `unread`. '
        "Archief (archive spec S 5): `archived=true` keeps the reviewed documents (`done`) of "
        "both kinds; `kind` (`letter` or `document`), `document_type`, `year` (the document's "
        "own date, else its upload's), `sender` (exactly, a value of `getArchiveFacets`) and "
        "`expiring=true` (own documents ending within 90 days or already ended) narrow it. "
        "422 `validation_error` for an unknown kind, type or year."
    ),
)
def list_documents(
    request: HttpRequest,
    workspace_id: UUID,
    inbox_state: str = "",
    person_id: UUID | None = None,
    label_id: UUID | None = None,
    automatic: bool = False,
    addressed_to_me: bool = False,
    include_departments: bool = False,
    unread_only: bool = False,
    letter_status: str = "",
    overdue: bool = False,
    kind: str = "",
    document_type: str = "",
    year: int = 0,
    sender: str = "",
    archived: bool = False,
    expiring: bool = False,
) -> list[DocumentSummaryOut]:
    membership = workspace_member(request, workspace_id)
    documents = services.list_documents(
        membership,
        inbox_state=inbox_state,
        person_id=person_id,
        label_id=label_id,
        automatic=automatic,
        addressed_to_me=addressed_to_me,
        include_departments=include_departments,
        unread_only=unread_only,
        letter_status=letter_status,
        overdue=overdue,
        archive=services.ArchiveFilters(
            kind=kind,
            document_type=document_type,
            year=year,
            sender=sender,
            archived=archived,
            expiring=expiring,
        ),
    )
    return services.document_summaries_out(membership, documents)


@router.get(
    "/workspaces/{workspace_id}/documents/archive-facets",
    response={200: ArchiveFacetsOut, 404: ErrorOut, **AUTH_ERRORS},
    operation_id="getArchiveFacets",
    description=(
        "The choices of Archief's filters with their counts, over the reviewed documents "
        "(`done`) the caller sees: `kinds`, `types`, `years` (newest first) and the 50 most "
        "frequent `senders`. Nothing the caller may not read is counted. Any role, session "
        "or device."
    ),
)
def get_archive_facets(request: HttpRequest, workspace_id: UUID) -> ArchiveFacetsOut:
    membership = workspace_member(request, workspace_id)
    facets = services.list_archive_facets(membership)
    return ArchiveFacetsOut(
        kinds=[FacetOut(value=f.value, count=f.count) for f in facets.kinds],
        types=[FacetOut(value=f.value, count=f.count) for f in facets.types],
        years=[FacetOut(value=f.value, count=f.count) for f in facets.years],
        senders=[FacetOut(value=f.value, count=f.count) for f in facets.senders],
    )


@router.get(
    "/workspaces/{workspace_id}/documents/status-counts",
    response={200: LetterStatusCountsOut, 404: ErrorOut, **AUTH_ERRORS},
    operation_id="countLetterStatuses",
    description=(
        "How many letters the caller sees per status (`new`, `open`, `waiting`, `handled`) "
        "and how many are late (`overdue`), of all post or, with `person_id`, of one "
        "person's. Only letters the caller may read are counted; an unknown or archived "
        "person gives zeros."
    ),
)
def count_letter_statuses(
    request: HttpRequest, workspace_id: UUID, person_id: UUID | None = None
) -> LetterStatusCountsOut:
    membership = workspace_member(request, workspace_id)
    counts = services.status_counts(membership, person_id=person_id)
    return LetterStatusCountsOut(
        new=counts.new,
        open=counts.open,
        waiting=counts.waiting,
        handled=counts.handled,
        overdue=counts.overdue,
    )


@router.post(
    "/workspaces/{workspace_id}/documents/read",
    response={200: MarkReadOut, 404: ErrorOut, **SESSION_WRITE_ERRORS, **BODY_ERRORS},
    operation_id="markDocumentsRead",
    description=(
        '"Alles gelezen": mark the given letters read for the caller (at most 200; 422 '
        "`validation_error` for more). Ids the caller does not see are skipped without a "
        "word. `marked`: how many were newly marked. The caller's own state only: nobody "
        "else sees it; no history line, no log."
    ),
)
def mark_documents_read(
    request: HttpRequest, workspace_id: UUID, payload: MarkReadIn
) -> MarkReadOut:
    membership = workspace_member(request, workspace_id)
    return MarkReadOut(marked=reading.mark_many_read(membership, payload.document_ids))


@router.post(
    "/workspaces/{workspace_id}/documents/search",
    response={
        200: DocumentSearchOut,
        404: ErrorOut,
        429: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    throttle=CallerThrottle("document_search", "30/1m"),
    operation_id="searchDocuments",
    description=(
        "Search the letters of the workspace by their text, sender, summary, label names "
        "and person names; every word must match, as a word beginning (Dutch stems too). "
        "Best first, then newest. The filters narrow the search; together they all apply. "
        "When nothing matches exactly, letters with similar words are answered with "
        "`fuzzy=true`. A BSN in `q` is removed before searching (`bsn_removed`). "
        "`next_offset` is null on the last page (offset at most 1000). Each hit has a "
        "fragment (`snippet`) with the matched words marked. The term is never logged. "
        "422 `validation_error` for a term over 200 characters, an unknown `inbox_state` "
        "or `document_type`, or `date_from` after `date_to`; 429 after 30 searches a minute. "
        "Archief: `kind`, `year` and `archived` narrow as in `listDocuments`; an own "
        "document is found by its title too."
    ),
)
def search_documents(
    request: HttpRequest, workspace_id: UUID, payload: DocumentSearchIn
) -> DocumentSearchOut:
    membership = workspace_member(request, workspace_id)
    result = search.search_documents(
        membership,
        q=payload.q,
        filters=search.SearchFilters(
            document_type=payload.document_type,
            label_id=payload.label_id,
            person_id=payload.person_id,
            inbox_state=payload.inbox_state,
            date_from=payload.date_from,
            date_to=payload.date_to,
            kind=payload.kind,
            year=payload.year,
            archived=payload.archived,
        ),
        limit=payload.limit,
        offset=payload.offset,
    )
    return search.search_out(result)


@router.get(
    "/workspaces/{workspace_id}/inbox/counts",
    response={200: InboxCountsOut, 404: ErrorOut, **AUTH_ERRORS},
    operation_id="getInboxCounts",
)
def get_inbox_counts(request: HttpRequest, workspace_id: UUID) -> InboxCountsOut:
    membership = workspace_member(request, workspace_id)
    return InboxCountsOut(**services.inbox_counts(membership))


@router.get(
    "/workspaces/{workspace_id}/menu/counts",
    response={200: MenuCountsOut, 404: ErrorOut, **AUTH_ERRORS},
    operation_id="getMenuCounts",
    description=(
        "The numbers of the menu in one call: letters waiting in the Inbox, unread letters "
        'in "Mijn post", open and overdue tasks of "Mijn taken", and "Blijft liggen" for the '
        "sorters and admins of an organisation. Only this workspace; nothing the caller may "
        "not see is counted. `getInboxCounts` and `countMyTasks` stay as they are."
    ),
)
def get_menu_counts(request: HttpRequest, workspace_id: UUID) -> MenuCountsOut:
    membership = workspace_member(request, workspace_id)
    counts = services.menu_counts(membership)
    return MenuCountsOut(
        inbox=counts.inbox,
        my_post_unread=counts.my_post_unread,
        my_tasks=counts.my_tasks,
        my_tasks_overdue=counts.my_tasks_overdue,
        lingering=counts.lingering,
        shows_lingering=counts.shows_lingering,
        has_person=counts.has_person,
    )


@router.get(
    "/workspaces/{workspace_id}/inbox/lingering",
    response={200: LingeringOut, 404: ErrorOut, **AUTH_ERRORS},
    operation_id="listLingeringLetters",
    description=(
        '"Blijft liggen", for the sorters and admins of an organisation with it switched on '
        "(`lingeringEnabled`): letters too long in the Inbox, and confirmed letters none of "
        "whose addressees opened them, oldest first, at most 100 each, only letters the "
        "caller may see. Empty lists for everyone else and at home."
    ),
)
def list_lingering_letters(request: HttpRequest, workspace_id: UUID) -> LingeringOut:
    membership = workspace_member(request, workspace_id)
    return services.lingering_out(membership)


@router.get(
    "/workspaces/{workspace_id}/documents/{document_id}",
    response={200: DocumentOut, 404: ErrorOut, **AUTH_ERRORS},
    operation_id="getDocument",
)
def get_document(request: HttpRequest, workspace_id: UUID, document_id: UUID) -> DocumentOut:
    membership = workspace_member(request, workspace_id)
    document = services.get_document(membership=membership, document_id=document_id)
    return services.document_out_as(membership, document)


@router.post(
    "/workspaces/{workspace_id}/documents/{document_id}/read",
    response={204: None, 404: ErrorOut, **SESSION_WRITE_ERRORS},
    operation_id="markDocumentRead",
    description=(
        "The caller opened the letter: call once when the letter page or the reader shows "
        "it (a plain `getDocument` marks nothing). Idempotent. 404 as `getDocument`. The "
        "caller's own state only: nobody else sees it; no history line, no log."
    ),
)
def mark_document_read(request: HttpRequest, workspace_id: UUID, document_id: UUID) -> Status[None]:
    membership = workspace_member(request, workspace_id)
    reading.mark_read(membership, document_id)
    return Status(204, None)


@router.delete(
    "/workspaces/{workspace_id}/documents/{document_id}/read",
    response={204: None, 404: ErrorOut, **SESSION_WRITE_ERRORS},
    operation_id="markDocumentUnread",
    description=(
        '"Markeer als ongelezen": the caller\'s own mark goes; the letter is unread for them '
        "again. Idempotent. 404 as `getDocument`."
    ),
)
def mark_document_unread(
    request: HttpRequest, workspace_id: UUID, document_id: UUID
) -> Status[None]:
    membership = workspace_member(request, workspace_id)
    reading.mark_unread(membership, document_id)
    return Status(204, None)


@router.get(
    "/workspaces/{workspace_id}/documents/{document_id}/access",
    response={200: DocumentAccessOut, 404: ErrorOut, **AUTH_ERRORS},
    operation_id="getDocumentAccess",
    description="Who can see this letter, and why, sorted by name; for any member who sees"
    " it (404 document_not_found otherwise, as for a letter that does not exist).",
)
def get_document_access(
    request: HttpRequest, workspace_id: UUID, document_id: UUID
) -> DocumentAccessOut:
    membership = workspace_member(request, workspace_id)
    rows = services.document_access(membership=membership, document_id=document_id)
    return DocumentAccessOut(
        viewers=[
            DocumentViewerOut(user_id=user_id, name=name, reasons=reasons)
            for user_id, name, reasons in rows
        ]
    )


@router.get(
    "/workspaces/{workspace_id}/documents/{document_id}/pages/{index}",
    response={404: ErrorOut, **AUTH_ERRORS},
    operation_id="getDocumentPage",
    openapi_extra={
        "responses": {
            200: {
                "description": "The page image",
                "content": {"image/jpeg": {"schema": {"type": "string", "format": "binary"}}},
            }
        }
    },
)
def get_document_page(
    request: HttpRequest, workspace_id: UUID, document_id: UUID, index: int
) -> HttpResponse:
    membership = workspace_member(request, workspace_id)
    data, content_type = services.read_page(
        membership=membership, document_id=document_id, index=index
    )
    response = HttpResponse(data, content_type=content_type)
    response["Cache-Control"] = "private, no-store"
    return response


@router.post(
    "/workspaces/{workspace_id}/documents/{document_id}/retry",
    response={202: DocumentOut, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS},
    operation_id="retryDocumentProcessing",
)
def retry_document_processing(
    request: HttpRequest, workspace_id: UUID, document_id: UUID
) -> Status[DocumentOut]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    document = services.retry_processing(workspace=membership.workspace, document_id=document_id)
    return Status(202, services.document_out(document))


@router.post(
    "/workspaces/{workspace_id}/documents/{document_id}/confirm",
    response={
        200: DocumentOut,
        404: ErrorOut,
        409: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    operation_id="confirmDocument",
    description=(
        "Store the review; the document becomes done. When the confirm hands the letter to"
        " someone else (the caller no longer sees it), the answer has `can_read` false and"
        " holds the id and status only. A repeat of the same review answers the same."
    ),
)
def confirm_document(
    request: HttpRequest, workspace_id: UUID, document_id: UUID, payload: ConfirmIn
) -> DocumentOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    document = review.confirm_document(
        workspace=membership.workspace,
        user=current_user(request),
        document_id=document_id,
        data=payload,
    )
    return services.document_out_for(document)


@router.post(
    "/workspaces/{workspace_id}/documents/{document_id}/reopen",
    response={200: DocumentOut, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS},
    description="Takes back the AI's confirmation: the document returns to review, the confirmed"
    " fields are emptied and the tasks the AI made that are still open are removed."
    " 409 document_not_reopenable unless the AI confirmed the document.",
    operation_id="reopenDocument",
)
def reopen_document(request: HttpRequest, workspace_id: UUID, document_id: UUID) -> DocumentOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    document = services.reopen_document(
        workspace=membership.workspace, user=current_user(request), document_id=document_id
    )
    return services.document_out_for(document)


@router.post(
    "/workspaces/{workspace_id}/documents/{document_id}/handle",
    response={
        200: DocumentOut,
        404: ErrorOut,
        409: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    operation_id="handleDocument",
    description=(
        '"Brief afhandelen": ticks off every open task of a checked letter after the '
        "confirmation. `task_ids` must be exactly the letter's open tasks (waiting ones "
        "included): 409 `tasks_changed` otherwise, nothing is completed. With a payment "
        "among them `payments_confirmed` must be true (409 `payment_not_confirmed`). 409 "
        "`document_not_reviewed` for a letter not checked yet. An already handled letter "
        "with no ids changes nothing. Writes one `task_completed` line per task with the "
        "caller and one `letter_handled` line."
    ),
)
def handle_document(
    request: HttpRequest, workspace_id: UUID, document_id: UUID, payload: HandleDocumentIn
) -> DocumentOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    document = handle.handle_document(
        membership=membership,
        user=current_user(request),
        document_id=document_id,
        task_ids=payload.task_ids,
        payments_confirmed=payload.payments_confirmed,
    )
    return services.document_out_for(document)


@router.patch(
    "/workspaces/{workspace_id}/documents/{document_id}/details",
    response={
        200: DocumentOut,
        404: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    operation_id="updateDocumentDetails",
    description=(
        "Change an own document's title, type, date (`document_date`), end date "
        "(`expires_on`, cleared with `clear_expires_on`) with its reminder lead "
        '(`remind_days`: 7, 30, 60 or 90; 0 leaves it) and "Bewaren tot" (`retain_until`, '
        "cleared with `clear_retain_until`); a letter takes only `retain_until`. Only what is "
        "sent changes; an empty text or a missing date leaves the field. Members (a read-only "
        "member gets 403; emergency access is read-only: 403 `emergency_read_only`). 404 "
        "`document_not_found` for a document the caller does not see or that is in the trash; "
        "422 `validation_error` for a type of the other kind, a field a letter has not, or a "
        "date out of range. One history line per changed field (`details_changed`, "
        "`retention_changed`)."
    ),
)
def update_document_details(
    request: HttpRequest, workspace_id: UUID, document_id: UUID, payload: DocumentDetailsIn
) -> DocumentOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    document = own.update_details(
        membership=membership,
        user=current_user(request),
        document_id=document_id,
        data=payload,
    )
    return services.document_out_for(document)


@router.put(
    "/workspaces/{workspace_id}/documents/{document_id}/addressees",
    response={
        200: DocumentOut,
        404: ErrorOut,
        409: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    operation_id="setDocumentAddressees",
)
def set_document_addressees(
    request: HttpRequest, workspace_id: UUID, document_id: UUID, payload: AddresseesIn
) -> DocumentOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    document = services.set_document_addressees(
        workspace=membership.workspace,
        user=current_user(request),
        document_id=document_id,
        person_ids=payload.person_ids,
    )
    return services.document_out_for(document)


@router.put(
    "/workspaces/{workspace_id}/documents/{document_id}/labels",
    response={
        200: DocumentOut,
        404: ErrorOut,
        409: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    operation_id="setDocumentLabels",
)
def set_document_labels(
    request: HttpRequest, workspace_id: UUID, document_id: UUID, payload: LabelsIn
) -> DocumentOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    document = services.set_document_labels(
        workspace=membership.workspace,
        user=current_user(request),
        document_id=document_id,
        label_ids=payload.label_ids,
    )
    return services.document_out_for(document)


@router.post(
    "/workspaces/{workspace_id}/documents/{document_id}/suggestions/{suggestion_id}/accept",
    response={
        200: DocumentOut,
        404: ErrorOut,
        409: ErrorOut,
        422: ErrorOut,
        **SESSION_WRITE_ERRORS,
    },
    operation_id="acceptSuggestion",
    description="Make the proposed change. Accepting an accepted proposal changes nothing.",
)
def accept_suggestion(
    request: HttpRequest, workspace_id: UUID, document_id: UUID, suggestion_id: UUID
) -> DocumentOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    document = services.accept_suggestion(
        workspace=membership.workspace,
        user=current_user(request),
        document_id=document_id,
        suggestion_id=suggestion_id,
    )
    return services.document_out_for(document)


@router.post(
    "/workspaces/{workspace_id}/documents/{document_id}/suggestions/{suggestion_id}/dismiss",
    response={200: DocumentOut, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS},
    operation_id="dismissSuggestion",
    description=(
        "Say no to a proposal; the document does not change and nothing automatic "
        "proposes or makes this change on it again."
    ),
)
def dismiss_suggestion(
    request: HttpRequest, workspace_id: UUID, document_id: UUID, suggestion_id: UUID
) -> DocumentOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    document = services.dismiss_suggestion(
        workspace=membership.workspace, document_id=document_id, suggestion_id=suggestion_id
    )
    return services.document_out_for(document)


@router.post(
    "/workspaces/{workspace_id}/documents/{document_id}/new-person-suggestions/{suggestion_id}/accept",
    response={
        200: DocumentOut,
        404: ErrorOut,
        409: ErrorOut,
        422: ErrorOut,
        **SESSION_WRITE_ERRORS,
    },
    operation_id="acceptNewPersonSuggestion",
    description=(
        "Members (not read-only). Make the person the AI proposed (as createPerson does, with "
        "its refusals) and put the letter on them; when a person with that name exists by "
        "now, that person. Accepting again changes nothing. 404 suggestion_not_found for a "
        "dismissed or unknown proposal."
    ),
)
def accept_new_person_suggestion(
    request: HttpRequest, workspace_id: UUID, document_id: UUID, suggestion_id: UUID
) -> DocumentOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    document = services.accept_new_person_suggestion(
        membership=membership,
        actor=actor_from_request(request),
        document_id=document_id,
        suggestion_id=suggestion_id,
    )
    return services.document_out_for(document)


@router.post(
    "/workspaces/{workspace_id}/documents/{document_id}/new-person-suggestions/{suggestion_id}/dismiss",
    response={200: DocumentOut, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS},
    operation_id="dismissNewPersonSuggestion",
    description=(
        "Members (not read-only). Say no to a proposed new person: nobody is made and the "
        "name is never proposed on this letter again."
    ),
)
def dismiss_new_person_suggestion(
    request: HttpRequest, workspace_id: UUID, document_id: UUID, suggestion_id: UUID
) -> DocumentOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    document = services.dismiss_new_person_suggestion(
        workspace=membership.workspace, document_id=document_id, suggestion_id=suggestion_id
    )
    return services.document_out_for(document)


@router.put(
    "/workspaces/{workspace_id}/documents/{document_id}/visibility",
    response={
        200: DocumentVisibilityOut,
        403: ErrorOut,
        404: ErrorOut,
        409: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    operation_id="setDocumentVisibility",
    description=(
        "Make the letter private (`restricted`) or visible to everyone (`workspace`), and"
        " share it: afterwards it is shared with exactly `shared_user_ids` (any letter)."
        " 403 `cannot_make_public` for someone not addressed while an addressee reads it;"
        " 404 `member_not_found` for a share with somebody who is no member. `can_read`"
        " false when the caller removed their own share."
    ),
)
def set_document_visibility(
    request: HttpRequest, workspace_id: UUID, document_id: UUID, payload: DocumentVisibilityIn
) -> DocumentVisibilityOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    return services.set_document_visibility(
        workspace=membership.workspace,
        user=current_user(request),
        actor=actor_from_request(request),
        document_id=document_id,
        visibility_value=payload.visibility,
        shared_user_ids=payload.shared_user_ids,
    )


@router.get(
    "/workspaces/{workspace_id}/documents/{document_id}/events",
    response={200: list[DocumentEventOut], 404: ErrorOut, **AUTH_ERRORS},
    operation_id="listDocumentEvents",
)
def list_document_events(
    request: HttpRequest, workspace_id: UUID, document_id: UUID
) -> list[DocumentEvent]:
    membership = workspace_member(request, workspace_id)
    return services.list_document_events(membership=membership, document_id=document_id)


@router.post(
    "/workspaces/{workspace_id}/documents/{document_id}/events/{event_id}/undo",
    response={200: DocumentOut, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS},
    operation_id="undoDocumentEvent",
    description=(
        "Reverse a label or addressee a rule or the autopilot added (`can_undo`; origin "
        "`rule`, `ai` or `name_match`); it is not made again on this document. 409 "
        "`event_not_undoable` when the change is not one of those, was undone already or is "
        "gone anyway."
    ),
)
def undo_document_event(
    request: HttpRequest, workspace_id: UUID, document_id: UUID, event_id: UUID
) -> DocumentOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    document = services.undo_document_event(
        workspace=membership.workspace,
        user=current_user(request),
        document_id=document_id,
        event_id=event_id,
    )
    return services.document_out_for(document)


@router.post(
    "/workspaces/{workspace_id}/documents/{document_id}/trash",
    response={200: TrashedDocumentOut, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS},
    operation_id="trashDocument",
    description=(
        "Move the letter to the trash (role member or admin; a viewer gets 403). It is gone"
        " from every list, count, search and task list, and can be put back with"
        " `restoreDocument` until `purge_after` (30 days); then it is purged for good."
        " Trashing a letter in the trash again answers the same. 404 `document_not_found`"
        " for a letter the caller does not see. Written to the log (`document_trashed`). A"
        ' document with a "Bewaren tot" today or later is moved too, with `warning` (ask'
        " first, from `retain_until`); it cannot be purged before that day."
    ),
)
def trash_document(
    request: HttpRequest, workspace_id: UUID, document_id: UUID
) -> TrashedDocumentOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    document = trash.trash_document(
        membership=membership,
        user=current_user(request),
        actor=actor_from_request(request),
        document_id=document_id,
    )
    return trash.trashed_out(document).model_copy(update={"warning": trash.trash_warning(document)})


@router.post(
    "/workspaces/{workspace_id}/documents/{document_id}/restore",
    response={200: DocumentOut, 404: ErrorOut, **SESSION_WRITE_ERRORS},
    operation_id="restoreDocument",
    description=(
        "Put a letter back from the trash, as it was (role member or admin). A letter that"
        " is not in the trash answers unchanged. 404 `document_not_found` once its purge"
        " started, or for a letter the caller does not see. Written to the log"
        " (`document_restored`)."
    ),
)
def restore_document(request: HttpRequest, workspace_id: UUID, document_id: UUID) -> DocumentOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    document = trash.restore_document(
        membership=membership,
        user=current_user(request),
        actor=actor_from_request(request),
        document_id=document_id,
    )
    return services.document_out(document)


@router.get(
    "/workspaces/{workspace_id}/trash",
    response={200: TrashListOut, 404: ErrorOut, **AUTH_ERRORS},
    operation_id="listTrash",
    description=(
        "The letters in the trash the caller sees, the most recently trashed first (at most"
        " 500), with when and by whom, and `purge_after`; `total` counts them all."
    ),
)
def list_trash(request: HttpRequest, workspace_id: UUID) -> TrashListOut:
    membership = workspace_member(request, workspace_id)
    return TrashListOut(
        items=[trash.trashed_out(document) for document in trash.list_trash(membership)],
        retention_days=trash.TRASH_RETENTION.days,
        total=trash.count_trash(membership),
    )


# Before `trash/{document_id}`: the router matches paths in order.
@router.post(
    "/workspaces/{workspace_id}/trash/empty",
    response={200: EmptyTrashOut, 404: ErrorOut, **SESSION_WRITE_ERRORS, **BODY_ERRORS},
    auth=session_auth,
    operation_id="emptyTrash",
    description=WEB_SESSION_ONLY
    + " Purge every letter in the caller's trash for good (role admin), up to `up_to`: the"
    " newest `trashed_at` the caller showed (all when absent). Letters the caller does not"
    " see stay. Answers how many letters are now being purged; a job removes them. Written"
    ' to the log, one `document_purged` row per letter. A letter with a "Bewaren tot" today'
    " or later stays in the trash; `retained` counts them.",
)
def empty_trash(request: HttpRequest, workspace_id: UUID, payload: EmptyTrashIn) -> EmptyTrashOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    emptied = trash.empty_trash(
        membership=membership, actor=actor_from_request(request), up_to=payload.up_to
    )
    return EmptyTrashOut(document_count=emptied.marked, retained=emptied.retained)


@router.delete(
    "/workspaces/{workspace_id}/trash/{document_id}",
    response={204: None, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS},
    auth=session_auth,
    operation_id="purgeDocument",
    description=WEB_SESSION_ONLY
    + " Purge a letter from the trash now, for good (role admin). Only a letter in the"
    " caller's trash: a live letter, one whose purge started, or one the caller does not"
    " see answers 404 `document_not_found`. From this answer on it cannot be restored; a"
    " job removes its scanned pages and its rows. A back-up made earlier still holds it."
    ' Written to the log (`document_purged`). 409 `document_retained` while its "Bewaren'
    ' tot" is today or later (remove the date first).',
)
def purge_document(request: HttpRequest, workspace_id: UUID, document_id: UUID) -> Status[None]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    trash.purge_now(
        membership=membership, actor=actor_from_request(request), document_id=document_id
    )
    return Status(204, None)


def _my_export_out(membership: Membership, export: MemberExport | None) -> MyExportOut:
    allowed = membership.workspace.allow_member_export
    if export is None:
        return MyExportOut(allowed=allowed, status="none")
    return MyExportOut(
        allowed=allowed,
        status=export.status,
        id=export.id,
        created_at=export.created_at,
        expires_at=export.expires_at,
        document_count=export.document_count,
        size=export.size,
    )


@router.post(
    _MY_EXPORT,
    response={
        202: MyExportOut,
        404: ErrorOut,
        409: ErrorOut,
        429: ErrorOut,
        **SESSION_WRITE_ERRORS,
    },
    auth=session_auth,
    throttle=CallerThrottle("member_export", "5/h"),
    operation_id="createMyExport",
    description=WEB_SESSION_ONLY
    + " Any member asks for an export of their own post: the letters on their name, as a"
    " ZIP of the page images and their data. A job builds it; poll `getMyExport`. The file"
    " can be downloaded for 24 hours, by the member who asked for it only. A newer request"
    " replaces an older export. 403 `export_not_allowed` when an admin switched exports off"
    " in this workspace, 409 `export_running` while the last one is being built, 429 after"
    " five requests in an hour. Written to the log (`export_created`).",
)
def create_my_export(request: HttpRequest, workspace_id: UUID) -> Status[MyExportOut]:
    membership = workspace_member(request, workspace_id)
    export = member_export.create_my_export(
        membership=membership, actor=actor_from_request(request)
    )
    return Status(202, _my_export_out(membership, export))


@router.get(
    _MY_EXPORT,
    response={200: MyExportOut, 404: ErrorOut, **AUTH_ERRORS},
    auth=session_auth,
    operation_id="getMyExport",
    description=WEB_SESSION_ONLY
    + " The caller's own export: `none`, `pending`, `ready` or `failed`, and whether the"
    " workspace allows exporting. Never another member's.",
)
def get_my_export(request: HttpRequest, workspace_id: UUID) -> MyExportOut:
    membership = workspace_member(request, workspace_id)
    return _my_export_out(membership, member_export.my_export(membership))


@router.get(
    f"{_MY_EXPORT}/file",
    response={403: ErrorOut, 404: ErrorOut, 429: ErrorOut, **AUTH_ERRORS},
    auth=session_auth,
    throttle=CallerThrottle("member_export_file", "20/h"),
    operation_id="downloadMyExport",
    openapi_extra={
        "responses": {
            200: {
                "description": "The export as a ZIP file, streamed",
                "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
            }
        }
    },
    description=WEB_SESSION_ONLY
    + " The caller's own finished export as a download (`Cache-Control: private, no-store`,"
    " `Content-Disposition: attachment`). 404 `export_not_found` for none, an expired or"
    " unfinished one, and for anybody else's: an admin cannot download a member's export."
    " 403 `export_not_allowed` when exports were switched off since. Written to the log"
    " (`export_downloaded`).",
)
def download_my_export(request: HttpRequest, workspace_id: UUID) -> StreamingHttpResponse:
    membership = workspace_member(request, workspace_id)
    # Everything that needs the request happens in here; the stream needs nothing.
    chunks = member_export.open_my_export(membership=membership, actor=actor_from_request(request))
    response = StreamingHttpResponse(chunks, content_type="application/zip")
    name = f"brievie-mijn-post-{timezone.localdate().isoformat()}.zip"
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    response["Cache-Control"] = "private, no-store"
    return response
