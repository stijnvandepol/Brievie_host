"""How a workspace has its documents read: its AI settings and the provider they give."""

import ipaddress
import re
from urllib.parse import urlsplit
from uuid import UUID

from django.db import transaction

from apps.common import crypto
from apps.pipeline.errors import PermanentError
from apps.pipeline.models import AISettings
from apps.pipeline.providers import LLMProvider, OpenAICompatibleProvider
from apps.workspaces.keys import workspace_key

DEFAULT_EXTERNAL_URL = "https://api.mistral.ai/v1"
DEFAULT_EXTERNAL_MODEL = "mistral-small-latest"
_KEY_AAD = b"ai-api-key"
# A DNS name is at most 253 characters, each label at most 63.
_MAX_HOST = 253
_HOST_LABEL = re.compile(r"[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?", re.IGNORECASE)
# Integer and hex forms that `inet_aton` and most resolvers read as an IPv4 address:
# `134744072`, `0x7f000001`, `017700000001`.
_NUMERIC_HOST = re.compile(r"(0x[0-9a-f]*|[0-9]+)", re.IGNORECASE)


def configure(
    *, workspace_id: UUID, mode: str, base_url: str, model: str, api_key: str | None
) -> AISettings:
    """Set the workspace's AI mode. Needs the workspace's RLS context.

    `external`: an https service and its key (default: Mistral). `local`: a service
    on the own network only, so no document text leaves it. `off`: nothing.
    """
    if mode not in AISettings.Mode.values:
        raise ValueError(f"Unknown mode: {mode!r}")
    if mode != AISettings.Mode.OFF:
        _check_address(base_url or DEFAULT_EXTERNAL_URL)
    sealed: bytes | None = None
    if mode == AISettings.Mode.OFF:
        base_url, model = "", ""
    elif mode == AISettings.Mode.EXTERNAL:
        base_url = base_url or DEFAULT_EXTERNAL_URL
        model = model or DEFAULT_EXTERNAL_MODEL
        if urlsplit(base_url).scheme != "https":
            raise ValueError("An external AI service needs an https address.")
        if not api_key:
            raise ValueError("An external AI service needs an API key.")
    else:
        if not is_on_own_network(base_url):
            raise ValueError("A local AI service must be on the own network.")
        if not model:
            raise ValueError("A local AI service needs a model name.")
    with transaction.atomic():
        if api_key and mode != AISettings.Mode.OFF:
            sealed = crypto.seal(workspace_key(workspace_id), api_key.encode(), aad=_KEY_AAD)
        settings, _ = AISettings.objects.update_or_create(
            workspace_id=workspace_id,
            defaults={"mode": mode, "base_url": base_url, "model": model, "api_key_sealed": sealed},
        )
    return settings


def provider_for(workspace_id: UUID) -> LLMProvider | None:
    """The provider for the workspace, or `None` when its AI is off."""
    settings = AISettings.objects.filter(workspace_id=workspace_id).first()
    if settings is None or settings.mode == AISettings.Mode.OFF:
        return None
    if settings.mode == AISettings.Mode.LOCAL and not is_on_own_network(settings.base_url):
        # Checked again here: a row changed behind our back must not send text outside.
        raise PermanentError("ai_not_configured")
    api_key = None
    if settings.api_key_sealed is not None:
        api_key = crypto.open_sealed(
            workspace_key(workspace_id), bytes(settings.api_key_sealed), aad=_KEY_AAD
        ).decode()
    return OpenAICompatibleProvider(
        base_url=settings.base_url, api_key=api_key, model=settings.model
    )


def _check_address(url: str) -> None:
    """Refuse an address that would not fit its column or is not a host name at all.

    The host becomes the provider name on every proposal: a value that cannot be
    stored would fail every document later instead of here.
    """
    if len(url) > (AISettings._meta.get_field("base_url").max_length or 0):
        raise ValueError("The address of the AI service is too long.")
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        raise ValueError("The address of the AI service is not valid.") from None
    if host.startswith("[") or _is_ip_literal(host):
        return
    labels = host.rstrip(".").split(".")
    if len(host) > _MAX_HOST or not all(_HOST_LABEL.fullmatch(label) for label in labels):
        raise ValueError("The host name of the AI service is not valid.")


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
    return address.is_loopback or address.is_private
