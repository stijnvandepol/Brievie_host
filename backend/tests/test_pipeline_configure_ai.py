from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.pipeline.models import AISettings
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace

API_KEY = "sk-geheime-sleutel-123"
ACCEPT = "--accept-external-ai"


def run(*args: str) -> str:
    out = StringIO()
    call_command("configure_ai", *args, stdout=out)
    return out.getvalue()


def test_external_reads_the_key_from_the_environment(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BRIEVIE_AI_API_KEY", API_KEY)

    output = run("--workspace", str(ws_a.id), "--mode", "external", ACCEPT)

    with workspace_context(ws_a.id, actor=SYSTEM):
        stored = AISettings.objects.get()
    assert stored.mode == "external"
    assert stored.base_url == "https://api.mistral.ai/v1"
    assert stored.model == "mistral-small-latest"
    assert stored.api_key_sealed is not None
    assert API_KEY not in output
    assert "external" in output


def test_key_cannot_be_given_on_the_command_line(ws_a: Workspace) -> None:
    with pytest.raises((CommandError, SystemExit)):
        run("--workspace", str(ws_a.id), "--mode", "external", "--api-key", API_KEY)


def test_off_needs_no_key(ws_a: Workspace) -> None:
    run("--workspace", str(ws_a.id), "--mode", "off")

    with workspace_context(ws_a.id, actor=SYSTEM):
        assert AISettings.objects.get().mode == "off"


def test_local_with_a_public_address_is_refused(ws_a: Workspace) -> None:
    with pytest.raises(CommandError, match="network"):
        run("--workspace", str(ws_a.id), "--mode", "local", "--base-url", "https://example.com/v1",
            "--model", "qwen")  # fmt: skip


def test_unknown_workspace_is_refused() -> None:
    with pytest.raises(CommandError, match="workspace"):
        run("--workspace", "00000000-0000-0000-0000-000000000000", "--mode", "off")


def test_supplier_claude_reads_the_key_from_the_environment(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BRIEVIE_AI_API_KEY", API_KEY)

    output = run("--workspace", str(ws_a.id), "--mode", "external", "--supplier", "claude", ACCEPT)

    with workspace_context(ws_a.id, actor=SYSTEM):
        stored = AISettings.objects.get()
    assert (stored.api, stored.base_url, stored.model) == (
        "anthropic", "https://api.anthropic.com", "claude-opus-5-5",
    )  # fmt: skip
    assert API_KEY not in output
    assert "api.anthropic.com" in output


def test_supplier_openai_without_a_model_is_refused(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BRIEVIE_AI_API_KEY", API_KEY)

    with pytest.raises(CommandError, match="model"):
        run("--workspace", str(ws_a.id), "--mode", "external", "--supplier", "openai")


def test_unknown_supplier_is_refused_by_the_parser(ws_a: Workspace) -> None:
    with pytest.raises((CommandError, SystemExit)):
        run("--workspace", str(ws_a.id), "--mode", "external", "--supplier", "gemini")
