"""The pure rule of "Blijft liggen" (navigation plan Task 5): calendar days in the
Netherlands, and who sees it."""

from datetime import UTC, datetime, timedelta

import pytest

from apps.documents.lingering import age_days, applies, cutoff


def test_age_days_counts_amsterdam_calendar_days() -> None:
    # Monday 23:30 in Amsterdam (21:30 UTC in summer) is one day old on Tuesday 00:10.
    since = datetime(2026, 6, 1, 21, 30, tzinfo=UTC)
    assert age_days(since, datetime(2026, 6, 1, 22, 10, tzinfo=UTC)) == 1
    assert age_days(since, datetime(2026, 6, 1, 21, 50, tzinfo=UTC)) == 0
    # Winter: UTC+1.
    assert (
        age_days(datetime(2026, 1, 5, 22, 30, tzinfo=UTC), datetime(2026, 1, 7, 9, tzinfo=UTC)) == 2
    )


def test_cutoff_is_the_start_of_the_youngest_lingering_day() -> None:
    now = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
    edge = cutoff(2, now)
    # Everything before it is two or more days old; at it, one.
    assert age_days(edge, now) == 1
    assert age_days(edge - timedelta(microseconds=1), now) == 2
    assert age_days(datetime(2026, 11, 18, 22, 59, tzinfo=UTC), now) == 2
    assert datetime(2026, 11, 18, 22, 59, tzinfo=UTC) < edge
    assert datetime(2026, 11, 18, 23, 0, tzinfo=UTC) == edge  # midnight in Amsterdam


@pytest.mark.parametrize(
    ("kind", "enabled", "sorts", "role", "expected"),
    [
        ("business", True, False, "admin", True),
        ("business", True, True, "member", True),
        ("business", True, False, "member", False),
        ("business", True, False, "viewer", False),
        ("business", False, True, "admin", False),
        ("home", True, True, "admin", False),
    ],
)
def test_applies(kind: str, enabled: bool, sorts: bool, role: str, expected: bool) -> None:
    assert applies(kind=kind, enabled=enabled, sorts_post=sorts, role=role) is expected
