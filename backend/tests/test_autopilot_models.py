import pytest
from django.conf import settings
from django.db.models import Model
from django.test import override_settings

from apps.documents.models import Document, DocumentAddressee, DocumentEvent, DocumentLabel
from apps.pipeline.models import AISettings
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (AISettings, "auto_organise"),
        (AISettings, "auto_confirm"),
        (DocumentLabel, "automatic"),
        (DocumentAddressee, "automatic"),
        (Document, "confirmed_by_ai"),
        (Task, "created_by_ai"),
    ],
)
def test_every_autopilot_flag_defaults_to_false(model: type[Model], field: str) -> None:
    assert getattr(model._meta.get_field(field), "default", None) is False


def test_a_new_ai_settings_row_has_both_switches_off(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        row = AISettings.objects.create(workspace=ws_a)
        row.refresh_from_db()
    assert (row.auto_organise, row.auto_confirm) == (False, False)


def test_the_new_event_choices_exist() -> None:
    assert DocumentEvent.Origin.AI.value == "ai"
    assert DocumentEvent.Origin.NAME_MATCH.value == "name_match"
    assert DocumentEvent.Kind.REOPENED.value == "reopened"


def test_the_server_switch_is_on_by_default_and_can_be_flipped() -> None:
    assert settings.BRIEVIE_AI_AUTOPILOT_ALLOWED is True
    with override_settings(BRIEVIE_AI_AUTOPILOT_ALLOWED=False):
        assert settings.BRIEVIE_AI_AUTOPILOT_ALLOWED is False
