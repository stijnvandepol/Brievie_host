from typing import ClassVar

from django.db import models
from django.db.models.functions import Lower

from apps.common.models import WorkspaceModel

# The fixed palette; the web has one CSS class per colour, the app maps each to its own.
COLORS = ("grey", "blue", "teal", "green", "yellow", "orange", "red", "purple")


class Label(WorkspaceModel):
    """A label the user puts on documents; one from a preset has a `system_key`."""

    # Trimmed and BSN-masked (see labels.services).
    name = models.CharField(max_length=50)
    color = models.CharField(max_length=10, choices=[(c, c) for c in COLORS], default="grey")
    # The preset label's stable key, so later features find it whatever its name; empty
    # for an own label.
    system_key = models.CharField(max_length=50, blank=True, default="")

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                models.F("workspace"), Lower("name"), name="labels_label_name_unique"
            ),
            models.UniqueConstraint(
                fields=["workspace", "system_key"],
                condition=~models.Q(system_key=""),
                name="labels_label_system_key_unique",
            ),
        ]

    def __str__(self) -> str:
        return str(self.id)
