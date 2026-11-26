"""The scan folder's pure rules: names, stability and ids (intake plan Task 11)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from apps.intake.scan_rules import (
    RESERVED,
    Fingerprint,
    FolderCandidate,
    classify_subfolders,
    default_folder_name,
    folder_targets,
    is_candidate_name,
    is_reserved,
    person_folder_names,
    scan_client_id,
    stable,
    valid_folder_name,
    valid_person_folder_name,
)

A = uuid.UUID("00000000-0000-0000-0000-00000000000a")
B = uuid.UUID("00000000-0000-0000-0000-00000000000b")
C = uuid.UUID("00000000-0000-0000-0000-00000000000c")
NOW = datetime(2026, 11, 26, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _database() -> None:
    """Overrides the suite's database fixture: these rules are pure."""


@pytest.fixture(scope="session", autouse=True)
def _test_role_is_subject_to_rls() -> None:
    """Overrides the suite's session check: no database is used here."""


def fingerprint(size: int = 100, mtime_ns: int = 1) -> Fingerprint:
    return Fingerprint(device=1, inode=2, size=size, mtime_ns=mtime_ns)


# Stability


def test_not_stable_at_the_first_look() -> None:
    take, since = stable(None, None, fingerprint(), NOW, stable_seconds=60)
    assert not take
    assert since == NOW


def test_stable_after_sixty_seconds_unchanged() -> None:
    first = fingerprint()
    take, since = stable(first, NOW, first, NOW + timedelta(seconds=59), stable_seconds=60)
    assert (take, since) == (False, NOW)
    take, since = stable(first, NOW, first, NOW + timedelta(seconds=60), stable_seconds=60)
    assert (take, since) == (True, NOW)


def test_a_change_restarts_the_clock() -> None:
    later = NOW + timedelta(seconds=120)
    take, since = stable(fingerprint(100), NOW, fingerprint(200), later, stable_seconds=60)
    assert (take, since) == (False, later)
    take, since = stable(fingerprint(100, 1), NOW, fingerprint(100, 2), later, stable_seconds=60)
    assert (take, since) == (False, later)


def test_an_empty_file_is_never_taken() -> None:
    empty = fingerprint(0)
    take, _since = stable(empty, NOW, empty, NOW + timedelta(hours=1), stable_seconds=60)
    assert not take


# Names


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Scan.PDF", True),
        ("brief.pdf", True),
        ("foto.JPG", True),
        ("foto.jpeg", True),
        ("scan.png", True),
        ("scan.pdf.part", False),
        ("scan.tmp", False),
        ("._scan.pdf", False),
        (".scan.pdf", False),
        ("~scan.pdf", False),
        ("scan.gif", False),
        ("pdf", False),
    ],
)
def test_candidate_names(name: str, expected: bool) -> None:
    assert is_candidate_name(name) is expected


@pytest.mark.parametrize("name", ["mislukt", "Mislukt", ".hidden", "@eaDir", "#recycle", "~tmp"])
def test_reserved_names(name: str) -> None:
    assert is_reserved(name)
    assert RESERVED == "mislukt"


def test_ordinary_names_are_not_reserved() -> None:
    assert not is_reserved("Anne")
    assert not is_reserved("Oude scans")


@pytest.mark.parametrize(
    ("name", "valid"),
    [
        ("kantoor", True),
        ("kantoor-den-haag", True),
        ("thuis2", True),
        ("Kantoor", False),
        ("kantoor den haag", False),
        ("", False),
        ("a" * 61, False),
        ("mislukt", False),
        ("../etc", False),
    ],
)
def test_valid_folder_names(name: str, valid: bool) -> None:
    assert valid_folder_name(name) is valid


def test_default_folder_name() -> None:
    assert default_folder_name("Kantoor Den Haag") == "kantoor-den-haag"
    assert default_folder_name("Café 't Hoekje") == "cafe-t-hoekje"
    assert valid_folder_name(default_folder_name("!!!"))
    assert valid_folder_name(default_folder_name("Mislukt"))
    assert valid_folder_name(default_folder_name("x" * 100))


def test_folder_names_unique_ignoring_case() -> None:
    assert person_folder_names({A: "Anne", B: "anne"}, taken={}) == {A: "Anne", B: "anne (2)"}


def test_a_renamed_person_keeps_the_taken_name() -> None:
    assert person_folder_names({A: "Anne de Wit"}, taken={A: "Anne"}) == {A: "Anne"}


def test_a_new_name_avoids_a_taken_one() -> None:
    names = person_folder_names({A: "Anne", B: "ANNE"}, taken={A: "anne"})
    assert names == {A: "anne", B: "ANNE (2)"}


def test_mislukt_as_a_person_name_does_not_become_mislukt() -> None:
    name = person_folder_names({A: "Mislukt"}, taken={})[A]
    assert name.casefold() != "mislukt"
    assert not is_reserved(name)


def test_department_and_person_same_name() -> None:
    names = person_folder_names({A: "Financien", B: "Financien"}, taken={})
    assert names == {A: "Financien", B: "Financien (2)"}


def test_unsafe_characters_are_removed() -> None:
    names = person_folder_names(
        {A: 'Jan: "de" <Vries>?', B: "  .verborgen", C: "a\x00b\u202ec/d\\e|f*g"}, taken={}
    )
    assert names[A] == "Jan de Vries"
    assert not names[B].startswith(".")
    assert names[C] == "abcdefg"
    for name in names.values():
        assert not is_reserved(name)


def test_long_names_are_cut_with_room_for_the_number() -> None:
    names = person_folder_names({A: "x" * 100, B: "X" * 100}, taken={})
    assert names[A] == "x" * 60
    assert names[B].endswith(" (2)")
    assert len(names[B]) <= 60


@pytest.mark.parametrize(
    ("name", "valid"),
    [
        ("Oude scans", True),
        ("Financien (2)", True),
        ("", False),
        ("mislukt", False),
        (".verborgen", False),
        ("a/b", False),
        ("naam.", False),
        (" naam", False),
        ("x" * 61, False),
    ],
)
def test_valid_person_folder_names(name: str, valid: bool) -> None:
    assert valid_person_folder_name(name) is valid


# Which folders exist


def candidate(
    kind: str = "person",
    *,
    archived: bool = False,
    has_account: bool = True,
    added_by_admin: bool = False,
    state: str | None = None,
    person_id: uuid.UUID = A,
) -> FolderCandidate:
    return FolderCandidate(
        id=person_id,
        kind=kind,
        display_name="Iemand",
        archived=archived,
        has_account=has_account,
        added_by_admin=added_by_admin,
        state=state,
    )


def test_a_person_with_an_account_gets_a_folder() -> None:
    assert folder_targets([candidate()], departments_enabled=False) == {A: "active"}


def test_a_person_without_an_account_only_when_added_by_hand() -> None:
    assert folder_targets([candidate(has_account=False)], departments_enabled=False) == {}
    added = candidate(has_account=False, added_by_admin=True, state="active")
    assert folder_targets([added], departments_enabled=False) == {A: "active"}


def test_a_person_who_lost_the_account_is_gone() -> None:
    lost = candidate(has_account=False, state="active")
    assert folder_targets([lost], departments_enabled=False) == {A: "gone"}


def test_a_department_follows_the_departments_switch() -> None:
    department = candidate("department", has_account=False)
    assert folder_targets([department], departments_enabled=True) == {A: "active"}
    assert folder_targets([department], departments_enabled=False) == {A: "paused"}


def test_an_archived_person_is_gone() -> None:
    archived = candidate(archived=True, state="active")
    assert folder_targets([archived], departments_enabled=True) == {A: "gone"}
    # Without a folder there is nothing to stop.
    assert folder_targets([candidate(archived=True)], departments_enabled=True) == {}


def test_stopped_stays_stopped() -> None:
    stopped = candidate(state="stopped")
    assert folder_targets([stopped], departments_enabled=True) == {A: "stopped"}


def test_subfolders_match_ignoring_case_and_skip_reserved() -> None:
    states, unknown = classify_subfolders(
        ["Anne", "@eaDir", "mislukt", "Oude scans", ".DS_Store", "#recycle"],
        {"anne": "active"},
    )
    assert states == {"Anne": "active"}
    assert unknown == ["Oude scans"]


# Ids


def test_scan_client_id() -> None:
    workspace = uuid.uuid4()
    first = scan_client_id(workspace, ("kantoor", "brief.pdf"), "ab" * 32, 10)
    assert first == scan_client_id(workspace, ("kantoor", "brief.pdf"), "ab" * 32, 10)
    assert first != scan_client_id(workspace, ("kantoor", "brief.pdf"), "ab" * 32, 11)
    assert first != scan_client_id(uuid.uuid4(), ("kantoor", "brief.pdf"), "ab" * 32, 10)
    assert first.version == 5
