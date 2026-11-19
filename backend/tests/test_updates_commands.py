"""The management commands of ./brievie update."""

import io
import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


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
