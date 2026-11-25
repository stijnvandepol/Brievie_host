from decimal import Decimal
from typing import ClassVar

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import WorkspaceModel


class Task(WorkspaceModel):
    """Something that has to be done, usually before a date."""

    same_workspace_fields = ("document",)

    class Type(models.TextChoices):
        PAY = "pay", _("Betalen")
        RESPOND = "respond", _("Reageren")
        OBJECT = "object", _("Bezwaar maken")
        APPLY = "apply", _("Aanvragen")
        INSPECTION = "inspection", _("Keuring")
        CANCEL = "cancel", _("Opzeggen")
        RENEW = "renew", _("Verlengen")
        FILE_TAX = "file_tax", _("Aangifte")
        OTHER = "other", _("Overig")
        # The reminder of an own document's end date ("Geldig tot"); made only by
        # `apps.documents.reminders`, never by a review (archive spec S 7.1, AR6).
        EXPIRY = "expiry", _("Verloopt")

    class DeadlineSource(models.TextChoices):
        IN_DOCUMENT = "in_document", _("Staat in het document")
        CALCULATED = "calculated", _("Berekend")
        MANUAL = "manual", _("Zelf ingevuld")

    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        DONE = "done", _("Afgerond")

    # The trash contract: a letter in the trash keeps its tasks (hidden from every list
    # with it); a purge deletes the letter's tasks as the system before the letter, so no
    # task of a private letter becomes letterless (a letterless task's description would
    # reach whoever sees all post).
    document = models.ForeignKey(
        "documents.Document", null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks"
    )
    type = models.CharField(max_length=20, choices=Type.choices)
    description = models.CharField(max_length=500)
    # A deadline is a day, not a moment.
    deadline = models.DateField(null=True, blank=True)
    deadline_source = models.CharField(
        max_length=20, choices=DeadlineSource.choices, default=DeadlineSource.MANUAL
    )
    # The words in the document the deadline was read from; empty unless `in_document`.
    deadline_quote = models.TextField(blank=True, default="")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    # The autopilot made this task, not a person.
    created_by_ai = models.BooleanField(default=False)
    # The person who made it (a confirm); null for the autopilot and for older tasks. A
    # task without a letter is seen by its maker and its assignee (R39).
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    # What a payment costs, as confirmed (or quoted by the letter for the autopilot); null
    # when unknown. Never guessed for older tasks (MT14).
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    # For a task without a date: the letter's words that name a term ("binnen zes weken na
    # dagtekening"), BSN-masked, so the list can ask for a date ("Datum nodig"). Also a
    # database default '' (migration 0005) for raw inserts.
    term_quote = models.CharField(max_length=200, blank=True, default="")
    # Who ticked it off (null for older tasks and once reopened), and who gave it to its
    # assignee (null when nobody did, or the system released it).
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    # "Wacht op reactie tot": the other party has the next move until this day. Shared:
    # everyone who sees the letter sees it. Cleared when the task is completed. Written
    # only by `apps.tasks.waiting` and the completion.
    waiting_until = models.DateField(null=True, blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["deadline", "created_at", "id"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(amount__gte=0), name="tasks_task_amount_not_negative"
            ),
            # At most one open end-date reminder per document: a second run (or a race)
            # fails harmlessly. Letters' tasks are untouched.
            models.UniqueConstraint(
                fields=["document"],
                condition=models.Q(type="expiry", status="open"),
                name="tasks_task_one_reminder",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            # "Mijn taken" and its counts read open tasks by date (spec S 11).
            models.Index(
                fields=["workspace", "deadline"],
                condition=models.Q(status="open"),
                name="tasks_task_open",
            ),
            # The task list in its own order (`list_tasks`): read in order, the policy runs
            # only until the page is full, not for every task before a sort.
            models.Index(
                fields=["workspace", "deadline", "created_at", "id"],
                name="tasks_task_list_order",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.type}: {self.description}"


class TaskSnooze(WorkspaceModel):
    """ "Uitgesteld tot": one member put a task off in their own list until a day. Personal:
    a row is visible and writable only to its own user, and only while that user sees the
    task (forced RLS); it changes nothing anybody else sees and is in no history."""

    same_workspace_fields = ("task",)

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="snoozes")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    until = models.DateField()

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["task", "user"], name="tasks_tasksnooze_unique"),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["user", "task"], name="tasks_tasksnooze_user"),
        ]

    def __str__(self) -> str:
        return f"{self.task_id} {self.until}"


def amount_text(amount: Decimal | None) -> str:
    """An amount as the API sends it: a decimal string with two places, empty when unknown
    (a nullable decimal would render as `anyOf` in the contract, MT8)."""
    return "" if amount is None else f"{amount:.2f}"
