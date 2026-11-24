"""The start gate of a compose install: pure, no database."""

from dataclasses import replace
from pathlib import Path

from apps.updates import gate
from apps.updates.gate import COMPOSE_REVISION, GateDecision, decide_start
from apps.updates.release import Decision, Release, Version, refusal_message

ROOT = Path(__file__).resolve().parents[2]


def v(text: str) -> Version:
    return Version.parse(text)


INSTALLED = v("0.4.0")
RELEASE = Release(v("0.5.0"), v("0.3.0"), 18)


def decide(
    *,
    installed: Version | None = INSTALLED,
    database_major: int | None = 18,
    unknown_migrations: bool = False,
    compose_revision: int = COMPOSE_REVISION,
    image_compose_revision: int = COMPOSE_REVISION,
    adopt_version: Version | None = None,
    has_data: bool = True,
    free_bytes: int = 10,
) -> GateDecision:
    return decide_start(
        image=v("0.5.0"),
        installed=installed,
        release=RELEASE,
        database_major=database_major,
        unknown_migrations=unknown_migrations,
        compose_revision=compose_revision,
        image_compose_revision=image_compose_revision,
        adopt_version=adopt_version,
        has_data=has_data,
        free_bytes=free_bytes,
        needed_bytes=10,
    )


def test_update_within_the_jump_is_allowed_with_space_exactly_equal() -> None:
    assert decide() == GateDecision("update")


def test_equal_versions_are_the_same() -> None:
    assert decide(installed=v("0.5.0")).outcome == "same"


def test_lower_image_refuses_as_downgrade() -> None:
    assert decide(installed=v("0.6.0")) == GateDecision("refused", "downgrade")


def test_unknown_migrations_refuse_even_when_versions_are_equal() -> None:
    result = decide(installed=v("0.5.0"), unknown_migrations=True)
    assert result == GateDecision("refused", "unknown_migrations")


def test_min_upgrade_from_boundary() -> None:
    assert decide(installed=v("0.3.0")).outcome == "update"
    result = decide(installed=v("0.2.9"))
    assert result == GateDecision("refused", "jump_too_large", v("0.3.0"))


def test_postgres_major_differs() -> None:
    assert decide(database_major=17) == GateDecision("refused", "postgres_major_differs")
    assert decide(database_major=None).outcome == "update"


def test_compose_revision_both_directions() -> None:
    old = decide(compose_revision=1, image_compose_revision=2)
    new = decide(compose_revision=2, image_compose_revision=1)
    assert old == GateDecision("refused", "compose_too_old")
    assert new == GateDecision("refused", "compose_too_new")


def test_space_one_below_the_need_refuses_only_for_an_update() -> None:
    assert decide(free_bytes=9) == GateDecision("refused", "no_space")
    assert decide(installed=v("0.5.0"), free_bytes=0).outcome == "same"


def test_first_refusing_rule_wins() -> None:
    result = decide(installed=v("0.2.0"), database_major=17, compose_revision=2, free_bytes=0)
    assert result.code == "jump_too_large"
    result = decide(database_major=17, compose_revision=2, free_bytes=0)
    assert result.code == "postgres_major_differs"
    result = decide(compose_revision=2, free_bytes=0)
    assert result.code == "compose_too_new"
    assert decide(installed=v("0.6.0"), unknown_migrations=True).code == "unknown_migrations"


def test_no_data_and_no_installed_version_is_fresh() -> None:
    assert decide(installed=None, has_data=False, database_major=None) == GateDecision("fresh")


def test_data_without_an_installed_version_needs_the_adopt_version() -> None:
    assert decide(installed=None).code == "adopt_needed"
    assert decide(installed=None, adopt_version=v("0.4.0")).code == "adopt_differs"
    assert decide(installed=None, adopt_version=v("0.5.0")).outcome == "same"


def test_every_refusal_has_a_dutch_sentence_that_ends_in_nothing_changed() -> None:
    codes = [
        "downgrade",
        "unknown_migrations",
        "jump_too_large",
        "postgres_major_differs",
        "compose_too_old",
        "compose_too_new",
        "no_space",
        "adopt_needed",
        "adopt_differs",
    ]
    for code in codes:
        text = refusal_message(
            Decision("refused", code, go_first=v("0.3.0")),
            installed=v("0.2.0"),
            target=v("0.5.0"),
            postgres_major=18,
        )
        assert text.endswith("Er is niets veranderd."), code
        assert "{" not in text, code


def test_gate_is_pure() -> None:
    source = (ROOT / "backend/apps/updates/gate.py").read_text()
    assert "import django" not in source
    assert "from django" not in source
    assert replace(RELEASE) == RELEASE


def test_dockerfile_carries_the_release_and_the_revision() -> None:
    text = (ROOT / "deploy/Dockerfile").read_text()
    assert "COPY deploy/release.json /app/release.json" in text
    # No BRIEVIE_COMPOSE_* variable in the image: the image knows its revision from the code
    # (gate.COMPOSE_REVISION), and only the standalone compose file says "compose install".
    assert "BRIEVIE_COMPOSE" not in text
    assert gate.COMPOSE_REVISION >= 1
