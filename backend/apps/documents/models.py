from collections.abc import Iterable
from decimal import Decimal
from typing import ClassVar

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.base import ModelBase
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.common.models import WorkspaceModel


class Folder(WorkspaceModel):
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="children"
    )
    name = models.CharField(max_length=200)
    # Full path such as "/Auto/APK", derived from the parent on save.
    path = models.TextField(editable=False)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["workspace", "path"], name="documents_folder_workspace_path_unique"
            ),
            # The path is built from names, so a name may not be blank or contain "/".
            models.CheckConstraint(
                condition=models.Q(name__regex=r"\S") & ~models.Q(name__contains="/"),
                name="documents_folder_name_valid",
            ),
        ]

    def __str__(self) -> str:
        return self.path

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        self.name = self.name.strip()
        if not self.name:
            raise ValidationError(
                {"name": ValidationError(_("Een map heeft een naam nodig."), code="blank")}
            )
        if "/" in self.name:
            raise ValidationError(
                {"name": ValidationError(_("Een mapnaam mag geen / bevatten."), code="slash")}
            )
        if self.parent is not None and self.parent.workspace_id != self.workspace_id:
            # The FK check bypasses RLS, so a parent from another workspace would be accepted.
            raise ValueError("A folder's parent must be in the same workspace.")
        parent_path = self.parent.path if self.parent is not None else ""
        self.path = f"{parent_path}/{self.name}"
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )


class Document(WorkspaceModel):
    """A letter or invoice: one or more pages, the recognised text and what was confirmed."""

    class Status(models.TextChoices):
        PROCESSING = "processing", gettext_lazy("Wordt verwerkt")
        NEEDS_REVIEW = "needs_review", gettext_lazy("Te controleren")
        DONE = "done", gettext_lazy("Klaar")
        FAILED = "failed", gettext_lazy("Verwerken mislukt")

    class Source(models.TextChoices):
        SCAN = "scan", gettext_lazy("Scan")
        UPLOAD = "upload", gettext_lazy("Upload")
        MAIL = "mail", gettext_lazy("Mail")
        WATCH_FOLDER = "watch_folder", gettext_lazy("Watch-map")

    class Type(models.TextChoices):
        INVOICE = "invoice", gettext_lazy("Factuur")
        ASSESSMENT = "assessment", gettext_lazy("Aanslag")
        DECISION = "decision", gettext_lazy("Beschikking")
        BILL = "bill", gettext_lazy("Rekening")
        REMINDER = "reminder", gettext_lazy("Herinnering")
        DEMAND = "demand", gettext_lazy("Aanmaning")
        POLICY = "policy", gettext_lazy("Polis")
        ANNUAL_STATEMENT = "annual_statement", gettext_lazy("Jaaropgave")
        PAYSLIP = "payslip", gettext_lazy("Loonstrook")
        INSPECTION = "inspection", gettext_lazy("Keuring")
        CONTRACT = "contract", gettext_lazy("Contract")
        RECEIPT = "receipt", gettext_lazy("Bonnetje")
        INFORMATION = "information", gettext_lazy("Informatie")
        OTHER = "other", gettext_lazy("Overig")

    class Visibility(models.TextChoices):
        # Who sees it follows the workspace's switches (S 4.1).
        WORKSPACE = "workspace", gettext_lazy("Normaal")
        # Private: only its reader rows (S 4.8).
        RESTRICTED = "restricted", gettext_lazy("Privé")

    # Chosen by the uploading client: a repeated upload finds the same document.
    client_id = models.UUIDField()
    scanned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.SCAN)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROCESSING)
    # Why processing failed (snake_case code); empty otherwise.
    failure_code = models.CharField(max_length=50, blank=True, default="")
    page_count = models.PositiveIntegerField()
    # BSN-masked text of all pages, joined by a form feed.
    ocr_text = models.TextField(blank=True, default="")

    # Filled in when the user confirms the review.
    sender = models.CharField(max_length=200, blank=True, default="")
    document_type = models.CharField(max_length=30, choices=Type.choices, blank=True, default="")
    letter_date = models.DateField(null=True, blank=True)
    summary = models.TextField(blank=True, default="")
    summary_by_ai = models.BooleanField(default=False)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    # The standing confirmation is the AI's (autopilot), not a person's.
    confirmed_by_ai = models.BooleanField(default=False)
    # An automatic label or addressee link (the autopilot's, not taken over by a person)
    # is on the letter. Derived from the links, written only by `documents.organise`, so
    # the inbox counts need no lookup in the link tables per letter (DECISIONS 2026-11-20).
    has_automatic_link = models.BooleanField(default=False)
    # The sorted label and addressee ids as the user confirmed them in the review
    # ({"label_ids": [...], "addressee_ids": [...]}); null when confirmed without them.
    confirmed_organisation = models.JSONField(null=True, blank=True)
    # No `db_default` (PB decision 5): Django would insert with RETURNING, which reads the
    # new row through the SELECT policies.
    visibility = models.CharField(
        max_length=20, choices=Visibility.choices, default=Visibility.WORKSPACE
    )
    # The persons and departments the letter is on (S 4.9.2), derived from its
    # `DocumentAddressee` rows. Written only by `documents.visibility.sync_addressee_ids`.
    addressee_ids = ArrayField(models.UUIDField(), default=list, blank=True)

    # In the trash since then (null: not in the trash). Every read leaves it out: through
    # the seam (`visibility.visible_documents`) and with `LIVE` off it.
    trashed_at = models.DateTimeField(null=True, blank=True)
    # Who moved it to the trash; null when Brievie did it.
    trashed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # The purge was decided (expired, an admin, emptied): never restorable from then on,
    # the purge job finishes it. Only on a letter in the trash.
    purge_started_at = models.DateTimeField(null=True, blank=True)

    # Full-text search (Dutch): the sender weighs most, then the summary, then the text.
    # Generated by PostgreSQL from the stored (already BSN-masked) fields.
    search_vector = models.GeneratedField(
        expression=SearchVector("sender", weight="A", config="dutch")
        + SearchVector("summary", weight="B", config="dutch")
        + SearchVector("ocr_text", weight="C", config="dutch"),
        output_field=SearchVectorField(),
        db_persist=True,
    )

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["workspace", "client_id"], name="documents_document_client_id_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(purge_started_at__isnull=True)
                | models.Q(trashed_at__isnull=False),
                name="documents_document_purge_needs_trash",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["workspace", "-created_at"], name="documents_document_recent"),
            # The trash list and the expiry query.
            models.Index(
                fields=["workspace", "trashed_at"],
                condition=models.Q(trashed_at__isnull=False),
                name="documents_document_trash",
            ),
            GinIndex(fields=["addressee_ids"], name="documents_document_addressees"),
            # Post without a name ("Zonder naam"), newest first: the list and its count
            # (measured, DECISIONS 2026-11-20).
            models.Index(
                fields=["workspace", "-created_at"],
                condition=models.Q(status="done", addressee_ids=[]),
                name="documents_document_unassigned",
            ),
            GinIndex(fields=["search_vector"], name="documents_document_search"),
            # For the typo fallback of search (pg_trgm word similarity).
            GinIndex(
                fields=["sender"], opclasses=["gin_trgm_ops"], name="documents_document_sender_trgm"
            ),
            GinIndex(
                fields=["summary"],
                opclasses=["gin_trgm_ops"],
                name="documents_doc_summary_trgm",
            ),
        ]

    def __str__(self) -> str:
        return str(self.id)


# The one spelling of "not in the trash" for reads off the seam (greppable). Through a
# relation: `models.Q(document__trashed_at__isnull=True)`.
LIVE = models.Q(trashed_at__isnull=True)


class DocumentPage(WorkspaceModel):
    same_workspace_fields = ("document",)

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="pages")
    index = models.PositiveIntegerField()  # 1-based
    content_type = models.CharField(max_length=50, default="image/jpeg")
    size = models.PositiveIntegerField()
    # Hex SHA-256 of the unencrypted file, as sent by the client.
    sha256 = models.CharField(max_length=64)
    ocr_text = models.TextField(blank=True, default="")  # BSN-masked
    # Relative path in the document storage; the file is encrypted.
    storage_path = models.CharField(max_length=300)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["document", "index"], name="documents_documentpage_index_unique"
            ),
        ]
        ordering: ClassVar[list[str]] = ["index"]

    def __str__(self) -> str:
        return f"{self.document_id} page {self.index}"


# Below this confidence a deadline or amount needs the user's attention (spec section 10).
NEEDS_ATTENTION_BELOW = Decimal("0.85")


class Extraction(WorkspaceModel):
    """One value the pipeline proposes for a document, with its source and confidence."""

    same_workspace_fields = ("document",)

    class Field(models.TextChoices):
        SENDER = "sender"
        DOCUMENT_TYPE = "document_type"
        LETTER_DATE = "letter_date"
        SUMMARY = "summary"
        ACTION_TYPE = "action_type"
        ACTION_DESCRIPTION = "action_description"
        ACTION_DEADLINE = "action_deadline"
        INVOICE_NUMBER = "invoice_number"
        INVOICE_DUE_DATE = "invoice_due_date"
        INVOICE_AMOUNT_INCL = "invoice_amount_incl"

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="extractions")
    field = models.CharField(max_length=30, choices=Field.choices)
    # 0 for values about the document; 1..n ties the values of one proposed action together.
    group = models.PositiveIntegerField(default=0)
    # Dates as ISO dates, amounts as decimal strings.
    value = models.TextField()
    # The words in the letter the value was read from; empty for generated text.
    quote = models.TextField(blank=True, default="")
    confidence = models.DecimalField(max_digits=4, decimal_places=3)
    # The AI service's host name (at most 253 characters in DNS).
    provider = models.CharField(max_length=255, blank=True, default="")
    model = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(confidence__gte=0) & models.Q(confidence__lte=1),
                name="documents_extraction_confidence_range",
            ),
        ]
        ordering: ClassVar[list[str]] = ["group", "created_at", "id"]

    def __str__(self) -> str:
        return f"{self.document_id} {self.field}"

    @property
    def needs_attention(self) -> bool:
        return needs_attention(self.field, self.confidence)


_ATTENTION_FIELDS = {
    Extraction.Field.ACTION_DEADLINE,
    Extraction.Field.INVOICE_DUE_DATE,
    Extraction.Field.INVOICE_AMOUNT_INCL,
}


def needs_attention(field: str, confidence: Decimal) -> bool:
    """Only deadlines and amounts: a wrong one costs money or a missed term."""
    return field in _ATTENTION_FIELDS and confidence < NEEDS_ATTENTION_BELOW


class DocumentAddressee(WorkspaceModel):
    """A person a document is "op naam van"; a document has zero or more."""

    same_workspace_fields = ("document", "person")

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="addressee_links")
    # Deleting a person who is still on a document is refused (archive instead).
    person = models.ForeignKey(
        "people.Person", on_delete=models.PROTECT, related_name="document_links"
    )
    # Brievie put this link there itself (autopilot).
    automatic = models.BooleanField(default=False)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["document", "person"], name="documents_documentaddressee_unique"
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            # The autopilot's links, for the "automatic" filter and count (DECISIONS
            # 2026-11-20): a few letters' worth, not every link of the workspace.
            models.Index(
                fields=["document"],
                condition=models.Q(automatic=True),
                name="documents_addressee_automatic",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.document_id} {self.person_id}"


class DocumentLabel(WorkspaceModel):
    """A label on a document; a document has zero or more."""

    same_workspace_fields = ("document", "label")

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="label_links")
    # Deleting a label takes it off its documents.
    label = models.ForeignKey(
        "labels.Label", on_delete=models.CASCADE, related_name="document_links"
    )
    # Brievie put this link there itself (autopilot).
    automatic = models.BooleanField(default=False)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["document", "label"], name="documents_documentlabel_unique"
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            # The autopilot's links, for the "automatic" filter and count (DECISIONS
            # 2026-11-20): a few letters' worth, not every link of the workspace.
            models.Index(
                fields=["document"],
                condition=models.Q(automatic=True),
                name="documents_label_automatic",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.document_id} {self.label_id}"


class DocumentEvent(WorkspaceModel):
    """One change in a document's history. Names are copied so they outlive a delete."""

    same_workspace_fields = ("document", "person", "label", "rule", "undoes", "related_document")

    class Kind(models.TextChoices):
        LABEL_ADDED = "label_added"
        LABEL_REMOVED = "label_removed"
        ADDRESSEE_ADDED = "addressee_added"
        ADDRESSEE_REMOVED = "addressee_removed"
        CONFIRMED = "confirmed"
        TASK_COMPLETED = "task_completed"
        TASK_REOPENED = "task_reopened"
        # The AI's standing confirmation was taken back because the letter changed.
        REOPENED = "reopened"
        MADE_PRIVATE = "made_private"
        MADE_PUBLIC = "made_public"
        SHARE_ADDED = "share_added"
        SHARE_REMOVED = "share_removed"
        ACCESS_GRANTED = "access_granted"
        EMERGENCY_OPENED = "emergency_opened"
        # An automatic action was not applied because the letter is private.
        PRIVATE_SKIPPED = "private_skipped"
        # Moved to the trash, and put back from it (never undoable through an undo).
        TRASHED = "trashed"
        RESTORED = "restored"
        # Duplicate letters: united into this letter (on the kept one), "two letters"
        # (on both), and a task the merge moved (on both).
        MERGED = "merged"
        DUPLICATE_DISMISSED = "duplicate_dismissed"
        TASK_MOVED = "task_moved"

    class Origin(models.TextChoices):
        USER = "user"
        RULE = "rule"
        # The user accepted a proposal.
        SUGGESTION = "suggestion"
        UNDO = "undo"
        # The autopilot did it: an AI proposal applied, or a name match applied.
        AI = "ai"
        NAME_MATCH = "name_match"
        # The autopilot applied a rule's label proposal (`rule` names the rule).
        RULE_AUTOPILOT = "rule_autopilot"
        # A person merged two scans of one letter (never undoable through an undo).
        MERGE = "merge"

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=30, choices=Kind.choices)
    origin = models.CharField(max_length=20, choices=Origin.choices, default=Origin.USER)
    # Null for a rule that ran in a job.
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    person = models.ForeignKey(
        "people.Person", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    person_name = models.TextField(blank=True, default="")
    label = models.ForeignKey(
        "labels.Label", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    label_name = models.TextField(blank=True, default="")
    rule = models.ForeignKey(
        "rules.Rule", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    rule_name = models.TextField(blank=True, default="")
    # The event this one reverses.
    undoes = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="undone_by"
    )
    # A short reason given with the event (emergency access), BSN-masked.
    note = models.TextField(blank=True, default="")
    # The other letter of a duplicate pair (merge, dismiss, a moved task); null once
    # that letter is purged.
    related_document = models.ForeignKey(
        Document, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.document_id} {self.kind}"


class DocumentShare(WorkspaceModel):
    """ "Delen met": one member may see one letter, whatever the switches (R44)."""

    same_workspace_fields = ("document",)

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="shares")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["document", "user"], name="documents_documentshare_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.document_id} shared with {self.user_id}"


class DocumentReader(WorkspaceModel):
    """Who reads a letter beyond the switches (S 4.9.4): the readers of a private letter
    (its addressees, its departments' members, its shares), a share of any letter, and
    emergency access until it expires. Written only by `documents.visibility`, as the
    system; a user sees only their own rows."""

    same_workspace_fields = ("document",)

    class Reason(models.TextChoices):
        ADDRESSEE = "addressee"
        DEPARTMENT = "department"
        SHARE = "share"
        EMERGENCY = "emergency"

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="readers")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    reason = models.CharField(max_length=20, choices=Reason.choices)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["document", "user", "reason"], name="documents_documentreader_unique"
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["user", "document"], name="documents_reader_user_document"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} reads {self.document_id} ({self.reason})"


class DocumentSuggestion(WorkspaceModel):
    """A proposed change to a document (a label or an addressee), one per change.

    A `dismissed` row is the document's memory of "no": nothing automatic proposes or
    applies that change on this document again.
    """

    same_workspace_fields = ("document", "label", "person", "rule")

    class Action(models.TextChoices):
        ADD_LABEL = "add_label"
        ADD_ADDRESSEE = "add_addressee"

    class Origin(models.TextChoices):
        # In order of strength: a rule's proposal outweighs a name match, which
        # outweighs the AI's.
        RULE = "rule"
        NAME_MATCH = "name_match"
        AI = "ai"
        # A row nothing proposed: only the user's "no" (removed by hand).
        NONE = ""

    class State(models.TextChoices):
        OPEN = "open"
        ACCEPTED = "accepted"
        DISMISSED = "dismissed"

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="suggestions")
    action = models.CharField(max_length=20, choices=Action.choices)
    label = models.ForeignKey(
        "labels.Label", null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    person = models.ForeignKey(
        "people.Person", null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    origin = models.CharField(max_length=20, choices=Origin.choices, blank=True, default="")
    rule = models.ForeignKey(
        "rules.Rule", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    rule_name = models.TextField(blank=True, default="")
    # The words in the letter that led to it; may be empty. BSN-masked.
    quote = models.TextField(blank=True, default="")
    confidence = models.DecimalField(max_digits=4, decimal_places=3, default=Decimal("0"))
    # True once a name match of 0.85 or higher contributed to this row.
    preselected = models.BooleanField(default=False)
    # The AI's own confidence for this change (0: the AI did not propose it), kept when a
    # stronger origin takes the row over: the autopilot applies a rule's label the AI
    # agrees with strongly enough.
    ai_confidence = models.DecimalField(max_digits=4, decimal_places=3, default=Decimal("0"))
    state = models.CharField(max_length=20, choices=State.choices, default=State.OPEN)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["document", "action", "label", "person"],
                nulls_distinct=False,
                name="documents_documentsuggestion_unique",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(action="add_label", label__isnull=False, person__isnull=True)
                    | models.Q(action="add_addressee", label__isnull=True, person__isnull=False)
                ),
                name="documents_documentsuggestion_one_target",
            ),
            models.CheckConstraint(
                condition=models.Q(confidence__gte=0) & models.Q(confidence__lte=1),
                name="documents_documentsuggestion_confidence_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.document_id} {self.action} {self.state}"


class DuplicateCandidate(WorkspaceModel):
    """Two letters that are probably one letter scanned twice: one row per pair, ever.

    Stored ordered (`first_id < second_id`). Readable and writable only for an actor who
    reads both letters (`rls.restrict_to_visible_document_pair`); member reads go through
    `apps.documents.duplicates.visible_pairs`. The purge of either letter removes the row.
    A `dismissed` row is the memory of "two letters": the pair is never proposed again.
    """

    same_workspace_fields = ("first", "second", "kept")

    class State(models.TextChoices):
        OPEN = "open"
        MERGED = "merged"
        DISMISSED = "dismissed"
        # A re-check after a person's confirm found a guard.
        WITHDRAWN = "withdrawn"

    first = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="+")
    second = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="+")
    state = models.CharField(max_length=20, choices=State.choices, default=State.OPEN)
    # The scores at detection.
    text_similarity = models.DecimalField(max_digits=4, decimal_places=3)
    number_overlap = models.DecimalField(max_digits=4, decimal_places=3)
    kept = models.ForeignKey(
        Document, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(first__lt=models.F("second")),
                name="documents_duplicate_ordered",
            ),
            models.UniqueConstraint(fields=["first", "second"], name="documents_duplicate_pair"),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["workspace", "first", "second"],
                condition=models.Q(state="open"),
                name="documents_duplicate_open",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.first_id} ~ {self.second_id} ({self.state})"


class MemberExport(WorkspaceModel):
    """A member's own export of their post: a sealed ZIP that lives for 24 hours.

    One row per member and workspace: a new request replaces the older export. The file
    is `storage_path` under the storage root, in the `exports/` directory that the sweep
    of page files does not look at.
    """

    class Status(models.TextChoices):
        PENDING = "pending"
        READY = "ready"
        FAILED = "failed"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    # `exports/<workspace id>/<export id>.sealed`, set when the export is asked for, so a
    # cleanup knows the file of a job that died.
    storage_path = models.CharField(max_length=300)
    document_count = models.PositiveIntegerField(default=0)
    size = models.BigIntegerField(default=0)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["workspace", "user"], name="documents_memberexport_user_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} export in {self.workspace_id}: {self.status}"
