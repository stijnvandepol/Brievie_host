"""The pure decision to confirm a letter on its own: one test per guard rail."""

import copy
from collections.abc import Callable
from datetime import date

import pytest

from apps.documents.models import Document
from apps.documents.schemas import ConfirmIn
from apps.pipeline.autopilot import decide_confirmation, mentions_term
from apps.pipeline.schema import ExtractionPayload
from apps.pipeline.validation import validate
from apps.tasks.models import Task

TODAY = date(2026, 11, 14)
TEXT = (
    "Belastingdienst\nDatum 3 november 2026\n"
    "Uw aanslag is vastgesteld. U moet betalen vóór 1 december 2026."
)
BASE: dict[str, object] = {
    "afzender": {"naam": "Belastingdienst", "bron": "Belastingdienst", "zekerheid": 0.95},
    "documenttype": "aanslag",
    "dagtekening": {"waarde": "2026-11-03", "bron": "Datum 3 november 2026", "zekerheid": 0.95},
    "samenvatting_b1": "Je moet de aanslag betalen.",
    "acties": [
        {
            "type": "betalen",
            "omschrijving": "Betaal de aanslag",
            "deadline": {
                "waarde": "2026-12-01",
                "bron": "vóór 1 december 2026",
                "zekerheid": 0.95,
            },
        }
    ],
    "factuur": None,
}
NO_DEADLINE = {"waarde": None, "bron": None, "zekerheid": 0.0}


Change = Callable[[dict[str, object]], None]


def build(change: Change | None = None, *, text: str = TEXT) -> ConfirmIn | None:
    """Decide on the baseline after `change(raw)` altered a copy of it."""
    raw = copy.deepcopy(BASE)
    if change is not None:
        change(raw)
    payload = ExtractionPayload.model_validate(raw)
    values = validate(payload, text, today=TODAY)
    return decide_confirmation(payload=payload, values=values, text=text, today=TODAY)


def action(raw: dict[str, object], index: int = 0) -> dict[str, object]:
    actions = raw["acties"]
    assert isinstance(actions, list)
    found = actions[index]
    assert isinstance(found, dict)
    return found


def deadline_of(raw: dict[str, object]) -> dict[str, object]:
    found = action(raw)["deadline"]
    assert isinstance(found, dict)
    return found


def set_(key: str, value: object) -> Change:
    def change(raw: dict[str, object]) -> None:
        raw[key] = value

    return change


def test_the_baseline_confirms_with_what_the_letter_says() -> None:
    data = build()

    assert data is not None
    assert (data.sender, data.document_type, data.letter_date) == (
        "Belastingdienst",
        Document.Type.ASSESSMENT,
        date(2026, 11, 3),
    )
    assert (data.summary, data.summary_by_ai, data.sets_organisation) == (
        "Je moet de aanslag betalen.",
        True,
        False,
    )
    (task,) = data.tasks
    assert (task.type, task.description, task.deadline) == (
        Task.Type.PAY,
        "Betaal de aanslag",
        date(2026, 12, 1),
    )
    assert (task.deadline_source, task.deadline_quote) == ("in_document", "vóór 1 december 2026")


def test_a_missing_summary_becomes_empty_text() -> None:
    data = build(set_("samenvatting_b1", ""))
    assert data is not None
    assert (data.summary, data.summary_by_ai) == ("", True)


def test_a_missing_sender_stays_a_proposal() -> None:
    assert build(set_("afzender", {"naam": None, "bron": None, "zekerheid": 0.0})) is None


def test_a_sender_just_under_the_threshold_stays_a_proposal() -> None:
    sender = {"naam": "Belastingdienst", "bron": "Belastingdienst", "zekerheid": 0.849}
    assert build(set_("afzender", sender)) is None


def test_a_sender_at_the_threshold_is_enough() -> None:
    sender = {"naam": "Belastingdienst", "bron": "Belastingdienst", "zekerheid": 0.85}
    assert build(set_("afzender", sender)) is not None


def test_a_sender_that_is_not_in_the_letter_stays_a_proposal() -> None:
    sender = {"naam": "Gemeente Utrecht", "bron": "Gemeente Utrecht", "zekerheid": 0.99}
    assert build(set_("afzender", sender)) is None


def test_a_missing_letter_date_stays_a_proposal() -> None:
    assert build(set_("dagtekening", {"waarde": None, "bron": None, "zekerheid": 0.0})) is None


def test_a_letter_date_just_under_the_threshold_stays_a_proposal() -> None:
    dated = {"waarde": "2026-11-03", "bron": "Datum 3 november 2026", "zekerheid": 0.849}
    assert build(set_("dagtekening", dated)) is None


def test_a_letter_date_whose_quote_has_no_day_stays_a_proposal() -> None:
    # The real validation gives it confidence 0: "november 2026" does not say the 3rd.
    text = TEXT.replace("Datum 3 november 2026", "Datum november 2026")
    dated = {"waarde": "2026-11-03", "bron": "Datum november 2026", "zekerheid": 0.99}

    assert build(set_("dagtekening", dated), text=text) is None


def test_an_unknown_document_type_word_stays_a_proposal() -> None:
    assert build(set_("documenttype", "zomaar iets")) is None


def test_a_document_type_is_read_without_case_or_spaces() -> None:
    assert build(set_("documenttype", " Aanslag ")) is not None


def test_an_unknown_action_type_word_stays_a_proposal() -> None:
    assert build(lambda raw: action(raw).update(type="doneren")) is None


def test_a_blank_description_stays_a_proposal() -> None:
    assert build(lambda raw: action(raw).update(omschrijving="   ")) is None


def test_twenty_actions_pass_and_twenty_one_do_not() -> None:
    def many(count: int) -> Change:
        def change(raw: dict[str, object]) -> None:
            raw["acties"] = [copy.deepcopy(action(raw)) for _ in range(count)]

        return change

    assert build(many(20)) is not None
    assert build(many(21)) is None


def test_a_deadline_that_is_not_in_the_letter_stays_a_proposal() -> None:
    assert build(lambda raw: deadline_of(raw).update(bron="vóór 2 december 2026")) is None


def test_a_deadline_just_under_the_threshold_stays_a_proposal() -> None:
    assert build(lambda raw: deadline_of(raw).update(zekerheid=0.849)) is None


def test_a_deadline_in_the_past_stays_a_proposal() -> None:
    text = TEXT.replace("1 december 2026", "13 november 2026")

    def change(raw: dict[str, object]) -> None:
        deadline_of(raw).update(waarde="2026-11-13", bron="vóór 13 november 2026")

    assert build(change, text=text) is None


def test_a_deadline_today_is_still_fine() -> None:
    text = TEXT.replace("1 december 2026", "14 november 2026")

    def change(raw: dict[str, object]) -> None:
        deadline_of(raw).update(waarde="2026-11-14", bron="vóór 14 november 2026")

    assert build(change, text=text) is not None


def test_a_deadline_before_the_letter_date_stays_a_proposal() -> None:
    text = TEXT.replace("Datum 3 november 2026", "Datum 20 november 2026").replace(
        "1 december 2026", "18 november 2026"
    )

    def change(raw: dict[str, object]) -> None:
        raw["dagtekening"] = {
            "waarde": "2026-11-20",
            "bron": "Datum 20 november 2026",
            "zekerheid": 0.95,
        }
        deadline_of(raw).update(waarde="2026-11-18", bron="vóór 18 november 2026")

    # The letter date is "tomorrow" at most 30 days ahead, so it is plausible.
    assert build(change, text=text) is None


def test_a_deadline_value_that_does_not_parse_stays_a_proposal() -> None:
    assert build(lambda raw: deadline_of(raw).update(waarde="1 december")) is None


def test_an_action_without_a_deadline_is_confirmed_as_manual() -> None:
    def change(raw: dict[str, object]) -> None:
        action(raw)["deadline"] = dict(NO_DEADLINE)

    text = "Belastingdienst\nDatum 3 november 2026\nUw aanslag is vastgesteld."
    data = build(change, text=text)

    assert data is not None
    (task,) = data.tasks
    assert (task.deadline, task.deadline_source, task.deadline_quote) == (None, "manual", "")


def test_an_action_without_a_deadline_while_the_letter_names_a_term_stays_a_proposal() -> None:
    def change(raw: dict[str, object]) -> None:
        action(raw)["deadline"] = dict(NO_DEADLINE)

    text = "Belastingdienst\nDatum 3 november 2026\nBetaal binnen 14 dagen na dagtekening."
    assert build(change, text=text) is None


def test_zero_actions_pass_even_when_the_letter_names_a_term() -> None:
    text = "Belastingdienst\nDatum 3 november 2026\nDit is ter informatie, uiterlijk bewaren."
    data = build(set_("acties", []), text=text)

    assert data is not None
    assert data.tasks == []


@pytest.mark.parametrize(
    "phrase",
    [
        "binnen 14 dagen",
        "Binnen twee weken",
        "binnen 3 werkdagen betalen",
        "binnen eenentwintig dagen",
        "binnen dertig dagen",
        "binnen 1 maand",
        "uiterlijk 1 december 2026",
        "Uiterlijk vrijdag",
        "vóór 01-12-2026",
        "voor 1 december",
        "voor 2026-12-01",
        "voor 1/12/2026",
        "de vervaldatum",
        "betaaltermijn van de factuur",
        "de termijn is verstreken",
        "DEADLINE",
        "ten laatste op vrijdag",
    ],
)
def test_mentions_term_finds_a_term(phrase: str) -> None:
    assert mentions_term(phrase) is True


@pytest.mark.parametrize(
    "phrase",
    [
        "voor u",
        "voor meer informatie",
        "binnenkort",
        "binnen de gemeente",
        "voor 5 personen",
        "binnen 14 uur",
        "",
    ],
)
def test_mentions_term_ignores_ordinary_words(phrase: str) -> None:
    assert mentions_term(phrase) is False
