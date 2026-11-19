from datetime import datetime
from typing import TYPE_CHECKING

from ninja import Schema
from pydantic import Field

from apps.common.schemas import InputSchema

if TYPE_CHECKING:
    from apps.pipeline.settings_service import AiStatus


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

    @classmethod
    def from_status(cls, status: "AiStatus", *, with_model: bool) -> "AiStatusOut":
        """What every member sees; `with_model` only for an administrator's form."""
        return cls(
            mode=status.mode,
            supplier=status.supplier,
            host=status.host,
            since=status.since,
            has_api_key=status.has_api_key,
            model=status.model if with_model else None,
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
