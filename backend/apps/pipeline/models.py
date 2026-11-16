from typing import ClassVar

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import WorkspaceModel


class AISettings(WorkspaceModel):
    """How a workspace has its documents read: not at all, or by an AI service."""

    class Mode(models.TextChoices):
        OFF = "off", _("Uit")
        EXTERNAL = "external", _("Externe dienst")
        LOCAL = "local", _("Op het eigen netwerk")

    class Api(models.TextChoices):
        OPENAI = "openai", _("OpenAI-compatibel")
        ANTHROPIC = "anthropic", _("Claude (Anthropic)")

    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.OFF)
    # The kind of API at `base_url`: OpenAI-compatible chat completions or Claude's Messages API.
    api = models.CharField(max_length=20, choices=Api.choices, default=Api.OPENAI)
    # Base URL of the service, e.g. https://api.mistral.ai/v1 or https://api.anthropic.com
    base_url = models.CharField(max_length=300, blank=True, default="")
    model = models.CharField(max_length=100, blank=True, default="")
    # The API key, sealed with the workspace key.
    api_key_sealed = models.BinaryField(null=True, blank=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["workspace"], name="pipeline_aisettings_unique"),
        ]

    def __str__(self) -> str:
        return f"AI {self.mode} for {self.workspace_id}"
