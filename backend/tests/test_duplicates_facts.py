"""The pure side of duplicate letters (plan Task 2): the numbers in a letter, its payment
reference, the overlap of two number sets, the decision with its guards, and what is the
same and what differs. No database."""

import time
from dataclasses import replace
from decimal import Decimal

import pytest

from apps.documents.duplicates_facts import (
    Difference,
    Fact,
    LetterFacts,
    Verdict,
    decide,
    differences,
    number_overlap,
    numbers_in,
    payment_reference_in,
    same,
    strong,
)

TEXT = "Uw factuur voor de maand maart. Wij verzoeken u het bedrag te betalen."
THREE = frozenset({"2026-03-12", "1234.56", "00123456"})


def facts(**changes: object) -> LetterFacts:
    base = LetterFacts(
        text=TEXT,
        numbers=THREE,
        payment_reference=Fact("1234567890123456", strong=True),
        sender=Fact("Energie BV", strong=True),
        letter_date=Fact("2026-03-12", strong=True),
        document_type=Fact("invoice", strong=True),
        amount=Fact("1234.56", strong=True),
        invoice_number=Fact("F-2026-001", strong=True),
        page_count=2,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def likely(new: LetterFacts, other: LetterFacts, sim: str = "0.90", overlap: str = "1") -> Verdict:
    return decide(new, other, text_similarity=Decimal(sim), number_overlap=Decimal(overlap))


def test_numbers_normalise_dates_amounts_and_runs() -> None:
    text = "Datum 12-03-2026, 12 maart 2026, € 1.234,56, klant 00123456"

    assert numbers_in(text) == {"2026-03-12", "1234.56", "00123456"}


def test_numbers_other_spellings() -> None:
    text = "12/3/2026 en 1 Januari 2027, EUR 87,50 en 1234567,00; nr 123456789"

    assert numbers_in(text) == {"2026-03-12", "2027-01-01", "87.50", "1234567.00", "123456789"}


def test_numbers_ignore_an_invalid_date_and_short_runs() -> None:
    assert numbers_in("31-02-2026 en 31 februari 2026, huisnummer 12345") == frozenset()


def test_numbers_of_a_grouped_payment_reference() -> None:
    assert numbers_in("Betalingskenmerk 1234 5678 9012 3456") == {"1234567890123456"}


def test_payment_reference_grouped_or_not() -> None:
    assert payment_reference_in("Kenmerk 1234 5678 9012 3456.") == "1234567890123456"
    assert payment_reference_in("Kenmerk 1234567890123456") == "1234567890123456"
    assert payment_reference_in("Kenmerk 123456789012345") is None
    assert payment_reference_in("Kenmerk 12345678901234567") is None
    assert payment_reference_in("Geen kenmerk") is None


def test_number_overlap_is_jaccard() -> None:
    assert number_overlap(frozenset("abc"), frozenset("bcd")) == Decimal("0.500")
    assert number_overlap(frozenset(), frozenset()) == Decimal("0")
    assert number_overlap(frozenset("a"), frozenset()) == Decimal("0.000")
    assert number_overlap(frozenset("abc"), frozenset("abd")) == Decimal("0.500")
    assert number_overlap(frozenset("ab"), frozenset("abc")) == Decimal("0.667")


def test_likely_when_text_and_numbers_match() -> None:
    a, b = facts(), facts()

    assert likely(a, b, "0.80", "0.70") is Verdict.LIKELY
    assert likely(a, b, "0.79", "0.70") is Verdict.NONE
    assert likely(a, b, "0.80", "0.69") is Verdict.NONE


def test_few_numbers_need_text_only() -> None:
    a = facts(numbers=frozenset({"2026-03-12", "1234.56"}))
    b = facts(numbers=frozenset({"2026-03-13", "99.00"}))

    assert likely(a, b, "0.85", "0") is Verdict.LIKELY
    # Three numbers on one side: the overlap counts again.
    assert likely(facts(), b, "0.85", "0") is Verdict.NONE


OTHER = {
    "letter_date": "2026-04-12",
    "amount": "99.00",
    "invoice_number": "F-2026-002",
    "payment_reference": "6543210987654321",
    "document_type": "reminder",
}


@pytest.mark.parametrize("field", list(OTHER))
def test_guard_needs_both_strong(field: str) -> None:
    a = facts()
    both_strong = facts(**{field: Fact(OTHER[field], strong=True)})
    one_weak = facts(**{field: Fact(OTHER[field], strong=False)})

    assert likely(a, both_strong) is Verdict.NONE
    assert likely(both_strong, a) is Verdict.NONE
    assert likely(a, one_weak) is Verdict.LIKELY
    assert likely(one_weak, a) is Verdict.LIKELY
    found = [d for d in differences(a, one_weak) if d.field == field]
    assert found == [Difference(field, getattr(a, field).value, OTHER[field], uncertain=True)]


def test_guard_needs_both_values() -> None:
    assert likely(facts(), facts(letter_date=None, amount=None)) is Verdict.LIKELY


def test_equal_spellings_are_no_guard() -> None:
    other = facts(
        amount=Fact("1234.560", strong=True),
        invoice_number=Fact("f 2026-001", strong=True),
        sender=Fact("energie  bv", strong=True),
    )

    assert likely(facts(), other) is Verdict.LIKELY
    assert differences(facts(), other) == []


def test_reminder_is_not_a_duplicate() -> None:
    first = facts(letter_date=Fact("2026-03-01", strong=True))
    reminder = facts(
        letter_date=Fact("2026-04-01", strong=True), document_type=Fact("invoice", strong=True)
    )

    assert likely(first, reminder, "0.93", "0.80") is Verdict.NONE


def test_page_count_never_guards() -> None:
    a, b = facts(page_count=3), facts(page_count=2)

    assert likely(a, b) is Verdict.LIKELY
    assert differences(a, b) == [Difference("page_count", "3", "2", uncertain=False)]


def test_strong_is_confirmed_or_085() -> None:
    assert strong(True, None) is True
    assert strong(True, Decimal("0.100")) is True
    assert strong(False, Decimal("0.85")) is True
    assert strong(False, Decimal("0.849")) is False
    assert strong(False, None) is False


def test_same_lists_equal_facts() -> None:
    bare = facts(payment_reference=None, document_type=None, amount=None, invoice_number=None)
    a = replace(bare, page_count=1)
    b = replace(bare, page_count=2)

    assert same(a, b) == ["sender", "letter_date"]
    assert same(facts(), facts()) == [
        "payment_reference",
        "sender",
        "letter_date",
        "document_type",
        "amount",
        "invoice_number",
        "page_count",
    ]


def test_differences_put_uncertain_first() -> None:
    a = facts(page_count=1)
    b = facts(
        sender=Fact("Energie Noord", strong=True),
        amount=Fact("99.00", strong=False),
        page_count=2,
    )

    assert differences(a, b) == [
        Difference("amount", "1234.56", "99.00", uncertain=True),
        Difference("sender", "Energie BV", "Energie Noord", uncertain=False),
        Difference("page_count", "1", "2", uncertain=False),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "1" * 100_000 + "x",
        "woord " * 20_000,
        "€" * 50_000 + "1" * 50_000,
        "1." * 50_000,
        "12-" * 30_000,
        "1234 " * 20_000,
        "12 maart " * 10_000,
    ],
)
def test_regexes_are_linear(text: str) -> None:
    started = time.monotonic()
    numbers_in(text)
    payment_reference_in(text)
    assert time.monotonic() - started < 0.5
