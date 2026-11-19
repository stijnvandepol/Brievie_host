"""The release rules of ./brievie update: pure functions, no database, no Django but text."""

import json
from pathlib import Path

import pytest

from apps.common.version import server_version
from apps.updates.release import (
    BUILD_RESERVE_BYTES,
    REASONS,
    Release,
    ReleaseEntry,
    ReleaseError,
    Version,
    decide,
    parse_release,
    parse_release_stream,
    refusal_message,
    space_needed,
)

ROOT = Path(__file__).resolve().parents[2]


def v(text: str) -> Version:
    return Version.parse(text)


def r(version: str, minimum: str, postgres_major: int = 18) -> Release:
    return Release(v(version), v(minimum), postgres_major)


def releases(*items: Release) -> dict[Version, ReleaseEntry]:
    return {item.version: item for item in items}


def release_text(**fields: object) -> str:
    data: dict[str, object] = {
        "format": 1,
        "version": "0.3.0",
        "min_upgrade_from": "0.2.0",
        "postgres_major": 18,
    }
    data.update(fields)
    return json.dumps({key: value for key, value in data.items() if value is not None})


def test_version_order_is_numeric() -> None:
    assert v("0.10.0") > v("0.9.9")
    assert v("1.0.0") > v("0.99.99")
    assert str(v("v1.2.3")) == "1.2.3"
    assert v("v1.2.3") == v("1.2.3")


@pytest.mark.parametrize(
    "text",
    [
        "latest",
        "0.3",
        "v0.3.0-rc.1",
        "0.3.0;rm -rf x",
        "../x",
        " 0.3.0",
        "0.3.0\n",
        "01.2.3",
        "",
        "V0.3.0",
        "0.3.0.0",
        "12345.0.0",
        "٣.0.0",
    ],
)
def test_malformed_versions_are_refused(text: str) -> None:
    with pytest.raises(ReleaseError) as caught:
        Version.parse(text)
    assert caught.value.code == "version_invalid"


def test_parse_release_accepts_the_committed_file() -> None:
    release = parse_release((ROOT / "deploy" / "release.json").read_text())
    assert release.version == v(server_version())
    assert release.min_upgrade_from <= release.version


@pytest.mark.parametrize(
    ("text", "code"),
    [
        (release_text(format=2), "release_format_unknown"),
        (release_text(format=True), "release_format_unknown"),
        (release_text(format="1"), "release_format_unknown"),
        (release_text(format=None), "release_invalid"),
        (release_text(postgres_major=None), "release_invalid"),
        (release_text(extra="x"), "release_invalid"),
        (release_text(min_upgrade_from="0.4.0"), "release_invalid"),
        (release_text(postgres_major="18"), "release_invalid"),
        (release_text(postgres_major=True), "release_invalid"),
        (release_text(postgres_major=9), "release_invalid"),
        (release_text(version="latest"), "release_invalid"),
        (release_text(version=3), "release_invalid"),
        ("[]", "release_invalid"),
        ("not json", "release_invalid"),
    ],
)
def test_parse_release_refuses(text: str, code: str) -> None:
    with pytest.raises(ReleaseError) as caught:
        parse_release(text)
    assert caught.value.code == code


def test_stream_reads_one_record_per_tag() -> None:
    stream = (
        "--- v0.3.0\n"
        + release_text()
        + "\n--- v0.4.0\n"
        + release_text(version="0.4.0", min_upgrade_from="0.3.0")
        + "\n"
    )
    parsed = parse_release_stream(stream)
    assert parsed == {v("0.3.0"): r("0.3.0", "0.2.0"), v("0.4.0"): r("0.4.0", "0.3.0")}


def test_stream_marks_a_mismatched_header() -> None:
    parsed = parse_release_stream("--- v0.3.0\n" + release_text(version="0.3.1") + "\n")
    entry = parsed[v("0.3.0")]
    assert isinstance(entry, ReleaseError)
    assert entry.code == "release_mismatch"


def test_stream_skips_a_malformed_header_and_its_record() -> None:
    stream = "--- vlatest\n" + release_text() + "\n--- v0.3.0\n" + release_text() + "\n"
    assert parse_release_stream(stream) == {v("0.3.0"): r("0.3.0", "0.2.0")}


def test_direct_jump_is_allowed() -> None:
    decision = decide(
        installed=v("0.2.0"),
        target=v("0.3.0"),
        releases=releases(r("0.3.0", "0.2.0")),
        postgres_major=18,
    )
    assert decision.outcome == "allowed"


def test_same_version_is_already() -> None:
    decision = decide(installed=v("0.3.0"), target=v("0.3.0"), releases={}, postgres_major=18)
    assert decision.outcome == "already"


def test_downgrade_is_refused() -> None:
    decision = decide(
        installed=v("0.3.0"),
        target=v("0.2.0"),
        releases=releases(r("0.2.0", "0.2.0")),
        postgres_major=18,
    )
    assert (decision.outcome, decision.code) == ("refused", "downgrade")


def test_before_versions_is_refused() -> None:
    decision = decide(
        installed=v("0.1.0"),
        target=v("0.3.0"),
        releases=releases(r("0.3.0", "0.1.0")),
        postgres_major=18,
    )
    assert (decision.outcome, decision.code) == ("refused", "before_versions")


def test_refused_jump_names_the_furthest_step() -> None:
    decision = decide(
        installed=v("0.2.0"),
        target=v("0.4.0"),
        releases=releases(r("0.3.0", "0.2.0"), r("0.3.1", "0.2.0"), r("0.4.0", "0.3.0")),
        postgres_major=18,
    )
    assert (decision.outcome, decision.code, decision.go_first) == (
        "refused",
        "go_first",
        v("0.3.1"),
    )


def test_a_step_with_another_postgres_major_is_not_named() -> None:
    decision = decide(
        installed=v("0.2.0"),
        target=v("0.4.0"),
        releases=releases(r("0.3.0", "0.2.0"), r("0.3.1", "0.2.0", 19), r("0.4.0", "0.3.0")),
        postgres_major=18,
    )
    assert decision.go_first == v("0.3.0")


def test_no_path_when_nothing_is_reachable() -> None:
    decision = decide(
        installed=v("0.2.0"),
        target=v("0.5.0"),
        releases=releases(r("0.5.0", "0.4.0")),
        postgres_major=18,
    )
    assert (decision.outcome, decision.code) == ("refused", "no_path")


def test_postgres_major_change_is_refused() -> None:
    decision = decide(
        installed=v("0.2.0"),
        target=v("0.3.0"),
        releases=releases(r("0.3.0", "0.2.0", 19)),
        postgres_major=18,
    )
    assert (decision.outcome, decision.code) == ("refused", "postgres_major_changed")


def test_missing_or_broken_target_release_is_refused() -> None:
    missing = decide(installed=v("0.2.0"), target=v("0.3.0"), releases={}, postgres_major=18)
    assert (missing.outcome, missing.code) == ("refused", "release_missing")
    broken = decide(
        installed=v("0.2.0"),
        target=v("0.3.0"),
        releases={v("0.3.0"): ReleaseError("release_format_unknown")},
        postgres_major=18,
    )
    assert (broken.outcome, broken.code) == ("refused", "release_format_unknown")


def test_space_needed() -> None:
    assert space_needed(documents_bytes=10, database_bytes=5, fallback=True) == (
        BUILD_RESERVE_BYTES + 15
    )
    assert space_needed(documents_bytes=10, database_bytes=5, fallback=False) == (
        BUILD_RESERVE_BYTES
    )


def test_every_refusal_has_a_dutch_sentence_that_says_nothing_changed() -> None:
    codes = [
        "before_versions",
        "downgrade",
        "release_missing",
        "release_invalid",
        "release_mismatch",
        "release_format_unknown",
        "postgres_major_changed",
        "go_first",
        "no_path",
    ]
    for code in codes:
        assert code in REASONS
        decision = decide(installed=v("0.2.0"), target=v("0.2.0"), releases={}, postgres_major=18)
        message = refusal_message(
            type(decision)(outcome="refused", code=code, go_first=v("0.3.1")),
            installed=v("0.2.0"),
            target=v("0.4.0"),
            postgres_major=18,
        )
        assert message.endswith("Er is niets veranderd."), code


def test_the_pinned_sentences() -> None:
    def message(code: str) -> str:
        decision = decide(installed=v("0.2.0"), target=v("0.2.0"), releases={}, postgres_major=18)
        return refusal_message(
            type(decision)(outcome="refused", code=code, go_first=v("0.3.1")),
            installed=v("0.2.0"),
            target=v("0.4.0"),
            postgres_major=18,
        )

    assert message("go_first").startswith(
        "Van versie 0.2.0 kun je niet in één keer naar 0.4.0. "
        "Werk eerst bij naar 0.3.1: ./brievie update 0.3.1"
    )
    assert "git checkout v0.4.0" in message("before_versions")
    assert "(18)" in message("postgres_major_changed")
    assert "CHANGELOG.md" in message("postgres_major_changed")
    assert "herstel" in message("downgrade")
