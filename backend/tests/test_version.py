"""One version number: backend/pyproject.toml, followed by the change log and release.json."""

import json
import re
import tomllib
from pathlib import Path

from apps.backup import manifest
from apps.common.version import server_version

ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = ROOT / "CHANGELOG.md"
RELEASE = ROOT / "deploy" / "release.json"


def test_server_version_reads_pyproject() -> None:
    project = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text())["project"]
    assert server_version() == project["version"]


def test_version_is_semantic() -> None:
    assert re.fullmatch(r"(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})", server_version())


def test_lock_file_has_the_same_version() -> None:
    lock = tomllib.loads((ROOT / "backend" / "uv.lock").read_text())
    package = next(p for p in lock["package"] if p["name"] == "brievie")
    assert package["version"] == server_version()


def test_changelog_top_entry_is_the_version() -> None:
    headings = re.findall(r"^## \[([^\]]+)\]", CHANGELOG.read_text(), re.MULTILINE)
    released = [heading for heading in headings if heading != "Onuitgebracht"]
    assert released[0] == server_version()


def test_changelog_entries_have_a_date() -> None:
    lines = [line for line in CHANGELOG.read_text().splitlines() if line.startswith("## [")]
    assert lines
    for line in lines:
        if line == "## [Onuitgebracht]":
            continue
        assert re.fullmatch(r"## \[\d+\.\d+\.\d+\] - \d{4}-\d{2}-\d{2}", line), line


def test_changelog_uses_the_dutch_headings_only() -> None:
    allowed = {
        "Voor je bijwerkt",
        "Toegevoegd",
        "Gewijzigd",
        "Opgelost",
        "Verwijderd",
        "Beveiliging",
    }
    found = set(re.findall(r"^### (.+)$", CHANGELOG.read_text(), re.MULTILINE))
    assert found <= allowed, found - allowed


def test_release_json_matches_the_version() -> None:
    release = json.loads(RELEASE.read_text())
    assert set(release) == {"format", "version", "min_upgrade_from", "postgres_major"}
    assert release["format"] == 1
    assert release["version"] == server_version()


def test_release_json_postgres_major_matches_compose() -> None:
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text()
    majors = set(re.findall(r"(?<!@)\bpostgres:(\d+)", compose))
    assert majors == {str(json.loads(RELEASE.read_text())["postgres_major"])}


def test_web_package_is_not_a_version_source() -> None:
    assert json.loads((ROOT / "web" / "package.json").read_text())["version"] == "0.0.0"


def test_manifest_records_the_server_version() -> None:
    assert manifest.brievie_version() == server_version()
