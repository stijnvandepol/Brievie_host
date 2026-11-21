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


def test_zero_actions_stay_a_proposal_when_the_letter_names_a_term() -> None:
    # A letter can say "no actions" and still hold a real term further on: a person looks.
    text = (
        "Belastingdienst\nDatum 3 november 2026\nDeze brief is alleen ter informatie. "
        "Tegen dit besluit kunt u binnen zes weken bezwaar maken."
    )
    assert build(set_("acties", []), text=text) is None


def test_zero_actions_confirm_when_the_letter_names_no_term() -> None:
    text = "Belastingdienst\nDatum 3 november 2026\nDeze brief is alleen ter informatie."
    data = build(set_("acties", []), text=text)

    assert data is not None
    assert data.tasks == []


def test_every_action_with_a_deadline_passes_while_the_letter_names_a_term() -> None:
    def change(raw: dict[str, object]) -> None:
        second = copy.deepcopy(action(raw))
        second.update(type="reageren", omschrijving="Stuur het formulier terug")
        raw["acties"] = [action(raw), second]

    assert build(change) is not None


def test_one_action_without_a_deadline_among_dated_ones_stays_a_proposal() -> None:
    def change(raw: dict[str, object]) -> None:
        second = {
            "type": "reageren",
            "omschrijving": "Stuur het formulier terug",
            "deadline": dict(NO_DEADLINE),
        }
        raw["acties"] = [action(raw), second]

    assert build(change) is None


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
        "binnen één week",
        "binnen eén week",
        "binnen 1 week",
        "binnen 14 kalenderdagen",
        "binnen een jaar",
        "binnen veertig dagen",
        "binnen zes weken na dagtekening",
        "binnen tweeëndertig dagen",
        "niet later dan 1 december 2026",
        "tot en met 1 december",
        "t/m 1 december",
        "vóór: 01-12-2026",
        "Betalen vóór: 01-12-2026",
        "voor vrijdag 1 december",
        "voor: maandag 01-12-2026",
        "uiterste betaaldatum",
        "betaaldatum 01-12-2026",
        "30 dagen netto",
        "Betalingsconditie: 30 dagen netto",
        "veertien dagen na factuurdatum",
        "uiterlijk op 1 december",
        "vo\u0301o\u0301r 1 december",  # decomposed "vóór"
        "termijnbedrag",
        "binnen veertien (14) dagen",
        "binnen 14 (veertien) dagen",
        "binnen 2 (twee) weken",
        "zes (6) weken",
        "U kunt tot zes weken na de dagtekening bezwaar maken",
        "Bezwaar: 6 weken na verzending",
        "binnen 14dagen",
        "vóór de vervaldag",
        "op de vervaldag",
        "vóór 1-12",
        "betalen voor 01-12-26",
        "u heeft tot 1 december",
        "u heeft tot 1 december 2026",
        "within 14 days",
        "Within thirty days",
        "the due date",
        "no later than Friday",
        "before 1 December 2026",
        "before December 1, 2026",
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
        "tot ziens",
        "voor 2026 geldt",
        "voor vrijdag",
        "30 dagen geleden",
        "voor 1.500 euro",
        "voor 12,50 euro",
        "tot slot",
        "before you",
        "",
    ],
)
def test_mentions_term_ignores_ordinary_words(phrase: str) -> None:
    assert mentions_term(phrase) is False


NO_TERM_TEXT = (
    "Belastingdienst\nDatum 3 november 2026\nWij sturen u deze aanslag. Bedrag 01-12-2026 euro."
)


def without_deadline(raw: dict[str, object]) -> None:
    action(raw)["deadline"] = dict(NO_DEADLINE)


def with_invoice_due(raw: dict[str, object]) -> None:
    raw["factuur"] = {
        "nummer": None,
        "vervaldatum": "2026-12-01",
        "bedrag_incl": None,
        "bron": "Bedrag 01-12-2026 euro",
        "zekerheid": 0.95,
    }


def test_a_pay_action_without_a_deadline_confirms_when_the_letter_has_no_due_date() -> None:
    assert build(without_deadline, text=NO_TERM_TEXT) is not None


def test_a_pay_action_without_a_deadline_stays_a_proposal_when_an_invoice_due_date_exists() -> None:
    def change(raw: dict[str, object]) -> None:
        without_deadline(raw)
        with_invoice_due(raw)

    assert build(change, text=NO_TERM_TEXT) is None


def test_an_invoice_due_date_with_only_an_action_that_is_not_a_payment_stays_a_proposal() -> None:
    def change(raw: dict[str, object]) -> None:
        without_deadline(raw)
        with_invoice_due(raw)
        action(raw).update(type="reageren")

    assert build(change, text=NO_TERM_TEXT) is None


def test_an_invoice_due_date_without_any_action_stays_a_proposal() -> None:
    def change(raw: dict[str, object]) -> None:
        with_invoice_due(raw)
        raw["acties"] = []

    assert build(change, text=NO_TERM_TEXT) is None


def test_an_invoice_due_date_with_a_dated_payment_and_a_dated_other_action_passes() -> None:
    def change(raw: dict[str, object]) -> None:
        with_invoice_due(raw)
        second = copy.deepcopy(action(raw))
        second.update(type="reageren", omschrijving="Stuur het formulier terug")
        raw["acties"] = [action(raw), second]

    assert build(change) is not None


def test_an_invoice_due_date_with_a_second_payment_without_a_deadline_stays_a_proposal() -> None:
    def change(raw: dict[str, object]) -> None:
        with_invoice_due(raw)
        second = {
            "type": "betalen",
            "omschrijving": "Betaal de rente",
            "deadline": dict(NO_DEADLINE),
        }
        raw["acties"] = [action(raw), second]

    assert build(change, text=NO_TERM_TEXT) is None


def test_an_invoice_due_date_does_not_block_a_pay_action_that_has_a_deadline() -> None:
    def change(raw: dict[str, object]) -> None:
        with_invoice_due(raw)

    assert build(change) is not None


def test_an_invoice_due_date_of_zero_confidence_still_blocks() -> None:
    def change(raw: dict[str, object]) -> None:
        without_deadline(raw)
        with_invoice_due(raw)
        raw["factuur"]["bron"] = "staat er niet"  # type: ignore[index]

    assert build(change, text=NO_TERM_TEXT) is None


def test_a_letter_dated_tomorrow_stays_a_proposal() -> None:
    text = TEXT.replace("Datum 3 november 2026", "Datum 15 november 2026")

    def change(raw: dict[str, object]) -> None:
        raw["dagtekening"] = {
            "waarde": "2026-11-15",
            "bron": "Datum 15 november 2026",
            "zekerheid": 0.95,
        }

    assert build(change, text=text) is None


def test_a_letter_dated_today_is_fine() -> None:
    text = TEXT.replace("Datum 3 november 2026", "Datum 14 november 2026")

    def change(raw: dict[str, object]) -> None:
        raw["dagtekening"] = {
            "waarde": "2026-11-14",
            "bron": "Datum 14 november 2026",
            "zekerheid": 0.95,
        }

    assert build(change, text=text) is not None


def test_a_sender_over_the_limit_stays_a_proposal() -> None:
    name = "B" * 201
    sender = {"naam": name, "bron": name, "zekerheid": 0.95}
    assert build(set_("afzender", sender), text=TEXT + "\n" + name) is None


def test_a_sender_at_the_limit_is_fine() -> None:
    name = "B" * 200
    sender = {"naam": name, "bron": name, "zekerheid": 0.95}
    assert build(set_("afzender", sender), text=TEXT + "\n" + name) is not None


def test_a_summary_over_the_limit_stays_a_proposal() -> None:
    assert build(set_("samenvatting_b1", "s" * 5001)) is None
    assert build(set_("samenvatting_b1", "s" * 5000)) is not None


def test_a_description_over_the_limit_stays_a_proposal() -> None:
    assert build(lambda raw: action(raw).update(omschrijving="d" * 501)) is None
    assert build(lambda raw: action(raw).update(omschrijving="d" * 500)) is not None


@pytest.mark.parametrize(
    "text",
    [
        "binnen " * 100_000,
        "1 " * 100_000,
        "1" * 200_000,
        "binnen 14" + " " * 200_000 + "x",
        "veertien (" * 50_000,
        "1 (1 (" * 50_000,
        "voor 1-" * 50_000,
        "within " * 100_000,
        "before 1 " * 50_000,
    ],
)
def test_mentions_term_stays_fast_on_long_text(text: str) -> None:
    import time

    started = time.monotonic()
    mentions_term(text)
    assert time.monotonic() - started < 1.0


def test_an_english_summary_changes_no_guard_rail() -> None:
    # summary_language "en": the summary and descriptions are English, the letter stays
    # Dutch. mentions_term and the deadline rules read the letter, never the summary.
    english = {"samenvatting_b1": "You have to pay the tax assessment."}

    def in_english(raw: dict[str, object]) -> None:
        raw.update(english)
        action(raw)["omschrijving"] = "Pay the tax assessment"

    def in_english_without_deadline(raw: dict[str, object]) -> None:
        in_english(raw)
        action(raw)["deadline"] = dict(NO_DEADLINE)

    data = build(in_english)
    assert data is not None
    assert data.summary == "You have to pay the tax assessment."
    assert [task.description for task in data.tasks] == ["Pay the tax assessment"]
    # The Dutch letter names a term ("vóór 1 december"): without a date, no confirm.
    assert mentions_term(TEXT)
    assert build(in_english_without_deadline) is None
