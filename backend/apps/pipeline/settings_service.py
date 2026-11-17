"""How a workspace has its documents read: its AI settings and the provider they give."""

import ipaddress
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID

from django.db import transaction
from django.utils.functional import Promise
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from apps.common import crypto
from apps.pipeline.errors import PermanentError
from apps.pipeline.models import AIConsentEvent, AISettings
from apps.pipeline.providers import AnthropicProvider, LLMProvider, OpenAICompatibleProvider
from apps.workspaces.keys import workspace_key

DEFAULT_SUPPLIER = "mistral"
DEFAULT_EXTERNAL_URL = "https://api.mistral.ai/v1"
DEFAULT_EXTERNAL_MODEL = "mistral-small-latest"


CUSTOM_SUPPLIER = "custom"
COMMAND_LINE = "command line"


@dataclass(frozen=True)
class SupplierPreset:
    api: str
    base_url: str
    model: str  # "" = no default: the caller must name a model
    name: str
    # Where the service's company is established, as told to whoever gives consent.
    country: Promise


SUPPLIERS: dict[str, SupplierPreset] = {
    "mistral": SupplierPreset(
        AISettings.Api.OPENAI,
        DEFAULT_EXTERNAL_URL,
        DEFAULT_EXTERNAL_MODEL,
        "Mistral",
        _("Frankrijk (Europese Unie)"),
    ),
    "openai": SupplierPreset(
        AISettings.Api.OPENAI,
        "https://api.openai.com/v1",
        "",
        "OpenAI",
        _("Verenigde Staten"),
    ),
    "claude": SupplierPreset(
        AISettings.Api.ANTHROPIC,
        "https://api.anthropic.com",
        "claude-opus-5-5",
        "Claude (Anthropic)",
        _("Verenigde Staten"),
    ),
}


class AiSettingsError(ValueError):
    """Settings that cannot be saved; `reason` says why in a word, for a Dutch answer."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class ConsentRequiredError(ValueError):
    """Switching to this external service needs somebody's explicit consent first."""


@dataclass(frozen=True)
class Actor:
    """Who decides about a workspace's AI: a user, or whoever runs the command."""

    user_id: UUID | None = None
    label: str = COMMAND_LINE


@dataclass(frozen=True)
class Target:
    """Checked settings, before anything is stored."""

    mode: str
    api: str
    # A key of `SUPPLIERS`, `custom` for another address, "" when not external.
    supplier: str
    base_url: str
    model: str
    host: str


@dataclass(frozen=True)
class AiStatus:
    """What a member may know about the workspace's AI."""

    mode: str
    supplier: str | None = None
    host: str | None = None
    since: datetime | None = None
    has_api_key: bool = False
    model: str | None = None


@dataclass(frozen=True)
class Preview:
    """What saving these settings would ask of whoever saves them."""

    needs_consent: bool
    # The text to agree to; "" when no consent is needed.
    notice: str
    # The external address the consent is about; "" when not external.
    address: str


_KEY_AAD = b"ai-api-key"
# A DNS name is at most 253 characters, each label at most 63.
_MAX_HOST = 253
_HOST_LABEL = re.compile(r"[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?", re.IGNORECASE)
# Integer and hex forms that `inet_aton` and most resolvers read as an IPv4 address:
# `134744072`, `0x7f000001`, `017700000001`.
_NUMERIC_HOST = re.compile(r"(0x[0-9a-f]*|[0-9]+)", re.IGNORECASE)


def resolve(*, mode: str, base_url: str, model: str, supplier: str = DEFAULT_SUPPLIER) -> Target:
    """Check the settings and fill in the supplier's preset. Raises `ValueError`.

    `external`: an https service; `supplier` picks the preset (Mistral by default,
    OpenAI, Claude), `base_url` and `model` override it. `local`: a service on the own
    network only, so no document text leaves it. `off`: nothing.
    """
    if mode not in AISettings.Mode.values:
        raise AiSettingsError("unknown_mode", f"Unknown mode: {mode!r}")
    preset = SUPPLIERS.get(supplier)
    if preset is None:
        raise AiSettingsError("unknown_supplier", f"Unknown supplier: {supplier!r}")
    if mode == AISettings.Mode.OFF:
        return Target(mode, AISettings.Api.OPENAI, "", "", "", "")
    api: str = AISettings.Api.OPENAI
    if mode == AISettings.Mode.EXTERNAL:
        api = preset.api
        base_url = base_url or preset.base_url
        model = model or preset.model
        if not model:
            raise AiSettingsError("model_required", f"Supplier {supplier} needs a model name.")
    if len(model) > (AISettings._meta.get_field("model").max_length or 0):
        raise AiSettingsError("model_invalid", "The model name is too long.")
    _check_address(base_url or DEFAULT_EXTERNAL_URL)
    host = _host(base_url)
    if mode == AISettings.Mode.EXTERNAL:
        if urlsplit(base_url).scheme != "https":
            raise AiSettingsError("not_https", "An external AI service needs an https address.")
        # A preset's name on somebody else's server would be a false statement.
        named = supplier if host == _host(preset.base_url) else CUSTOM_SUPPLIER
        return Target(mode, api, named, base_url, model, host)
    if not is_on_own_network(base_url):
        raise AiSettingsError("not_local", "A local AI service must be on the own network.")
    if not model:
        raise AiSettingsError("model_required", "A local AI service needs a model name.")
    return Target(mode, api, "", base_url, model, host)


def configure(
    *,
    workspace_id: UUID,
    mode: str,
    base_url: str,
    model: str,
    api_key: str | None,
    supplier: str = DEFAULT_SUPPLIER,
    actor: Actor | None = None,
    consent: bool = False,
    consent_address: str | None = None,
) -> AISettings:
    """Set the workspace's AI mode and record the decision. Needs the workspace's RLS context.

    See `resolve` for the settings. Switching to an external service whose address has no
    consent in force raises `ConsentRequiredError` unless `actor` was told what leaves
    the server and agreed: `consent` when the caller showed the notice for these very
    settings itself (the command), `consent_address` when the caller only knows which
    address the actor agreed to (the API); that counts for exactly that address.

    Without `api_key` the stored key stays, but only at the same address: a key never
    moves to another service.
    """
    target = resolve(mode=mode, base_url=base_url, model=model, supplier=supplier)
    consent = consent or (consent_address is not None and consent_address == target.base_url)
    actor = actor or Actor()
    with transaction.atomic():
        # Locked, so a decision taken at the same moment cannot slip in between the
        # check and the write.
        current = AISettings.objects.select_for_update().filter(workspace_id=workspace_id).first()
        event: str | None = AIConsentEvent.Event.CHANGED
        consented = ""
        if target.mode == AISettings.Mode.EXTERNAL:
            consented = target.base_url
            if current is None or current.consented_base_url != target.base_url:
                if not consent:
                    raise ConsentRequiredError("An external AI service needs explicit consent.")
                event = AIConsentEvent.Event.GRANTED
        elif target.mode == AISettings.Mode.OFF:
            was_on = current is not None and current.mode != AISettings.Mode.OFF
            event = AIConsentEvent.Event.SWITCHED_OFF if was_on else None
        sealed: bytes | None = None
        if api_key and target.mode != AISettings.Mode.OFF:
            sealed = crypto.seal(workspace_key(workspace_id), api_key.encode(), aad=_KEY_AAD)
        elif target.mode == AISettings.Mode.EXTERNAL:
            # Only a key that is already at this very address may stay.
            kept = None
            if api_key is None and current is not None and current.base_url == target.base_url:
                kept = current.api_key_sealed if current.mode == target.mode else None
            if kept is None:
                raise AiSettingsError("key_required", "An external AI service needs an API key.")
            sealed = bytes(kept)
        settings, _created = AISettings.objects.update_or_create(
            workspace_id=workspace_id,
            defaults={
                "mode": target.mode,
                "api": target.api,
                "base_url": target.base_url,
                "model": target.model,
                "api_key_sealed": sealed,
                "consented_base_url": consented,
            },
        )
        if event is not None:
            AIConsentEvent.objects.create(
                workspace_id=workspace_id,
                event=event,
                mode=target.mode,
                supplier=target.supplier,
                host=target.host,
                model=target.model,
                actor_id=actor.user_id,
                actor_label=actor.label,
            )
    return settings


def consent_in_force(workspace_id: UUID, base_url: str) -> bool:
    """Whether somebody agreed to send the workspace's letters to exactly `base_url`.

    A consent covers one address: another host, port or path asks again (behind one
    host a path can lead to another service). It ends when AI is switched off or moves
    to the own network. Needs the workspace's RLS context.
    """
    consented = AISettings.objects.filter(workspace_id=workspace_id).values_list(
        "consented_base_url", flat=True
    )
    return bool(base_url) and consented.first() == base_url


def status(workspace_id: UUID) -> AiStatus:
    """The mode and, for an external service, where the text goes and since when.

    `since` is empty for an external service without consent in force: then no letter
    is read. The address on the own network is nobody's business. Needs the RLS context.
    """
    settings = AISettings.objects.filter(workspace_id=workspace_id).first()
    if settings is None or settings.mode == AISettings.Mode.OFF:
        return AiStatus(mode=AISettings.Mode.OFF)
    events = AIConsentEvent.objects.filter(workspace_id=workspace_id).order_by("-created_at")
    has_key = settings.api_key_sealed is not None
    if settings.mode == AISettings.Mode.LOCAL:
        latest = events.first()
        since = latest.created_at if latest and latest.mode == AISettings.Mode.LOCAL else None
        return AiStatus(mode=settings.mode, since=since, has_api_key=has_key, model=settings.model)
    host = _host(settings.base_url)
    granted = None
    if settings.consented_base_url == settings.base_url:
        granted = events.filter(event=AIConsentEvent.Event.GRANTED, host=host).first()
    if granted is None:
        return AiStatus(
            mode=settings.mode, host=host or None, has_api_key=has_key, model=settings.model
        )
    return AiStatus(
        mode=settings.mode,
        supplier=granted.supplier,
        host=host,
        since=granted.created_at,
        has_api_key=has_key,
        model=settings.model,
    )


def preview(
    *,
    workspace_id: UUID,
    workspace_name: str,
    mode: str,
    base_url: str,
    model: str,
    supplier: str = DEFAULT_SUPPLIER,
) -> Preview:
    """Check the settings without saving them, and say what saving would ask.

    Raises `AiSettingsError`. Needs the workspace's RLS context.
    """
    target = resolve(mode=mode, base_url=base_url, model=model, supplier=supplier)
    if target.mode != AISettings.Mode.EXTERNAL:
        return Preview(needs_consent=False, notice="", address="")
    if consent_in_force(workspace_id, target.base_url):
        return Preview(needs_consent=False, notice="", address=target.base_url)
    return Preview(
        needs_consent=True,
        notice=external_notice(workspace_name, target),
        address=target.base_url,
    )


def external_notice(workspace_name: str, target: Target) -> str:
    """What whoever decides must know before letters go to an external AI service.

    The command and the web interface show this same text.
    """
    preset = SUPPLIERS.get(target.supplier)
    if preset is None or target.base_url != preset.base_url:
        # The consent is for this very address, so that is what is shown: behind one
        # host another port or path can lead to another service, also at a preset's host.
        parts = urlsplit(target.base_url)
        service = f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}{parts.path}"
        country = gettext("onbekend. Zoek zelf op in welk land deze dienst staat.")
    else:
        service = f"{preset.name} ({target.host})"
        country = str(preset.country)
    return gettext(
        "Je zet AI aan voor werkruimte %(workspace)s. Lees dit eerst.\n"
        "\n"
        "Dit gaat naar de AI-dienst, bij elke brief:\n"
        "- De tekst van elke brief. Een BSN is daarin vervangen door [BSN]. Andere "
        "gegevens staan er gewoon in, ook over geld, gezondheid en andere mensen.\n"
        "- De namen van de labels in deze werkruimte.\n"
        "- De namen van de personen in deze werkruimte, ook andere schrijfwijzen van "
        "hun naam.\n"
        "\n"
        "Dit gaat er nooit naartoe:\n"
        "- De foto's of scans van de pagina's.\n"
        "- Je regels en de interne nummers van brieven, labels en personen.\n"
        "\n"
        "Hier gaat het naartoe:\n"
        "- Dienst: %(service)s, model %(model)s\n"
        "- Land: %(country)s\n"
        "\n"
        "Let op:\n"
        "- Wat de dienst met de tekst doet en hoe lang de dienst die bewaart, staat in "
        "de voorwaarden van de dienst zelf. Brievie heeft daar geen invloed op.\n"
        "- Jij beslist dit voor iedereen van wie post in deze werkruimte staat. Vertel "
        "het hun.\n"
        "- Brievie legt vast dat je akkoord bent, en wanneer. Elk lid van de werkruimte "
        "kan zien dat AI aan staat.\n"
        "- Je kunt AI altijd weer uitzetten. Wat al verstuurd is, haal je daarmee niet "
        "terug.\n"
    ) % {
        "workspace": workspace_name,
        "service": service,
        "model": target.model,
        "country": country,
    }


def _host(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def ai_mode(workspace_id: UUID) -> str:
    """The workspace's AI mode; `off` when it has no settings. Needs its RLS context."""
    mode = AISettings.objects.filter(workspace_id=workspace_id).values_list("mode", flat=True)
    return str(mode.first() or AISettings.Mode.OFF)


def provider_for(workspace_id: UUID) -> LLMProvider | None:
    """The provider for the workspace, or `None` when its AI is off."""
    settings = AISettings.objects.filter(workspace_id=workspace_id).first()
    if settings is None or settings.mode == AISettings.Mode.OFF:
        return None
    if settings.mode == AISettings.Mode.LOCAL and not is_on_own_network(settings.base_url):
        # Checked again here: a row changed behind our back must not send text outside.
        raise PermanentError("ai_not_configured")
    if settings.mode == AISettings.Mode.EXTERNAL and (
        settings.consented_base_url != settings.base_url
    ):
        # No letter leaves the server for a service nobody agreed to: a workspace set
        # to external before consent was recorded, or a row changed behind our back.
        raise PermanentError("ai_not_configured")
    api_key = None
    if settings.api_key_sealed is not None:
        api_key = crypto.open_sealed(
            workspace_key(workspace_id), bytes(settings.api_key_sealed), aad=_KEY_AAD
        ).decode()
    if settings.api == AISettings.Api.ANTHROPIC:
        return AnthropicProvider(base_url=settings.base_url, api_key=api_key, model=settings.model)
    return OpenAICompatibleProvider(
        base_url=settings.base_url, api_key=api_key, model=settings.model
    )


def _check_address(url: str) -> None:
    """Refuse an address that would not fit its column or is not a host name at all.

    The host becomes the provider name on every proposal: a value that cannot be
    stored would fail every document later instead of here.
    """
    if len(url) > (AISettings._meta.get_field("base_url").max_length or 0):
        raise AiSettingsError("address_invalid", "The address of the AI service is too long.")
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        raise AiSettingsError(
            "address_invalid", "The address of the AI service is not valid."
        ) from None
    if host.startswith("[") or _is_ip_literal(host):
        return
    labels = host.rstrip(".").split(".")
    if len(host) > _MAX_HOST or not all(_HOST_LABEL.fullmatch(label) for label in labels):
        raise AiSettingsError("address_invalid", "The host name of the AI service is not valid.")


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def is_on_own_network(url: str) -> bool:
    """Whether `url` names a host that cannot be on the public internet.

    A Compose service name (no dot), `localhost`, a `.local` name, or a literal
    loopback or private address. Other names are refused: they could resolve
    anywhere.
    """
    parts = urlsplit(url)
    host = parts.hostname
    if parts.scheme not in ("http", "https") or not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Python only knows dotted IPv4, but the resolver httpx uses also reads
        # `http://134744072` (8.8.8.8) or `http://0x7f000001` as an address. Such a
        # host has no dot, so it would pass as a Compose service name and send the
        # letters to any public address. Numeric hosts are therefore never names.
        # (Other short forms such as `127.1` contain a dot and are refused below.)
        if _NUMERIC_HOST.fullmatch(host):
            return False
        return "." not in host or host == "localhost" or host.endswith(".local")
    if isinstance(address, ipaddress.IPv6Address):
        # Addresses that carry a public IPv4 address inside count as private to Python.
        if address.sixtofour is not None or address.teredo is not None:
            return False
        address = address.ipv4_mapped or address
    # Link-local is where a cloud keeps its metadata service, never an AI service.
    if address.is_link_local:
        return False
    return address.is_loopback or address.is_private
