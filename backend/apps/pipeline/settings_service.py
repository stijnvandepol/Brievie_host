"""How a workspace has its documents read: its AI settings and the provider they give."""

import ipaddress
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


def configure(
    *, workspace_id: UUID, mode: str, base_url: str, model: str, api_key: str | None
) -> AISettings:
    """Set the workspace's AI mode. Needs the workspace's RLS context.

    `external`: an https service and its key (default: Mistral). `local`: a service
    on the own network only, so no document text leaves it. `off`: nothing.
    """
    if mode not in AISettings.Mode.values:
        raise ValueError(f"Unknown mode: {mode!r}")
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
        return "." not in host or host == "localhost" or host.endswith(".local")
    return address.is_loopback or address.is_private
