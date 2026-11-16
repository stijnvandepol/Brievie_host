"""The workspace's own lists the AI may choose from (spec section 10.1).

Per request the persons get the aliases P1..Pn and the labels L1..Ln: the model
copies a short alias more reliably than a UUID, and the real ids never leave the
server.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

MAX_PERSONS = 50
MAX_LABELS = 100


@dataclass(frozen=True)
class Choice:
    alias: str
    target_id: UUID
    name: str
    also: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtractionRequest:
    text: str
    persons: tuple[Choice, ...] = ()
    labels: tuple[Choice, ...] = ()


def aliased(
    persons: Sequence[tuple[UUID, str, Sequence[str]]],
    labels: Sequence[tuple[UUID, str]],
) -> tuple[tuple[Choice, ...], tuple[Choice, ...]]:
    """The lists sorted by name, cut at the maxima, with their aliases."""
    sorted_persons = sorted(persons, key=lambda person: (person[1].casefold(), str(person[0])))
    sorted_labels = sorted(labels, key=lambda label: (label[1].casefold(), str(label[0])))
    return (
        tuple(
            Choice(f"P{number}", target_id, name, tuple(also))
            for number, (target_id, name, also) in enumerate(sorted_persons[:MAX_PERSONS], 1)
        ),
        tuple(
            Choice(f"L{number}", target_id, name)
            for number, (target_id, name) in enumerate(sorted_labels[:MAX_LABELS], 1)
        ),
    )


def choices_block(request: ExtractionRequest) -> str:
    """The lines under the letter; a line is left out when its list is empty."""
    lines = []
    if request.persons:
        lines.append("Personen (op naam van): " + "; ".join(map(_person, request.persons)))
    if request.labels:
        lines.append("Labels: " + "; ".join(f"{c.alias} = {c.name}" for c in request.labels))
    return "\n".join(lines)


def _person(choice: Choice) -> str:
    also = f" (ook: {', '.join(choice.also)})" if choice.also else ""
    return f"{choice.alias} = {choice.name}{also}"
