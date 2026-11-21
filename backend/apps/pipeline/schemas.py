from datetime import datetime
from typing import TYPE_CHECKING

from ninja import Schema
from pydantic import Field

from apps.common.schemas import InputSchema

if TYPE_CHECKING:
    from apps.pipeline.settings_service import AiStatus, Autopilot


class AiStatusOut(Schema):
    # `off`, `external` or `local`.
    mode: str
    # For `external` with consent in force: `mistral`, `openai`, `claude` or `custom`.
    supplier: str | None = None
    # Host of the external service the text of letters goes to; never for `local`.
    host: str | None = None
    # When somebody agreed to this external service, or when `local` was set. Empty for
    # `external` means no consent is recorded: no letter is read until there is one.
    since: datetime | None = None
    # Whether a key for the service is stored. The key itself is never given.
    has_api_key: bool = False
    # The model in use, for an administrator only, so a form can keep it.
    model: str | None = None
    # The AI autopilot as stored (see `AiAutopilotOut`); `autopilot_allowed` is the server switch.
    auto_organise: bool = False
    auto_confirm: bool = False
    autopilot_allowed: bool = True

    @classmethod
    def from_status(
        cls,
        status: "AiStatus",
        *,
        with_model: bool,
        autopilot: "Autopilot | None" = None,
    ) -> "AiStatusOut":
        """What every member sees; `with_model` only for an administrator's form.

        Without `autopilot` the autopilot fields keep their defaults.
        """
        extra = (
            {}
            if autopilot is None
            else {
                "auto_organise": autopilot.organise,
                "auto_confirm": autopilot.confirm,
                "autopilot_allowed": autopilot.allowed,
            }
        )
        return cls(
            mode=status.mode,
            supplier=status.supplier,
            host=status.host,
            since=status.since,
            has_api_key=status.has_api_key,
            model=status.model if with_model else None,
            **extra,
        )


class AiSupplierOut(Schema):
    key: str
    name: str
    # Where the service's company is established, as a Dutch phrase.
    country: str
    host: str
    # "" = no default: a model name must be given.
    default_model: str


class AiSettingsPreviewIn(InputSchema):
    mode: str
    supplier: str = "mistral"
    # "" = the supplier's own address and default model.
    base_url: str = Field(default="", max_length=300)
    model: str = Field(default="", max_length=100)


class AiSettingsIn(AiSettingsPreviewIn):
    # Write-only. Left out: the stored key stays, at the same address only.
    api_key: str | None = Field(default=None, max_length=500)
    # The `address` of the preview whose notice the administrator agreed to.
    consent_address: str | None = Field(default=None, max_length=300)


class AiSettingsPreviewOut(Schema):
    needs_consent: bool
    # The text to show and agree to; "" when no consent is needed.
    notice: str
    # The external address the consent is about; send it back as `consent_address`.
    address: str


class AiAutopilotOut(Schema):
    # The stored switches: put letters under persons and labels itself, and confirm them itself.
    auto_organise: bool
    auto_confirm: bool
    # The server switch: false means the autopilot cannot be switched on here.
    allowed: bool
    # The text to agree to before a switch goes on.
    notice: str
    # A switch is on, but was agreed to under an older `notice`: show it to agree again
    # (`updateAiAutopilot` with the same switches and `acknowledged`). Until then the
    # autopilot does only what the older notice said.
    notice_outdated: bool = False


class AiAutopilotIn(InputSchema):
    auto_organise: bool
    auto_confirm: bool
    # Whether the administrator read `notice`; needed when a switch goes from off to on.
    acknowledged: bool = False
