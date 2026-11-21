"""The language columns have a database default: a raw INSERT that leaves them out (a
seed, a restore script, the perf data) keeps working."""

import uuid

from django.db import connection
from django.utils import timezone

from apps.accounts.models import User
from apps.workspaces.models import Workspace


def test_a_raw_workspace_insert_without_summary_language_gets_dutch() -> None:
    workspace_id = uuid.uuid4()
    now = timezone.now()
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO workspaces_workspace (id, created_at, updated_at, name, kind, modules,"
            " allow_member_export, everyone_sees_all, departments_enabled, admins_read_all,"
            " care_is_private, emergency_access)"
            " VALUES (%s, %s, %s, 'Rauw', 'home', '{}', true, true, false, true, false, false)",
            [workspace_id, now, now],
        )

    assert Workspace.objects.get(id=workspace_id).summary_language == "nl"


def test_a_raw_user_insert_without_language_has_no_choice() -> None:
    user_id = uuid.uuid4()
    now = timezone.now()
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO accounts_user (id, password, is_superuser, created_at, updated_at,"
            " email, name, explanation_language, is_active, is_server_admin)"
            " VALUES (%s, '!', false, %s, %s, 'rauw@example.test', 'Rauw', 'nl', true, false)",
            [user_id, now, now],
        )

    assert User.objects.get(id=user_id).language == ""
