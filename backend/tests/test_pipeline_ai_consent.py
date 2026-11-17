from datetime import UTC, datetime
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client

from apps.pipeline import settings_service
from apps.pipeline.errors import PermanentError
from apps.pipeline.models import AIConsentEvent, AISettings
from apps.pipeline.settings_service import Actor, ConsentRequiredError
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Workspace
from tests.documents_support import admin_of

API_KEY = "sk-geheime-sleutel-123"
ACCEPT = "--accept-external-ai"


def run(*args: str) -> str:
    out = StringIO()
    call_command("configure_ai", *args, stdout=out)
    return out.getvalue()


def external(
    workspace: Workspace,
    *,
    consent: bool = True,
    supplier: str = "mistral",
    base_url: str = "",
    model: str = "",
) -> AISettings:
    return settings_service.configure(
        workspace_id=workspace.id,
        mode="external",
        supplier=supplier,
        base_url=base_url,
        model=model,
        api_key=API_KEY,
        consent=consent,
    )


def off(workspace: Workspace) -> AISettings:
    return settings_service.configure(
        workspace_id=workspace.id, mode="off", base_url="", model="", api_key=None
    )


def events(workspace: Workspace) -> list[AIConsentEvent]:
    return list(AIConsentEvent.objects.filter(workspace=workspace).order_by("created_at"))


class Terminal(StringIO):
    def isatty(self) -> bool:
        return True


def answer(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    """The administrator sits at a terminal and types `text`."""
    monkeypatch.setattr("sys.stdin", Terminal(text + "\n"))


# --- the service ---


def test_external_without_consent_is_refused_and_writes_nothing(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        with pytest.raises(ConsentRequiredError):
            external(ws_a, consent=False)

        assert not AISettings.objects.exists()
        assert not AIConsentEvent.objects.exists()


def test_consent_is_recorded_without_the_key(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        external(ws_a)
        (event,) = events(ws_a)

    assert (event.event, event.mode, event.supplier, event.host, event.model) == (
        "granted", "external", "mistral", "api.mistral.ai", "mistral-small-latest",
    )  # fmt: skip
    assert (event.actor_id, event.actor_label) == (None, "command line")
    assert event.created_at is not None
    stored = {field.name: getattr(event, field.attname) for field in event._meta.fields}
    assert API_KEY not in repr(stored)
    assert "/v1" not in repr(stored)


def test_consent_records_the_user_who_gave_it(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    with workspace_context(ws_a.id):
        settings_service.configure(
            workspace_id=ws_a.id, mode="external", base_url="", model="", api_key=API_KEY,
            actor=Actor(user_id=admin.id, label="web"), consent=True,
        )  # fmt: skip
        (event,) = events(ws_a)

    assert (event.actor_id, event.actor_label) == (admin.id, "web")


def test_a_preset_supplier_at_another_host_is_recorded_as_custom(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        external(ws_a, base_url="https://llm.example.com/v1?team=7", model="m")
        (event,) = events(ws_a)

    assert (event.supplier, event.host) == ("custom", "llm.example.com")


def test_another_model_on_the_same_host_needs_no_new_consent(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        external(ws_a)
        external(ws_a, consent=False, model="mistral-large-latest")
        first, second = events(ws_a)

    assert (first.event, second.event) == ("granted", "changed")
    assert second.model == "mistral-large-latest"


def test_another_host_needs_new_consent(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        external(ws_a)
        with pytest.raises(ConsentRequiredError):
            external(ws_a, consent=False, supplier="claude")

        assert AISettings.objects.get().base_url == "https://api.mistral.ai/v1"
        external(ws_a, supplier="claude")
        assert [e.event for e in events(ws_a)] == ["granted", "granted"]
        assert events(ws_a)[-1].supplier == "claude"


def test_another_path_or_port_at_the_same_host_needs_new_consent(ws_a: Workspace) -> None:
    # Behind a gateway the path chooses the service the text ends up at.
    with workspace_context(ws_a.id):
        external(ws_a, base_url="https://gateway.example.com/v1/mistral", model="m")
        for address in (
            "https://gateway.example.com/v1/openai",
            "https://gateway.example.com:8443/v1/mistral",
        ):
            with pytest.raises(ConsentRequiredError):
                external(ws_a, consent=False, base_url=address, model="m")


def test_a_clock_that_ran_behind_does_not_bring_a_consent_back(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        external(ws_a)
        off(ws_a)
        # The server restarted with its clock behind: the newest row looks the oldest.
        AIConsentEvent.objects.filter(event="switched_off").update(
            created_at=datetime(2020, 1, 1, tzinfo=UTC)
        )

        with pytest.raises(ConsentRequiredError):
            external(ws_a, consent=False)


def test_switching_off_is_recorded_and_ends_the_consent(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        external(ws_a)
        off(ws_a)
        last = events(ws_a)[-1]
        assert (last.event, last.mode, last.host, last.supplier) == ("switched_off", "off", "", "")

        with pytest.raises(ConsentRequiredError):
            external(ws_a, consent=False)


def test_switching_off_what_is_already_off_records_nothing(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        off(ws_a)
        off(ws_a)

        assert not AIConsentEvent.objects.exists()


def test_local_is_recorded_without_a_question(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        settings_service.configure(
            workspace_id=ws_a.id, mode="local", base_url="http://ollama:11434/v1", model="qwen",
            api_key=None,
        )  # fmt: skip
        (event,) = events(ws_a)

    assert (event.event, event.mode, event.supplier, event.host) == (
        "changed", "local", "", "ollama",
    )  # fmt: skip


def test_local_ends_the_consent_for_the_external_service(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        external(ws_a)
        settings_service.configure(
            workspace_id=ws_a.id, mode="local", base_url="http://ollama:11434/v1", model="qwen",
            api_key=None,
        )  # fmt: skip

        with pytest.raises(ConsentRequiredError):
            external(ws_a, consent=False)


def test_consent_is_invisible_to_another_workspace(ws_a: Workspace, ws_b: Workspace) -> None:
    with workspace_context(ws_a.id):
        external(ws_a)
        off(ws_a)
    with workspace_context(ws_b.id):
        assert AIConsentEvent.objects.count() == 0
        # The consent of A does not count for B.
        with pytest.raises(ConsentRequiredError):
            external(ws_b, consent=False)
    assert AIConsentEvent.objects.count() == 0  # no context: no rows
    with workspace_context(ws_a.id):
        assert AIConsentEvent.objects.count() == 2


# --- the pipeline ---


def test_external_settings_without_a_recorded_consent_give_no_provider(ws_a: Workspace) -> None:
    # A workspace set to external before consent existed, or a row changed by hand.
    with workspace_context(ws_a.id):
        AISettings.objects.create(
            workspace=ws_a, mode="external", base_url="https://api.mistral.ai/v1", model="m"
        )
        with pytest.raises(PermanentError) as excinfo:
            settings_service.provider_for(ws_a.id)

    assert excinfo.value.code == "ai_not_configured"


def test_consent_for_one_host_does_not_cover_an_address_changed_by_hand(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        external(ws_a)
        assert settings_service.provider_for(ws_a.id) is not None
        AISettings.objects.update(base_url="https://llm.example.com/v1")

        with pytest.raises(PermanentError):
            settings_service.provider_for(ws_a.id)


# --- the command ---


def test_command_refuses_external_without_confirmation(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BRIEVIE_AI_API_KEY", API_KEY)

    # Not a terminal and no flag: nobody can have read the notice.
    with pytest.raises(CommandError, match="--accept-external-ai"):
        run("--workspace", str(ws_a.id), "--mode", "external")

    with workspace_context(ws_a.id):
        assert not AISettings.objects.exists()
        assert not AIConsentEvent.objects.exists()


@pytest.mark.parametrize("typed", ["", "ja", "y", "akkoord niet"])
def test_command_refuses_anything_but_the_word(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch, typed: str
) -> None:
    monkeypatch.setenv("BRIEVIE_AI_API_KEY", API_KEY)
    answer(monkeypatch, typed)

    with pytest.raises(CommandError, match="niet aangezet"):
        run("--workspace", str(ws_a.id), "--mode", "external")

    with workspace_context(ws_a.id):
        assert not AISettings.objects.exists()
        assert not AIConsentEvent.objects.exists()


def test_command_switches_on_after_the_typed_word(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BRIEVIE_AI_API_KEY", API_KEY)
    answer(monkeypatch, " Akkoord ")

    run("--workspace", str(ws_a.id), "--mode", "external")

    with workspace_context(ws_a.id):
        assert AISettings.objects.get().mode == "external"
        (event,) = events(ws_a)
    assert (event.event, event.actor_label) == ("granted", "command line")


def test_command_says_what_leaves_the_server_before_it_asks(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BRIEVIE_AI_API_KEY", API_KEY)

    output = run("--workspace", str(ws_a.id), "--mode", "external", ACCEPT)

    for part in (
        "tekst van elke brief",
        "[BSN]",
        "namen van de labels",
        "namen van de personen",
        "nooit",
        "pagina",
        "Mistral",
        "api.mistral.ai",
        "Frankrijk",
        "voorwaarden van de dienst",
        "iedereen van wie post",
        "Thuis",
    ):
        assert part in output, part
    assert API_KEY not in output


def test_command_names_the_united_states_for_claude(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BRIEVIE_AI_API_KEY", API_KEY)

    output = run("--workspace", str(ws_a.id), "--mode", "external", "--supplier", "claude", ACCEPT)

    assert "Verenigde Staten" in output


def test_command_does_not_guess_the_country_of_another_service(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BRIEVIE_AI_API_KEY", API_KEY)

    output = run(
        "--workspace", str(ws_a.id), "--mode", "external", "--base-url",
        "https://llm.example.com/v1", "--model", "m", ACCEPT,
    )  # fmt: skip

    assert "llm.example.com" in output
    assert "Frankrijk" not in output
    assert "Mistral" not in output
    assert "onbekend" in output


def test_command_asks_again_only_for_another_service(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BRIEVIE_AI_API_KEY", API_KEY)
    run("--workspace", str(ws_a.id), "--mode", "external", ACCEPT)

    # Same service, other model: no question, so it also works without a terminal.
    run("--workspace", str(ws_a.id), "--mode", "external", "--model", "mistral-large-latest")
    with pytest.raises(CommandError, match="--accept-external-ai"):
        run("--workspace", str(ws_a.id), "--mode", "external", "--supplier", "claude")

    with workspace_context(ws_a.id):
        assert [e.event for e in events(ws_a)] == ["granted", "changed"]
        assert AISettings.objects.get().model == "mistral-large-latest"


def test_command_checks_the_settings_before_it_asks(ws_a: Workspace) -> None:
    with pytest.raises(CommandError, match="model"):
        run("--workspace", str(ws_a.id), "--mode", "external", "--supplier", "openai", ACCEPT)


def test_command_records_switching_off(ws_a: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIEVIE_AI_API_KEY", API_KEY)
    run("--workspace", str(ws_a.id), "--mode", "external", ACCEPT)

    run("--workspace", str(ws_a.id), "--mode", "off")

    with workspace_context(ws_a.id):
        assert [e.event for e in events(ws_a)] == ["granted", "switched_off"]


# --- the API ---


def api_time(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def ai_status(client: Client, workspace: Workspace) -> dict[str, object]:
    client.force_login(admin_of(workspace))
    response = client.get(f"/api/v1/workspaces/{workspace.id}/ai")
    assert response.status_code == 200
    body: dict[str, object] = response.json()
    return body


def test_status_names_the_service_and_the_day_of_consent(client: Client, ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        external(ws_a)
        external(ws_a, consent=False, model="mistral-large-latest")
        granted = events(ws_a)[0]

    body = ai_status(client, ws_a)

    assert body == {
        "mode": "external",
        "supplier": "mistral",
        "host": "api.mistral.ai",
        "since": api_time(granted.created_at),
    }


def test_status_after_switching_off_names_nothing(client: Client, ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        external(ws_a)
        off(ws_a)

    assert ai_status(client, ws_a) == {
        "mode": "off", "supplier": None, "host": None, "since": None,
    }  # fmt: skip


def test_status_of_local_keeps_the_own_address_to_itself(client: Client, ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        settings_service.configure(
            workspace_id=ws_a.id, mode="local", base_url="http://ollama:11434/v1", model="qwen",
            api_key=None,
        )  # fmt: skip
        (event,) = events(ws_a)

    body = ai_status(client, ws_a)

    assert (body["mode"], body["supplier"], body["host"]) == ("local", None, None)
    assert body["since"] == api_time(event.created_at)


def test_status_without_recorded_consent_has_no_date(client: Client, ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        AISettings.objects.create(
            workspace=ws_a, mode="external", base_url="https://api.mistral.ai/v1", model="m"
        )

    assert ai_status(client, ws_a) == {
        "mode": "external", "supplier": None, "host": "api.mistral.ai", "since": None,
    }  # fmt: skip


def test_status_fields_are_optional_in_the_contract() -> None:
    from brievie.api import api

    schema = api.get_openapi_schema()["components"]["schemas"]["AiStatusOut"]

    assert schema["required"] == ["mode"]
    assert set(schema["properties"]) == {"mode", "supplier", "host", "since"}
