"""The duplicate measurement's corpus is one experiment: the same letters every run and on
every day. No database.

On 2026-11-25 the measurement read recall 0.930 where DECISIONS records 0.950 for the
same thresholds and the same code: the corpus was written as of the clock's day, so the
seed named another experiment each day. The digest below pins the corpus of `ANCHOR`; a
change to the corpus, the noise model or the sampling changes it, and then the measured
numbers in DECISIONS are measured again and this digest is renewed with them.
"""

import hashlib
from datetime import UTC, datetime

import pytest

from tests.perf import test_duplicates_measure as measure

# sha256 of the corpus of `measure.ANCHOR` (see `_digest`).
DIGEST = "c283b2399659055ba1e28405ccbf7dfa3b062a9c9d936ff5ac166b6eed498bbc"


def _digest(world: measure.World) -> str:
    h = hashlib.sha256()
    for letter, letter_id in zip(world.letters, world.ids, strict=True):
        h.update(f"{letter_id}\0{letter.meta.letter_date}\0{letter.text}\0".encode())
    for arrival in [*world.positives, *world.negatives]:
        h.update(f"{arrival.kind}\0{arrival.partner}\0{arrival.power}\0{arrival.text}\0".encode())
    return h.hexdigest()


def test_the_corpus_is_the_same_every_run() -> None:
    assert _digest(measure.sample()) == _digest(measure.sample())


def test_the_corpus_is_the_one_of_the_decision() -> None:
    assert datetime(2026, 11, 21, tzinfo=UTC) == measure.ANCHOR
    assert _digest(measure.sample()) == DIGEST


def test_another_day_is_another_experiment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Why the day is pinned: a letter's text holds its dates, and the noise draws one
    number per character, so the day the corpus is written as of changes every rescan."""
    monkeypatch.setattr(measure, "ANCHOR", datetime(2026, 11, 25, tzinfo=UTC))
    assert _digest(measure.sample()) != DIGEST
