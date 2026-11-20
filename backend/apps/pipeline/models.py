from typing import ClassVar

from django.conf import settings
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
    # The external address somebody agreed to send letters to; "" = no consent in force.
    # Text only goes out while this equals `base_url`. The history is `AIConsentEvent`.
    consented_base_url = models.CharField(max_length=300, blank=True, default="")
    # The AI autopilot, both opt-in: put a letter under persons and labels itself, and
    # confirm it itself. Only in force while the mode is not `off` and the server allows it.
    auto_organise = models.BooleanField(default=False)
    auto_confirm = models.BooleanField(default=False)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["workspace"], name="pipeline_aisettings_unique"),
        ]

    def __str__(self) -> str:
        return f"AI {self.mode} for {self.workspace_id}"


class AIConsentEvent(WorkspaceModel):
    """One decision about a workspace's AI: who switched it on, changed it or switched it off.

    The history: the application only ever adds rows. The state in force is
    `AISettings.consented_base_url`, so it does not depend on the server's clock.
    `created_at` is the moment of the decision. Never the API key, the full address or
    document text.
    """

    class Event(models.TextChoices):
        # Somebody read what leaves the server and agreed to this external service.
        GRANTED = "granted"
        # A change that needs no new question: another model or key at the same address,
        # or a service on the own network.
        CHANGED = "changed"
        SWITCHED_OFF = "switched_off"

    event = models.CharField(max_length=20, choices=Event.choices)
    mode = models.CharField(max_length=10, choices=AISettings.Mode.choices)
    # A key of `settings_service.SUPPLIERS`, `custom` for another address, "" when not external.
    supplier = models.CharField(max_length=20, blank=True, default="")
    # Host of the service's address only: no path, query or port.
    host = models.CharField(max_length=253, blank=True, default="")
    model = models.CharField(max_length=100, blank=True, default="")
    # Null for the command line.
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor_label = models.CharField(max_length=50)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["workspace", "-created_at"], name="pipeline_aiconsent_latest"),
        ]

    def __str__(self) -> str:
        return f"AI {self.event} ({self.mode}) for {self.workspace_id}"
