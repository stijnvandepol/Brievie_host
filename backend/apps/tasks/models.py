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

    class Meta:
        ordering: ClassVar[list[str]] = ["deadline", "created_at", "id"]

    def __str__(self) -> str:
        return f"{self.type}: {self.description}"
