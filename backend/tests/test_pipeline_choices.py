import uuid

from apps.pipeline.choices import (
    MAX_LABELS,
    MAX_PERSONS,
    Choice,
    ExtractionRequest,
    aliased,
    choices_block,
)

JAN = uuid.uuid4()
EMMA = uuid.uuid4()


def test_choices_block_format() -> None:
    request = ExtractionRequest(
        text="brief",
        persons=(
            Choice("P1", JAN, "Jan de Vries", ("J. de Vries",)),
            Choice("P2", EMMA, "Emma"),
        ),
        labels=(Choice("L1", uuid.uuid4(), "Auto"), Choice("L2", uuid.uuid4(), "Zorg")),
    )

    assert choices_block(request) == (
        "Personen (op naam van): P1 = Jan de Vries (ook: J. de Vries); P2 = Emma\n"
        "Labels: L1 = Auto; L2 = Zorg"
    )


def test_choices_block_joins_several_variants() -> None:
    person = Choice("P1", JAN, "Jan de Vries", ("J. de Vries", "Jan"))
    request = ExtractionRequest(text="brief", persons=(person,))

    assert choices_block(request) == (
        "Personen (op naam van): P1 = Jan de Vries (ook: J. de Vries, Jan)"
    )


def test_choices_block_leaves_out_an_empty_list() -> None:
    labels_only = ExtractionRequest(text="brief", labels=(Choice("L1", uuid.uuid4(), "Auto"),))

    assert choices_block(labels_only) == "Labels: L1 = Auto"
    assert choices_block(ExtractionRequest(text="brief")) == ""


def test_aliased_sorts_by_name_and_numbers_from_one() -> None:
    auto, zorg = uuid.uuid4(), uuid.uuid4()

    persons, labels = aliased(
        [(JAN, "Jan de Vries", ["J. de Vries"]), (EMMA, "emma de Vries", [])],
        [(zorg, "Zorg"), (auto, "Auto")],
    )

    assert persons == (
        Choice("P1", EMMA, "emma de Vries", ()),
        Choice("P2", JAN, "Jan de Vries", ("J. de Vries",)),
    )
    assert labels == (Choice("L1", auto, "Auto"), Choice("L2", zorg, "Zorg"))


def test_aliased_cuts_at_the_maxima() -> None:
    persons, labels = aliased(
        [(uuid.uuid4(), f"Persoon {n:03}", []) for n in range(MAX_PERSONS + 5)],
        [(uuid.uuid4(), f"Label {n:03}") for n in range(MAX_LABELS + 5)],
    )

    assert (MAX_PERSONS, MAX_LABELS) == (50, 100)
    assert len(persons) == MAX_PERSONS
    assert len(labels) == MAX_LABELS
    assert persons[-1].alias == "P50"
    assert persons[-1].name == "Persoon 049"
    assert labels[-1].alias == "L100"
