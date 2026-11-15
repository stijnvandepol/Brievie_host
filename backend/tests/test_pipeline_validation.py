import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from apps.documents.models import needs_attention
from apps.pipeline.schema import ExtractionPayload, json_schema
from apps.pipeline.validation import (
    ValidatedValue,
    amounts_in,
    dates_in,
    quote_in_text,
    validate,
)

TEXT = (
    "Belastingdienst\nDatum 3 november 2026\nAanslagnummer 1234.56.789\n"
    "U moet € 1.234,56 betalen vóór 1 december 2026."
)
TODAY = date(2026, 11, 14)
SNAPSHOT = Path(__file__).parent / "fixtures" / "extraction_schema.json"


def payload(**changes: object) -> ExtractionPayload:
    data: dict[str, object] = {
        "afzender": {"naam": "Belastingdienst", "bron": "Belastingdienst", "zekerheid": 0.97},
        "documenttype": "aanslag",
        "dagtekening": {"waarde": "2026-11-03", "bron": "Datum 3 november 2026", "zekerheid": 0.9},
        "samenvatting_b1": "Je moet belasting betalen.",
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
    data.update(changes)
    return ExtractionPayload.model_validate(data)


def action(**deadline: object) -> list[dict[str, object]]:
    base = {"waarde": "2026-12-01", "bron": "vóór 1 december 2026", "zekerheid": 0.95}
    return [{"type": "betalen", "omschrijving": "Betaal", "deadline": {**base, **deadline}}]


def invoice(**changes: object) -> dict[str, object]:
    base = {
        "nummer": None,
        "vervaldatum": None,
        "bedrag_incl": "1234.56",
        "bron": "U moet € 1.234,56 betalen",
        "zekerheid": 0.9,
    }
    return {**base, **changes}


def value(values: list[ValidatedValue], field: str, group: int = 0) -> ValidatedValue:
    (found,) = [v for v in values if v.field == field and v.group == group]
    return found


def fields(values: list[ValidatedValue]) -> set[tuple[str, int]]:
    return {(v.field, v.group) for v in values}


def test_value_found_in_its_quote_keeps_the_confidence() -> None:
    values = validate(payload(), TEXT, today=TODAY)

    deadline = value(values, "action_deadline", 1)
    assert deadline.value == "2026-12-01"
    assert deadline.quote == "vóór 1 december 2026"
    assert deadline.confidence == Decimal("0.950")
    assert value(values, "sender").value == "Belastingdienst"
    assert value(values, "sender").confidence == Decimal("0.970")
    assert value(values, "letter_date").value == "2026-11-03"
    assert value(values, "letter_date").confidence == Decimal("0.900")


def test_quote_not_in_the_text_gets_zero() -> None:
    values = validate(payload(acties=action(bron="vóór 2 december 2026")), TEXT, today=TODAY)

    assert value(values, "action_deadline", 1).confidence == Decimal("0")


def test_value_not_in_its_quote_gets_zero() -> None:
    # Review Focus 3: the quote is real, the date is invented.
    values = validate(payload(acties=action(waarde="2026-12-02")), TEXT, today=TODAY)

    assert value(values, "action_deadline", 1).confidence == Decimal("0")
    assert value(values, "action_deadline", 1).value == "2026-12-02"


def test_sender_not_in_the_text_gets_zero() -> None:
    sender = {"naam": "CAK", "bron": "CAK", "zekerheid": 0.99}

    values = validate(payload(afzender=sender), TEXT, today=TODAY)

    assert value(values, "sender").confidence == Decimal("0")


def test_amount_in_dutch_format_matches() -> None:
    values = validate(payload(factuur=invoice()), TEXT, today=TODAY)

    amount = value(values, "invoice_amount_incl")
    assert amount.value == "1234.56"
    assert amount.confidence == Decimal("0.900")


def test_invented_amount_gets_zero() -> None:
    values = validate(payload(factuur=invoice(bedrag_incl="1500.00")), TEXT, today=TODAY)

    assert value(values, "invoice_amount_incl").confidence == Decimal("0")


def test_invoice_value_outside_its_quote_but_in_the_text_is_accepted() -> None:
    # The invoice has one quote for several values.
    data = invoice(vervaldatum="2026-12-01", nummer="1234.56.789")

    values = validate(payload(factuur=data), TEXT, today=TODAY)

    assert value(values, "invoice_due_date").confidence == Decimal("0.900")
    assert value(values, "invoice_number").confidence == Decimal("0.900")


def test_invoice_number_not_in_the_text_gets_zero() -> None:
    values = validate(payload(factuur=invoice(nummer="F-2026-001")), TEXT, today=TODAY)

    assert value(values, "invoice_number").confidence == Decimal("0")


def test_implausible_letter_date_gets_zero() -> None:
    text = "Datum 1 januari 1990"
    dated = {"waarde": "1990-01-01", "bron": "Datum 1 januari 1990", "zekerheid": 0.9}

    values = validate(payload(dagtekening=dated, acties=[]), text, today=TODAY)

    assert value(values, "letter_date").confidence == Decimal("0")


def test_deadline_far_in_the_past_gets_zero() -> None:
    text = TEXT + " Eerder: vóór 1 december 2020."
    acties = action(waarde="2020-12-01", bron="vóór 1 december 2020")

    values = validate(payload(acties=acties), text, today=TODAY)

    assert value(values, "action_deadline", 1).confidence == Decimal("0")


@pytest.mark.parametrize("bad", ["veel", "12,5,3", "", "NaN", "-5.00"])
def test_unparsable_amount_is_dropped(bad: str) -> None:
    values = validate(payload(factuur=invoice(bedrag_incl=bad)), TEXT, today=TODAY)

    assert ("invoice_amount_incl", 0) not in fields(values)


@pytest.mark.parametrize("bad", ["1 december", "2026-13-01", "01-12-2026", ""])
def test_unparsable_date_is_dropped(bad: str) -> None:
    values = validate(payload(acties=action(waarde=bad)), TEXT, today=TODAY)

    assert ("action_deadline", 1) not in fields(values)
    assert ("action_type", 1) in fields(values)


def test_action_without_deadline_has_no_deadline_value() -> None:
    acties = action(waarde=None, bron=None, zekerheid=0)

    values = validate(payload(acties=acties), TEXT, today=TODAY)

    assert fields(values) >= {("action_type", 1), ("action_description", 1)}
    assert ("action_deadline", 1) not in fields(values)


def test_generated_text_has_no_quote_and_a_fixed_confidence() -> None:
    values = validate(payload(), TEXT, today=TODAY)

    for field, group in [("summary", 0), ("document_type", 0), ("action_description", 1)]:
        generated = value(values, field, group)
        assert generated.quote == ""
        assert generated.confidence == Decimal("0.500")
    assert value(values, "summary").value == "Je moet belasting betalen."


def test_dutch_values_map_to_the_english_codes() -> None:
    values = validate(payload(), TEXT, today=TODAY)

    assert value(values, "document_type").value == "assessment"
    assert value(values, "action_type", 1).value == "pay"


def test_unknown_document_and_action_types_map_to_other() -> None:
    acties = [{**action()[0], "type": "dansen"}]

    values = validate(payload(documenttype="gedicht", acties=acties), TEXT, today=TODAY)

    assert value(values, "document_type").value == "other"
    assert value(values, "action_type", 1).value == "other"


def test_confidence_is_clamped_and_never_raised() -> None:
    acties = action(zekerheid=7)
    sender = {"naam": "Belastingdienst", "bron": "Belastingdienst", "zekerheid": -1}

    values = validate(payload(acties=acties, afzender=sender), TEXT, today=TODAY)

    assert value(values, "action_deadline", 1).confidence == Decimal("1.000")
    assert value(values, "sender").confidence == Decimal("0")


def test_each_action_gets_its_own_group() -> None:
    no_deadline = {"waarde": None, "bron": None, "zekerheid": 0}
    acties = [*action(), {"type": "reageren", "omschrijving": "Bel", "deadline": no_deadline}]

    values = validate(payload(acties=acties), TEXT, today=TODAY)

    assert value(values, "action_type", 1).value == "pay"
    assert value(values, "action_type", 2).value == "respond"


def test_missing_sender_and_date_give_no_values() -> None:
    empty = {"naam": None, "bron": None, "zekerheid": 0}
    undated = {"waarde": None, "bron": None, "zekerheid": 0}

    values = validate(payload(afzender=empty, dagtekening=undated), TEXT, today=TODAY)

    assert ("sender", 0) not in fields(values)
    assert ("letter_date", 0) not in fields(values)


def test_quote_matching_ignores_case_and_whitespace() -> None:
    assert quote_in_text("u moet  €\n1.234,56 BETALEN", TEXT)
    assert not quote_in_text("u moet € 1.234,57 betalen", TEXT)
    assert not quote_in_text("  ", TEXT)


def test_dates_in_dutch_text() -> None:
    text = "14-11-2026, 3 nov. 2026, 1 maart 2027, 2026-12-31, 5/1/2027, 31 februari 2026"

    assert dates_in(text) == {
        date(2026, 11, 14), date(2026, 11, 3), date(2027, 3, 1), date(2026, 12, 31),
        date(2027, 1, 5),
    }  # fmt: skip


def test_amounts_in_dutch_text() -> None:
    text = "€ 1.234,56 en 1234.56 en EUR 12,- en 99,95 en 1 000,00 en € 2.500 en EUR 40"

    assert amounts_in(text) == {
        Decimal("1234.56"), Decimal("12"), Decimal("99.95"), Decimal("1000.00"), Decimal("2500"),
        Decimal("40"),
    }  # fmt: skip


def test_plain_numbers_are_not_amounts() -> None:
    text = "Postbus 2500, binnen 30 dagen, jaar 2026, kenmerk 1234.56.789, NL91ABNA0417164300"

    assert amounts_in(text) == set()


def test_amount_that_is_only_a_plain_number_in_the_letter_gets_zero() -> None:
    # Final review: "Postbus 2500" must not confirm an invented amount of 2500.00.
    text = TEXT + " Postbus 2500"
    data = invoice(bedrag_incl="2500.00")

    values = validate(payload(factuur=data), text, today=TODAY)

    assert value(values, "invoice_amount_incl").confidence == Decimal("0")


def test_letter_date_is_not_accepted_as_due_date() -> None:
    # Not in the invoice quote, and the only matching date in the text is the letter's own.
    data = invoice(vervaldatum="2026-11-03")

    values = validate(payload(factuur=data), TEXT, today=TODAY)

    assert value(values, "invoice_due_date").confidence == Decimal("0")


def test_needs_attention_only_for_deadlines_and_amounts() -> None:
    low = Decimal("0.849")
    assert needs_attention("action_deadline", low)
    assert needs_attention("invoice_due_date", low)
    assert needs_attention("invoice_amount_incl", low)
    assert not needs_attention("action_deadline", Decimal("0.850"))
    assert not needs_attention("sender", low)
    assert not needs_attention("letter_date", Decimal("0"))


def test_payload_refuses_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        payload(extra="x")


def test_json_schema_matches_snapshot_and_has_no_refs() -> None:
    schema = json_schema()
    rendered = json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    assert "$ref" not in rendered
    assert "$defs" not in rendered
    assert sorted(cast(dict[str, object], schema["properties"])) == [
        "acties", "afzender", "dagtekening", "documenttype", "factuur", "samenvatting_b1",
    ]  # fmt: skip
    assert rendered == SNAPSHOT.read_text(encoding="utf-8"), (
        "update tests/fixtures/extraction_schema.json"
    )


def test_json_schema_is_strict_everywhere() -> None:
    def objects(node: object) -> list[dict[str, object]]:
        found: list[dict[str, object]] = []
        if isinstance(node, dict):
            if node.get("type") == "object":
                found.append(node)
            for child in node.values():
                found.extend(objects(child))
        elif isinstance(node, list):
            for child in node:
                found.extend(objects(child))
        return found

    all_objects = objects(json_schema())
    assert len(all_objects) >= 6
    for node in all_objects:
        assert node["additionalProperties"] is False
        assert sorted(cast(list[str], node["required"])) == sorted(
            cast(dict[str, object], node["properties"])
        )


def test_one_decimal_dutch_amount_is_parsed_with_cents() -> None:
    assert amounts_in("EUR 45,5") == {Decimal("45.50")}
    assert amounts_in("€ 1.234,5 te betalen") == {Decimal("1234.50")}


def test_english_thousands_form_is_parsed_only_when_unambiguous() -> None:
    assert amounts_in("Total 1,234.56") == {Decimal("1234.56")}
    assert amounts_in("€ 12,345,678.90") == {Decimal("12345678.90")}
    # Without cents a comma group could be Dutch decimals: never a smaller wrong number.
    assert amounts_in("€ 1,234") == set()
    assert amounts_in("1,234.5") == set()


def test_one_decimal_amount_in_the_letter_confirms_the_invoice() -> None:
    text = TEXT + " Te betalen: EUR 45,5"
    data = invoice(bedrag_incl="45.50", bron="Te betalen: EUR 45,5")

    values = validate(payload(factuur=data), text, today=TODAY)

    assert value(values, "invoice_amount_incl").confidence == Decimal("0.900")
