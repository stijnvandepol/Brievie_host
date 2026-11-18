import json
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest
from django.conf import settings as django_settings
from django.db import connection
from django.test import Client
from psycopg.conninfo import make_conninfo
from pytest_django import Settings

from apps.backup.errors import BackupError
from apps.backup.manifest import (
    MANIFEST_FORMAT,
    Manifest,
    WorkspaceCounts,
    brievie_version,
    build_manifest,
    manifest_from_json,
    manifest_to_json,
    unknown_migrations,
)
from apps.devices.pki import certificate_sha256
from apps.workspaces.models import Workspace
from tests.documents_support import admin_of, jpeg, upload

CA_FIXTURE = Path(__file__).parent / "fixtures" / "ca_root.crt"


def sample_manifest(**changes: object) -> Manifest:
    values: dict[str, object] = {
        "format": MANIFEST_FORMAT,
        "run_id": uuid.uuid4(),
        "created_at": datetime(2026, 11, 17, 21, 3, tzinfo=timezone(timedelta(hours=1))),
        "brievie_version": "0.1.0",
        "migrations": {"documents": ["0001_initial"]},
        "postgres_version": "18.1",
        "hostname": "brievie.local",
        "authority_sha256": "ab" * 32,
        "workspaces": {uuid.uuid4(): WorkspaceCounts(3, 5, 5)},
    }
    values.update(changes)
    return Manifest(**values)  # type: ignore[arg-type]


def upload_letter(workspace: Workspace, pages: int) -> None:
    client = Client()
    client.force_login(admin_of(workspace))
    response = upload(client, workspace.id, [jpeg(f"p{n}-{workspace.id}") for n in range(pages)])
    assert response.status_code == 201


@pytest.mark.django_db(transaction=True)
def test_manifest_counts_every_workspace_as_the_backup_role(
    ws_a: Workspace, ws_b: Workspace, backup_conninfo: str, tmp_path: Path
) -> None:
    upload_letter(ws_a, 2)
    upload_letter(ws_b, 1)

    manifest = build_manifest(
        run_id=uuid.uuid4(),
        conninfo=backup_conninfo,
        storage_root=Path(django_settings.BRIEVIE_STORAGE_ROOT),
        authority_dir=tmp_path / "no-authority",
    )

    assert manifest.workspaces[ws_a.id] == WorkspaceCounts(1, 2, 2)
    assert manifest.workspaces[ws_b.id] == WorkspaceCounts(1, 1, 1)
    assert manifest.authority_sha256 is None
    assert "documents" in manifest.migrations
    assert manifest.postgres_version
    assert manifest.created_at.tzinfo is not None


@pytest.mark.django_db(transaction=True)
def test_manifest_lists_a_workspace_without_documents(
    ws_a: Workspace, backup_conninfo: str, tmp_path: Path
) -> None:
    manifest = build_manifest(
        run_id=uuid.uuid4(),
        conninfo=backup_conninfo,
        storage_root=tmp_path / "missing",
        authority_dir=tmp_path,
    )
    assert manifest.workspaces[ws_a.id] == WorkspaceCounts(0, 0, 0)


@pytest.mark.django_db(transaction=True)
def test_manifest_as_the_owner_fails_loudly(ws_a: Workspace, tmp_path: Path) -> None:
    upload_letter(ws_a, 1)
    owner = django_settings.DATABASE_OWNER
    assert owner is not None
    owner_conninfo = make_conninfo(
        dbname=str(connection.settings_dict["NAME"]),
        user=str(owner["USER"]),
        password=str(owner["PASSWORD"]),
        host=str(owner["HOST"]),
        port=str(owner["PORT"]),
    )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        build_manifest(
            run_id=uuid.uuid4(),
            conninfo=owner_conninfo,
            storage_root=Path(django_settings.BRIEVIE_STORAGE_ROOT),
            authority_dir=tmp_path,
        )


def test_manifest_json_round_trip() -> None:
    manifest = sample_manifest()
    text = manifest_to_json(manifest)
    assert manifest_from_json(text) == manifest
    assert manifest_from_json(text).created_at.utcoffset() == timedelta(hours=1)
    data = json.loads(text)
    assert list(data) == [
        "format",
        "run_id",
        "created_at",
        "brievie_version",
        "migrations",
        "postgres_version",
        "hostname",
        "authority_sha256",
        "workspaces",
    ]
    assert data["created_at"] == "2026-11-17T21:03:00+01:00"
    (counts,) = data["workspaces"].values()
    assert counts == {"documents": 3, "pages": 5, "page_files": 5}


def test_manifest_without_authority_round_trips() -> None:
    manifest = sample_manifest(authority_sha256=None, created_at=datetime(2026, 1, 1, tzinfo=UTC))
    assert manifest_from_json(manifest_to_json(manifest)) == manifest


@pytest.mark.parametrize("text", ["not json", "[]", '{"format": 2}', '{"format": 1}'])
def test_manifest_of_another_format_or_shape_is_refused(text: str) -> None:
    with pytest.raises(BackupError) as caught:
        manifest_from_json(text)
    assert caught.value.code == "manifest_unreadable"


def test_manifest_of_format_two_is_refused() -> None:
    data = json.loads(manifest_to_json(sample_manifest()))
    data["format"] = 2
    with pytest.raises(BackupError) as caught:
        manifest_from_json(json.dumps(data))
    assert caught.value.code == "manifest_unreadable"


def test_unknown_migrations_finds_a_newer_schema() -> None:
    manifest = sample_manifest(migrations={"documents": ["0001_initial", "9999_future"]})
    assert unknown_migrations(manifest) == ["documents.9999_future"]


def test_authority_fingerprint_matches_pki(tmp_path: Path) -> None:
    pem = CA_FIXTURE.read_text()
    (tmp_path / "root.crt").write_text(pem)
    # The test database is not needed: build only the fingerprint through the public API.
    from apps.backup.manifest import authority_fingerprint

    assert authority_fingerprint(tmp_path) == certificate_sha256(pem)
    assert authority_fingerprint(tmp_path / "other") is None


def test_brievie_version_reads_pyproject() -> None:
    assert brievie_version() == "0.1.0"


def test_hostname_comes_from_settings(settings: Settings) -> None:
    from apps.backup.manifest import host_name

    settings.BRIEVIE_HOSTNAME = "brievie.example"
    assert host_name() == "brievie.example"


def _manifest_data() -> dict[str, object]:
    data: dict[str, object] = json.loads(manifest_to_json(sample_manifest()))
    return data


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("created_at", "2026-11-17T21:03:00"),
        ("migrations", {"documents": "0001_initial"}),
        ("migrations", {"documents": [1]}),
        ("migrations", ["documents"]),
        ("format", True),
        ("format", 1.0),
        ("hostname", "brievie.local\nrun_id=forged"),
        ("hostname", "brievie.local\r"),
        ("hostname", "brievie\tlocal"),
        ("hostname", "brievie\x00local"),
        ("hostname", "brievie\x7flocal"),
        ("hostname", "brievie\x85local"),
        ("hostname", "brievie\u2028local"),
        ("hostname", "a" * 254),
        ("hostname", 5),
        ("brievie_version", "0.1.0\nhostname=x"),
        ("brievie_version", "0.1.0\x1b[31m"),
        ("brievie_version", ""),
        ("brievie_version", "1" * 65),
    ],
)
def test_manifest_with_a_wrong_field_is_refused(field: str, value: object) -> None:
    data = _manifest_data()
    data[field] = value
    with pytest.raises(BackupError) as caught:
        manifest_from_json(json.dumps(data))
    assert caught.value.code == "manifest_unreadable"


@pytest.mark.django_db(transaction=True)
def test_manifest_is_read_in_one_repeatable_read_transaction(
    ws_a: Workspace, backup_conninfo: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[psycopg.Connection[tuple[object, ...]]] = []
    real_connect = psycopg.connect

    def spy(conninfo: str) -> psycopg.Connection[tuple[object, ...]]:
        conn = real_connect(conninfo)
        opened.append(conn)
        return conn

    monkeypatch.setattr("apps.backup.manifest.psycopg.connect", spy)
    build_manifest(
        run_id=uuid.uuid4(),
        conninfo=backup_conninfo,
        storage_root=tmp_path,
        authority_dir=tmp_path,
    )
    (conn,) = opened
    assert conn.isolation_level == psycopg.IsolationLevel.REPEATABLE_READ
    assert conn.read_only is True


@pytest.mark.parametrize(
    ("hostname", "version"),
    [
        ("brievie.local", "0.1.0"),
        ("192.168.1.20", "1.2.3rc1+g1a2b"),
        ("my_host-1", "v1"),
        ("2001:db8::1", "0.1 .0"),
        ("[fe80::1%eth0]", "1.0"),
        ("brievie local", "1.0"),
        ("", "1.0"),
        ("a" * 253, "1" * 64),
    ],
)
def test_manifest_accepts_plain_host_names_and_versions(hostname: str, version: str) -> None:
    data = _manifest_data()
    data["hostname"] = hostname
    data["brievie_version"] = version
    manifest = manifest_from_json(json.dumps(data))
    assert (manifest.hostname, manifest.brievie_version) == (hostname, version)


@pytest.mark.parametrize("hostname", ["2001:db8::1", "fe80::1%eth0", "192.168.1.20", ""])
def test_a_host_name_brievie_accepts_round_trips(hostname: str) -> None:
    manifest = sample_manifest(hostname=hostname)
    assert manifest_from_json(manifest_to_json(manifest)) == manifest


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hostname", "brievie.local\nrun_id=forged"),
        ("hostname", "a" * 254),
        ("brievie_version", "0.1.0\r"),
        ("brievie_version", ""),
    ],
)
def test_a_manifest_that_could_not_be_read_back_is_never_written(field: str, value: str) -> None:
    with pytest.raises(BackupError) as caught:
        manifest_to_json(sample_manifest(**{field: value}))
    expected = "hostname_unusable" if field == "hostname" else "manifest_unreadable"
    assert caught.value.code == expected


@pytest.mark.django_db(transaction=True)
def test_building_a_manifest_refuses_a_host_name_with_a_line_break(
    ws_a: Workspace, backup_conninfo: str, tmp_path: Path, settings: Settings
) -> None:
    settings.BRIEVIE_HOSTNAME = "brievie.local\nrun_id=forged"
    with pytest.raises(BackupError) as caught:
        build_manifest(
            run_id=uuid.uuid4(),
            conninfo=backup_conninfo,
            storage_root=tmp_path,
            authority_dir=tmp_path,
        )
    assert caught.value.code == "hostname_unusable"
    assert "BRIEVIE_HOSTNAME" in caught.value.message


@pytest.mark.django_db(transaction=True)
def test_build_manifest_counts_in_a_given_open_snapshot(
    ws_a: Workspace, backup_conninfo: str, tmp_path: Path
) -> None:
    from apps.backup.manifest import counting_transaction, export_snapshot

    with counting_transaction(backup_conninfo) as conn:
        snapshot_id = export_snapshot(conn)
        upload_letter(ws_a, 1)  # after the snapshot: not in it
        manifest = build_manifest(
            run_id=uuid.uuid4(), conn=conn, storage_root=tmp_path, authority_dir=tmp_path
        )
        assert not conn.closed  # the caller's connection stays open for the dump
        # Another transaction can import the snapshot, as pg_dump --snapshot does.
        with psycopg.connect(backup_conninfo) as other:
            other.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            other.execute("SET row_security = off")
            other.execute(f"SET TRANSACTION SNAPSHOT '{snapshot_id}'")
            row = other.execute("SELECT count(*) FROM documents_document").fetchone()
            assert row == (0,)
    assert manifest.workspaces[ws_a.id].documents == 0
    assert conn.closed


def test_build_manifest_needs_a_connection_or_a_conninfo(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        build_manifest(run_id=uuid.uuid4(), storage_root=tmp_path, authority_dir=tmp_path)
