"""The management commands of ./brievie update."""

import io
import json
import os
from datetime import timedelta
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from pytest_django import Settings

from apps.common.models import WorkspaceModel
from apps.updates import services
from apps.updates.models import ServerUpdate
from apps.updates.release import BUILD_RESERVE_BYTES, ReleaseError, space_needed


def stream(*records: tuple[str, str, int]) -> str:
    lines = []
    for version, minimum, major in records:
        lines.append(f"--- v{version}")
        lines.append(
            json.dumps(
                {
                    "format": 1,
                    "version": version,
                    "min_upgrade_from": minimum,
                    "postgres_major": major,
                }
            )
        )
    return "\n".join(lines) + "\n"


def plan(installed: str, target: str, text: str, major: str = "18") -> str:
    out = io.StringIO()
    call_command(
        "update_plan",
        "--installed",
        installed,
        "--target",
        target,
        "--postgres-major",
        major,
        stdin=io.StringIO(text),
        stdout=out,
    )
    return out.getvalue()


def test_update_plan_prints_allowed() -> None:
    assert plan("0.2.0", "0.3.0", stream(("0.3.0", "0.2.0", 18))).strip() == "allowed"


def test_update_plan_prints_already() -> None:
    assert plan("0.3.0", "v0.3.0", "").strip() == "already"


def test_update_plan_refusal_is_dutch_and_exit_1() -> None:
    text = stream(("0.3.0", "0.2.0", 18), ("0.3.1", "0.2.0", 18), ("0.4.0", "0.3.0", 18))
    with pytest.raises(CommandError) as caught:
        plan("0.2.0", "0.4.0", text)
    assert caught.value.returncode == 1
    assert "Werk eerst bij naar 0.3.1: ./brievie update 0.3.1" in str(caught.value)


@pytest.mark.parametrize(
    ("installed", "target", "major"),
    [("latest", "0.3.0", "18"), ("0.2.0", "0.3", "18"), ("0.2.0", "0.3.0", "x")],
)
def test_update_plan_bad_argument_is_exit_2(installed: str, target: str, major: str) -> None:
    with pytest.raises(CommandError) as caught:
        plan(installed, target, "", major)
    assert caught.value.returncode == 2


# --- the record --------------------------------------------------------------------------


def test_record_update_stores_versions_and_time() -> None:
    before = timezone.now()
    update = services.record_update(from_version="0.2.0", to_version="v0.3.0", backup_run_id=None)
    assert (update.from_version, update.to_version) == ("0.2.0", "0.3.0")
    assert update.finished_at.tzinfo is not None
    assert update.finished_at >= before
    assert services.last_update() == update


def test_record_update_refuses_a_malformed_version() -> None:
    with pytest.raises(ReleaseError):
        services.record_update(from_version="0.2.0", to_version="latest", backup_run_id=None)
    assert not ServerUpdate.objects.exists()


def test_last_update_is_the_newest() -> None:
    first = services.record_update(from_version="0.2.0", to_version="0.3.0", backup_run_id=None)
    ServerUpdate.objects.filter(id=first.id).update(finished_at=timezone.now() - timedelta(days=1))
    second = services.record_update(from_version="0.3.0", to_version="0.4.0", backup_run_id=None)
    assert services.last_update() == second


def test_record_update_command(capsys: pytest.CaptureFixture[str]) -> None:
    call_command(
        "record_update",
        "--from",
        "0.2.0",
        "--to",
        "0.3.0",
        "--backup-run",
        "0b6c3f7e-1d2a-4c5b-9e8f-7a6b5c4d3e2f",
    )
    assert "Vastgelegd: 0.2.0 → 0.3.0" in capsys.readouterr().out
    assert str(ServerUpdate.objects.get().backup_run_id) == "0b6c3f7e-1d2a-4c5b-9e8f-7a6b5c4d3e2f"


def test_update_status_lines(capsys: pytest.CaptureFixture[str]) -> None:
    call_command("update_status")
    assert "Nog nooit bijgewerkt met ./brievie update" in capsys.readouterr().out
    services.record_update(from_version="0.2.0", to_version="0.3.0", backup_run_id=None)
    call_command("update_status")
    assert "Laatst bijgewerkt: 0.2.0 → 0.3.0 op " in capsys.readouterr().out


def test_server_update_is_not_a_workspace_table() -> None:
    assert not issubclass(ServerUpdate, WorkspaceModel)


# --- free space before an update ---------------------------------------------------------


class _StatVfs:
    def __init__(self, free: int) -> None:
        self.f_bavail = free // 4096
        self.f_frsize = 4096


def documents(settings: Settings) -> Path:
    root = Path(settings.BRIEVIE_STORAGE_ROOT)
    (root / "ws").mkdir(parents=True)
    (root / "ws" / "page.sealed").write_bytes(b"x" * 3000)
    (root / "ws" / "other.sealed").write_bytes(b"y" * 1000)
    return root


def test_preflight_with_fallback_needs_data_plus_reserve(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = documents(settings)
    # A link is never followed nor counted.
    (root / "ws" / "link").symlink_to("/")
    settings.BRIEVIE_BACKUP_DIR_SET = False
    settings.BRIEVIE_BACKUP_REPOSITORY = ""
    monkeypatch.setattr(services, "_database_bytes", lambda: 500)
    monkeypatch.setattr(os, "statvfs", lambda path: _StatVfs(10 * 4096))

    found = services.preflight()

    assert found.fallback is True
    assert found.needed_bytes == space_needed(
        documents_bytes=4000, database_bytes=500, fallback=True
    )
    assert found.free_bytes == 10 * 4096


def test_preflight_with_a_destination_needs_the_reserve_only(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    documents(settings)
    settings.BRIEVIE_BACKUP_DIR_SET = True
    monkeypatch.setattr(services, "_database_bytes", lambda: 500)
    monkeypatch.setattr(os, "statvfs", lambda path: _StatVfs(10 * 4096))
    found = services.preflight()
    assert found.fallback is False
    assert found.needed_bytes == BUILD_RESERVE_BYTES


def test_preflight_command_refuses_with_a_dutch_sentence(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        services,
        "preflight",
        lambda: services.Preflight(
            free_bytes=1024, needed_bytes=BUILD_RESERVE_BYTES, fallback=True
        ),
    )
    with pytest.raises(CommandError) as caught:
        call_command("update_preflight")
    assert caught.value.returncode == 1
    assert "te weinig vrije ruimte" in str(caught.value)
    assert str(caught.value).endswith("Er is niets veranderd.")


def test_preflight_command_is_quiet_when_there_is_room(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        services,
        "preflight",
        lambda: services.Preflight(free_bytes=10**12, needed_bytes=10**9, fallback=False),
    )
    call_command("update_preflight")
    assert capsys.readouterr().out == ""


# --- the health check after an update ----------------------------------------------------


@pytest.mark.parametrize(
    ("status", "body", "expected", "verdict"),
    [
        (200, b'{"status": "ok", "server_version": "0.3.0"}', "0.3.0", ""),
        (200, b'{"status": "ok", "server_version": "0.2.0"}', "0.3.0", "wrong_version"),
        (200, b'{"status": "ok"}', "0.3.0", "wrong_version"),
        (503, b'{"code": "database_unavailable"}', "0.3.0", "unhealthy"),
        (200, b"not json", "0.3.0", "unhealthy"),
        (200, b"[]", "0.3.0", "unhealthy"),
    ],
)
def test_health_verdict(status: int, body: bytes, expected: str, verdict: str) -> None:
    assert services.health_verdict(status, body, expected) == verdict


def test_check_health_tries_again_until_it_is_right(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = [(0, b""), (503, b"{}"), (200, b'{"server_version": "0.3.0"}')]
    monkeypatch.setattr(services, "_get_health", lambda: answers.pop(0))
    slept: list[float] = []
    assert services.wait_until_healthy("0.3.0", tries=5, delay=5, sleep=slept.append) == ""
    assert slept == [5, 5]


def test_check_health_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services, "_get_health", lambda: (200, b'{"server_version": "0.2.0"}'))
    monkeypatch.setattr(services, "wait_until_healthy", lambda expected: "wrong_version")
    with pytest.raises(CommandError) as caught:
        call_command("check_health", "--expect-version", "0.3.0")
    assert caught.value.returncode == 1
    assert "De server antwoordt niet goed na het bijwerken." in str(caught.value)
