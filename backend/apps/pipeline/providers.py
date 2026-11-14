"""The AI services that read a letter. One interface, with a fake in the tests."""

from functools import cache
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from apps.pipeline.errors import PermanentError, RetryableError
from apps.pipeline.schema import json_schema

# More text than this is cut off (at a page boundary when possible).
MAX_TEXT_CHARS = 30000
_PAGE_BREAK = "\f"
_PROMPT = Path(__file__).parent / "prompts" / "extract_nl.txt"


class ProviderUnavailable(RetryableError):  # noqa: N818 - named for what happened
    """The service did not answer, or asked us to come back later."""

    def __init__(self) -> None:
        super().__init__("ai_unavailable")


class ProviderRejected(PermanentError):  # noqa: N818
    """The service refused the request; trying again will not help."""


class LLMProvider(Protocol):
    name: str
    model: str

    def extract(self, text: str) -> str:
        """The model's answer for the letter `text`: a JSON string."""
        ...


class OpenAICompatibleProvider:
    """Any service that speaks the OpenAI chat-completions API: Mistral, Ollama, ..."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport
        self.name = urlsplit(base_url).hostname or "unknown"
        self.model = model

    def extract(self, text: str) -> str:
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": _cut(text)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "brievie_extraction",
                    "schema": json_schema(),
                    "strict": True,
                },
            },
        }
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        # Errors are raised `from None`: httpx's own exceptions and the response body
        # can carry the key or the letter, and must not travel with ours.
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                response = client.post(self._url, json=body, headers=headers)
        except httpx.HTTPError:
            raise ProviderUnavailable from None
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderUnavailable
        if response.status_code in (401, 403):
            raise ProviderRejected("ai_key_rejected")
        if response.status_code != 200:
            raise ProviderRejected("ai_request_rejected")
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, LookupError, TypeError):
            raise PermanentError("ai_invalid_output") from None
        if not isinstance(content, str):
            raise PermanentError("ai_invalid_output")
        return content


@cache
def _system_prompt() -> str:
    return _PROMPT.read_text(encoding="utf-8")


def _cut(text: str) -> str:
    """At most MAX_TEXT_CHARS: whole pages while they fit, else a hard cut."""
    if len(text) <= MAX_TEXT_CHARS:
        return text
    kept = text[:MAX_TEXT_CHARS]
    boundary = kept.rfind(_PAGE_BREAK)
    if boundary <= 0:
        return kept
    return kept[:boundary].rstrip("\n")
