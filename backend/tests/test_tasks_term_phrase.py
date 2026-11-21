"""The words of a term without a date, for a task that needs one (spec S 6 item 7, S 12,
plan Task 2). Pure: no database is touched."""

import json
import time
from pathlib import Path

import pytest

from apps.pipeline.autopilot import TERM_PHRASE_MAX, mentions_term, term_phrase

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "U kunt binnen zes weken na dagtekening bezwaar maken.",
            "binnen zes weken na dagtekening bezwaar maken",
        ),
        ("Betaal uiterlijk 14 dagen na ontvangst.", "uiterlijk 14 dagen na ontvangst"),
        ("Betalen vóór: 01-12-2026. Daarna volgt een herinnering.", "vóór: 01-12-2026"),
        (
            "Zie   de\nbijlage. U heeft tot 1 december om te reageren!",
            "tot 1 december om te reageren",
        ),
        ("Geen datum hier.", ""),
        ("", ""),
    ],
)
def test_term_phrase(text: str, expected: str) -> None:
    assert term_phrase(text) == expected


def test_term_phrase_is_at_most_200_characters() -> None:
    text = "Maak binnen zes weken " + "bezwaar " * 100 + "."
    phrase = term_phrase(text)
    assert phrase.startswith("binnen zes weken")
    assert len(phrase) <= TERM_PHRASE_MAX
    assert phrase.endswith("bezwaar")  # cut on a word boundary


def test_term_phrase_masks_a_bsn() -> None:
    phrase = term_phrase("Reageer binnen 6 weken, BSN 111222333, op deze brief.")
    assert "111222333" not in phrase
    assert "[BSN]" in phrase


@pytest.mark.parametrize(
    "text",
    [
        "binnen 14 kalenderdagen",
        "30 dagen netto",
        "vóór 1 december",
        "within 14 days",
        "voor meer informatie",
        "binnenkort",
        "voor 1.500 euro",
        "tot slot",
    ],
)
def test_term_phrase_agrees_with_mentions_term(text: str) -> None:
    assert bool(term_phrase(text)) == mentions_term(text)


def test_term_phrase_agrees_on_the_trial_letters() -> None:
    letters: dict[str, str] = json.loads((FIXTURES / "trial_letters.json").read_text())
    for number, text in letters.items():
        if number.startswith("_"):
            continue
        assert bool(term_phrase(text)) == mentions_term(text), number


def _seconds(text: str) -> float:
    start = time.perf_counter()
    term_phrase(text)
    return time.perf_counter() - start


def test_term_phrase_linear_time() -> None:
    """1 MB of text that almost matches costs about four times 256 KB, not sixteen; and
    never much more than `mentions_term` itself on the same text."""
    for unit in ("1.", "binnen ", "14 (", "a "):
        small = unit * (256_000 // len(unit))
        large = unit * (1_024_000 // len(unit))
        assert _seconds(large) < 8 * max(_seconds(small), 0.01), unit
        start = time.perf_counter()
        mentions_term(large)
        base = time.perf_counter() - start
        assert _seconds(large) < 3 * base + 0.2, unit
