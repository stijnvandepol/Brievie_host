from typing import ClassVar

from django.db import models

from apps.common.models import WorkspaceModel


class Rule(WorkspaceModel):
    """A user's rule: when its conditions hold, it proposes or makes changes (design 9.1).

    `definition` is one versioned JSON document ({"version": 1, "match", "conditions",
    "actions"}); `apps.rules.definition` validates, writes and reads it.
    """

    class Mode(models.TextChoices):
        PROPOSE = "propose"
        AUTO = "auto"

    # Order in which rules run; unique per workspace (deferred, so a reorder can swap).
    position = models.PositiveIntegerField()
    # Trimmed and BSN-masked.
    name = models.CharField(max_length=100)
    active = models.BooleanField(default=True)
    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.PROPOSE)
    # A matching rule with `stop` ends the run after its own actions.
    stop = models.BooleanField(default=False)
    definition = models.JSONField()
    # The preset label's key for a preset rule; empty for an own rule.
    system_key = models.CharField(max_length=50, blank=True, default="")

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["workspace", "position"],
                name="rules_rule_position_unique",
                deferrable=models.Deferrable.DEFERRED,
            ),
            models.UniqueConstraint(
                fields=["workspace", "system_key"],
                condition=~models.Q(system_key=""),
                name="rules_rule_system_key_unique",
            ),
        ]
        ordering: ClassVar[list[str]] = ["position"]

    def __str__(self) -> str:
        return str(self.id)


class RuleRun(WorkspaceModel):
    """A rule applied or proposed at least one change to a document in one run."""

    same_workspace_fields = ("rule", "document")

    class Trigger(models.TextChoices):
        PROCESSED = "processed"
        CONFIRMED = "confirmed"
        MANUAL = "manual"

    class Outcome(models.TextChoices):
        APPLIED = "applied"
        PROPOSED = "proposed"

    rule = models.ForeignKey(Rule, on_delete=models.CASCADE, related_name="runs")
    document = models.ForeignKey(
        "documents.Document", on_delete=models.CASCADE, related_name="rule_runs"
    )
    trigger = models.CharField(max_length=20, choices=Trigger.choices)
    outcome = models.CharField(max_length=20, choices=Outcome.choices)

    def __str__(self) -> str:
        return f"{self.rule_id} {self.document_id} {self.outcome}"
