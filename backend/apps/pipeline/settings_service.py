"""How a workspace has its documents read: its AI settings and the provider they give."""

import ipaddress
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID

from django.conf import settings as django_settings
from django.db import transaction
from django.utils.functional import Promise
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User
from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, record
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


class AutopilotError(ValueError):
    """The autopilot switches cannot be saved; `reason` says why in a word.

    `forbidden`: the server does not allow it. `needs_ai`: the workspace's AI is off.
    `ack_required`: a switch goes on without the notice being acknowledged.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Autopilot:
    """The autopilot switches. `allowed` is the server switch. `notice_outdated`: a switch
    is on but was agreed to under an older notice. `rule_labels`: the autopilot may apply a
    rule's label proposal (the notice that says so was agreed to)."""

    organise: bool
    confirm: bool
    allowed: bool
    notice_outdated: bool = False
    rule_labels: bool = False


@dataclass(frozen=True)
class Actor:
    """Who decides about a workspace's AI: a user, or whoever runs the command."""

    user_id: UUID | None = None
    label: str = COMMAND_LINE
    client_ip: str | None = None


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


# The sealed API key is bound to its workspace and to the one address it may be sent to
# (security review 2026-11-20): whoever changes `base_url` in the table behind our back
# gets a key that no longer opens, instead of the key arriving at their server. Keys
# sealed before (`b"ai-api-key"`) no longer open either; the administrator types the key
# again.
_KEY_AAD_PREFIX = b"ai-api-key-v2|"


def _key_aad(workspace_id: UUID, base_url: str) -> bytes:
    return _KEY_AAD_PREFIX + str(workspace_id).encode() + b"|" + base_url.encode()


def _open_key(workspace_id: UUID, sealed: bytes, base_url: str) -> str | None:
    """The API key when it was sealed for this workspace and exactly this address."""
    try:
        opened = crypto.open_sealed(
            workspace_key(workspace_id), sealed, aad=_key_aad(workspace_id, base_url)
        )
    except crypto.DecryptionError:
        return None
    return opened.decode()


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
            sealed = crypto.seal(
                workspace_key(workspace_id),
                api_key.encode(),
                aad=_key_aad(workspace_id, target.base_url),
            )
        elif target.mode == AISettings.Mode.EXTERNAL:
            # Only a key that is already at this very address may stay.
            kept = None
            if api_key is None and current is not None and current.base_url == target.base_url:
                kept = current.api_key_sealed if current.mode == target.mode else None
            # A key sealed for another address (or before keys were bound) is no key.
            if kept is not None and _open_key(workspace_id, bytes(kept), target.base_url) is None:
                kept = None
            if kept is None:
                raise AiSettingsError("key_required", "An external AI service needs an API key.")
            sealed = bytes(kept)
        # Autopilot was agreed to for the AI as it was. Three things end it: AI switched
        # off, another mode (external <-> local: another model, often weaker), or consent
        # granted for another address. Another model in the same mode at the same address
        # does not. Both switches stay off until somebody agrees again.
        reset = current is not None and (
            target.mode == AISettings.Mode.OFF
            or target.mode != current.mode
            or event == AIConsentEvent.Event.GRANTED
        )
        was_organise = reset and current is not None and current.auto_organise
        was_confirm = reset and current is not None and current.auto_confirm
        defaults: dict[str, object] = {
            "mode": target.mode,
            "api": target.api,
            "base_url": target.base_url,
            "model": target.model,
            "api_key_sealed": sealed,
            "consented_base_url": consented,
        }
        if reset:
            defaults |= {"auto_organise": False, "auto_confirm": False}
        settings, _created = AISettings.objects.update_or_create(
            workspace_id=workspace_id, defaults=defaults
        )
        for name, was in ((_ORGANISE, was_organise), (_CONFIRM, was_confirm)):
            if was:
                _record_switch(workspace_id, actor, name, False)
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
            # Beside the consent event, in the same transaction; never the key or address.
            record(
                AuditCode.WORKSPACE_SETTING_CHANGED,
                actor=_audit_actor(actor),
                target=AuditTarget(TargetKind.WORKSPACE, workspace_id),
                workspace_id=workspace_id,
                values={"setting": "ai", "value": target.mode},
            )
    return settings


_ORGANISE = "ai_auto_organise"
_CONFIRM = "ai_auto_confirm"


def _record_switch(workspace_id: UUID, actor: Actor, name: str, value: bool) -> None:
    record(
        AuditCode.WORKSPACE_SETTING_CHANGED,
        actor=_audit_actor(actor),
        target=AuditTarget(TargetKind.WORKSPACE, workspace_id),
        workspace_id=workspace_id,
        values={"setting": name, "value": value},
    )


# The version of `autopilot_notice`. Raise it when the notice says the autopilot does
# more: a switch agreed to under an older version asks again (`notice_outdated`).
AUTOPILOT_NOTICE_VERSION = 2
# From this notice version on, the autopilot may apply a rule's label proposal.
RULE_LABELS_FROM_NOTICE = 2


def autopilot(workspace_id: UUID) -> Autopilot:
    """The stored switches (off without a settings row). Needs the workspace's RLS context."""
    row = AISettings.objects.filter(workspace_id=workspace_id).first()
    organise = row is not None and row.auto_organise
    confirm = row is not None and row.auto_confirm
    version = row.autopilot_notice_version if row is not None else 0
    return Autopilot(
        organise=organise,
        confirm=confirm,
        allowed=_autopilot_allowed(),
        notice_outdated=(organise or confirm) and version < AUTOPILOT_NOTICE_VERSION,
        rule_labels=organise and version >= RULE_LABELS_FROM_NOTICE,
    )


def autopilot_in_force(workspace_id: UUID) -> Autopilot:
    """What the pipeline may do now: nothing unless the server allows it and AI is on.

    Checked at the moment of use, so a server switch turned off after the fact or a row
    changed behind our back stops the autopilot at once. Needs the workspace's RLS context.
    """
    stored = autopilot(workspace_id)
    row = AISettings.objects.filter(workspace_id=workspace_id).first()
    on = stored.allowed and row is not None and row.mode != AISettings.Mode.OFF
    return Autopilot(
        organise=on and stored.organise,
        confirm=on and stored.confirm,
        allowed=stored.allowed,
        notice_outdated=stored.notice_outdated,
        rule_labels=on and stored.rule_labels,
    )


def _autopilot_allowed() -> bool:
    return bool(django_settings.BRIEVIE_AI_AUTOPILOT_ALLOWED)


def autopilot_notice() -> str:
    """What whoever switches the autopilot on must know and agree to (B1 Dutch).

    Paragraphs are separated by a blank line.
    """
    return gettext(
        "Je zet de AI-autopilot aan. Lees dit eerst.\n"
        "\n"
        "Brievie zet brieven dan zelf onder personen en labels. Zet je ook "
        '"Brieven zelf bevestigen" aan? Dan bevestigt Brievie brieven ook zelf, '
        "zonder dat iemand eerst kijkt.\n"
        "\n"
        "Een label dat een regel voorstelt, zet Brievie er dan ook zelf op. Maar alleen als "
        "de regel het woord in de afzender of bovenaan de brief vond, of als de AI zeker is "
        "van hetzelfde label. Staat het woord alleen verderop in de brief en is de AI niet "
        "zeker? Dan blijft het een voorstel.\n"
        "\n"
        'Een regel op "Automatisch toepassen" zet een label altijd zelf op.\n'
        "\n"
        "De AI leest wat er in de brief staat en gelooft dat. Maar een brief kan fout zijn, "
        "of je misleiden. Dan klopt ook de afzender, de datum of de taak niet.\n"
        "\n"
        "Brievie bevestigt een brief alleen als het de afzender, de datum en elke termijn "
        "in de brief zelf heeft gevonden. Noemt de brief een termijn die Brievie niet in "
        "een taak met een datum kan zetten? Dan laat Brievie de brief voor jou staan.\n"
        "\n"
        "Toch kan Brievie iets missen. Is een brief belangrijk? Lees dan zelf de brief.\n"
        "\n"
        'Alles wat Brievie zelf doet, krijgt het kenmerk "Automatisch door AI" (een label '
        'van een regel: "Automatisch door regel"). Je kunt het terugdraaien. Of je '
        'controleert de brief zelf met "Zelf controleren".\n'
    )


def configure_autopilot(
    *, workspace_id: UUID, organise: bool, confirm: bool, acknowledged: bool, actor: Actor
) -> Autopilot:
    """Set the two autopilot switches and audit every one that changed.

    Raises `AutopilotError`: `forbidden` or `needs_ai` when a switch would go on while the
    server forbids it or the workspace's AI is off, `ack_required` when a switch goes
    from off to on without `acknowledged`. Switching off always works. Agreeing
    (`acknowledged`) with a switch on stores the current notice version, also when no
    switch changes (agreeing again to a newer notice). Needs the RLS context.
    """
    with transaction.atomic():
        row = AISettings.objects.select_for_update().filter(workspace_id=workspace_id).first()
        now = {_ORGANISE: organise, _CONFIRM: confirm}
        before = {
            _ORGANISE: row is not None and row.auto_organise,
            _CONFIRM: row is not None and row.auto_confirm,
        }
        turned_on = [name for name, value in now.items() if value and not before[name]]
        if turned_on:
            if not _autopilot_allowed():
                raise AutopilotError("forbidden")
            if row is None or row.mode == AISettings.Mode.OFF:
                raise AutopilotError("needs_ai")
        if turned_on and not acknowledged:
            raise AutopilotError("ack_required")
        if row is not None:
            row.auto_organise = organise
            row.auto_confirm = confirm
            fields = ["auto_organise", "auto_confirm", "updated_at"]
            if acknowledged and (organise or confirm):
                row.autopilot_notice_version = AUTOPILOT_NOTICE_VERSION
                fields.append("autopilot_notice_version")
            row.save(update_fields=fields)
            for name, value in now.items():
                if value != before[name]:
                    _record_switch(workspace_id, actor, name, value)
    return autopilot(workspace_id)


def _audit_actor(actor: Actor) -> AuditActor:
    """The audit actor: the user (their name is loaded; `Actor` holds none) or the system."""
    if actor.user_id is None:
        return AuditActor.system()
    user = User.objects.only("id", "name").get(id=actor.user_id)
    return AuditActor.for_user(user, client_ip=actor.client_ip)


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
    # A key that does not open for this address is as good as none: the form asks for it.
    has_key = (
        settings.api_key_sealed is not None
        and _open_key(workspace_id, bytes(settings.api_key_sealed), settings.base_url) is not None
    )
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
        # Opens only for the address it was stored for: never sent anywhere else.
        api_key = _open_key(workspace_id, bytes(settings.api_key_sealed), settings.base_url)
        if api_key is None:
            raise PermanentError("ai_not_configured")
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
