"""What two letters have in common, and whether they are probably one letter scanned twice
(spec "What is compared" and "The decision"). Pure: no database, no model import beyond
`NEEDS_ATTENTION_BELOW`.

The numbers in a letter are what tell template letters apart (a reminder, a second
instalment, next month's invoice): dates, amounts and long digit runs, normalised so two
scans of one letter give the same set. Every regex has bounded quantifiers and no nested
ones, so each runs in linear time over the text.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from typing import Final

from apps.documents.models import NEEDS_ATTENTION_BELOW

TEXT_SIMILAR_FROM: Final = Decimal("0.80")
NUMBERS_SHARED_FROM: Final = Decimal("0.70")
# Below this many numbers on both letters the overlap says little: text alone decides.
FEW_NUMBERS: Final = 3


@dataclass(frozen=True)
class Fact:
    value: str
    strong: bool


@dataclass(frozen=True)
class LetterFacts:
    text: str
    numbers: frozenset[str]
    payment_reference: Fact | None
    sender: Fact | None
    letter_date: Fact | None
    document_type: Fact | None
    amount: Fact | None
    invoice_number: Fact | None
    page_count: int


class Verdict(StrEnum):
    NONE = "none"
    LIKELY = "likely"


@dataclass(frozen=True)
class Difference:
    field: str
    first: str
    second: str
    uncertain: bool


# The facts compared field by field, in the order of the spec's table.
FIELDS: Final = (
    "payment_reference",
    "sender",
    "letter_date",
    "document_type",
    "amount",
    "invoice_number",
)
# A difference in one of these, when both values are strong, means two letters.
GUARDS: Final = ("letter_date", "amount", "invoice_number", "payment_reference", "document_type")

_MONTHS: Final = (
    "januari",
    "februari",
    "maart",
    "april",
    "mei",
    "juni",
    "juli",
    "augustus",
    "september",
    "oktober",
    "november",
    "december",
)

# 16 digits, alone or in four groups of four with one space between (an acceptgiro).
_PAYMENT_REFERENCE = re.compile(r"(?<!\d)(?<!\d )\d{4}( ?)\d{4}\1\d{4}\1\d{4}(?!\d)(?! \d)")
# 12-03-2026, 12/3/2026, 12.03.2026
_DATE_DMY = re.compile(r"(?<!\d)(\d{1,2})[-./](\d{1,2})[-./](\d{4})(?!\d)")
# 2026-03-12
_DATE_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
# 12 maart 2026
_DATE_WORDS = re.compile(
    r"(?<!\d)(\d{1,2})\s{1,3}(" + "|".join(_MONTHS) + r")\s{1,3}(\d{4})(?!\d)", re.IGNORECASE
)
# 1.234,56 or 1234,56 (with or without a currency sign before it); the dots are checked
# in Python.
_AMOUNT = re.compile(r"(?<![\d.,])(\d[\d.]{0,17}),(\d{2})(?!\d)")
_DIGIT_RUN = re.compile(r"(?<!\d)\d{6,64}(?!\d)")
_SPACE = re.compile(r"\s{1,64}")
_NOT_ALNUM = re.compile(r"[\W_]{1,64}")


def _iso(year: str, month: str, day: str) -> str | None:
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


def _amount(whole: str, cents: str) -> str | None:
    parts = whole.split(".")
    if len(parts) > 1 and not (
        1 <= len(parts[0]) <= 3 and all(len(part) == 3 for part in parts[1:])
    ):
        return None
    return f"{int(''.join(parts))}.{cents}"


def numbers_in(text: str) -> frozenset[str]:
    """The dates (ISO), amounts (decimal text, `1234.56`), payment references and digit
    runs of 6 or more (without separators) in `text`. Each match is blanked before the
    next kind is looked for, so one number counts once. An invalid date is left out."""
    found: set[str] = set()

    def take(pattern: re.Pattern[str], value: Callable[[re.Match[str]], str | None]) -> None:
        nonlocal text

        def keep(match: re.Match[str]) -> str:
            normal = value(match)
            if normal is not None:
                found.add(normal)
            return " " * len(match.group(0))

        text = pattern.sub(keep, text)

    take(_PAYMENT_REFERENCE, lambda m: m.group(0).replace(" ", ""))
    take(_DATE_DMY, lambda m: _iso(m.group(3), m.group(2), m.group(1)))
    take(_DATE_ISO, lambda m: _iso(m.group(1), m.group(2), m.group(3)))
    take(
        _DATE_WORDS,
        lambda m: _iso(m.group(3), str(_MONTHS.index(m.group(2).lower()) + 1), m.group(1)),
    )
    take(_AMOUNT, lambda m: _amount(m.group(1), m.group(2)))
    take(_DIGIT_RUN, lambda m: m.group(0))
    return frozenset(found)


def payment_reference_in(text: str) -> str | None:
    """The first 16-digit payment reference in `text`, digits only, or None."""
    match = _PAYMENT_REFERENCE.search(text)
    return None if match is None else match.group(0).replace(" ", "")


def number_overlap(a: frozenset[str], b: frozenset[str]) -> Decimal:
    """Jaccard of two number sets, 3 decimals; `Decimal("0")` when both are empty."""
    union = a | b
    if not union:
        return Decimal("0")
    return (Decimal(len(a & b)) / Decimal(len(union))).quantize(
        Decimal("0.001"), rounding=ROUND_HALF_UP
    )


def strong(confirmed: bool, confidence: Decimal | None) -> bool:
    """A value is strong when a person confirmed it or it was read with confidence
    `NEEDS_ATTENTION_BELOW` (0.85) or more."""
    return confirmed or (confidence is not None and confidence >= NEEDS_ATTENTION_BELOW)


def _normal(field: str, value: str) -> object:
    """The form two values are compared in: amounts as numbers, references on their
    letters and digits, other text without case and extra spaces."""
    if field == "amount":
        try:
            return Decimal(value)
        except InvalidOperation:
            return value.strip()
    if field in ("invoice_number", "payment_reference"):
        return _NOT_ALNUM.sub("", value).casefold()
    return _SPACE.sub(" ", value).strip().casefold()


def _pairs(a: LetterFacts, b: LetterFacts) -> list[tuple[str, Fact, Fact]]:
    """Every fact present on both letters, in table order, page count last."""
    found: list[tuple[str, Fact, Fact]] = []
    for field in FIELDS:
        left, right = getattr(a, field), getattr(b, field)
        if isinstance(left, Fact) and isinstance(right, Fact):
            found.append((field, left, right))
    found.append(
        ("page_count", Fact(str(a.page_count), strong=True), Fact(str(b.page_count), strong=True))
    )
    return found


def _equal(field: str, left: Fact, right: Fact) -> bool:
    return _normal(field, left.value) == _normal(field, right.value)


def decide(
    new: LetterFacts, other: LetterFacts, *, text_similarity: Decimal, number_overlap: Decimal
) -> Verdict:
    """`LIKELY` when the texts are alike and (their numbers are shared, or both letters
    have fewer than 3 numbers), unless a guard fires: a date, amount, invoice number,
    payment reference or kind of letter that differs while both values are strong."""
    for field, left, right in _pairs(new, other):
        if field in GUARDS and left.strong and right.strong and not _equal(field, left, right):
            return Verdict.NONE
    if text_similarity < TEXT_SIMILAR_FROM:
        return Verdict.NONE
    few = len(new.numbers) < FEW_NUMBERS and len(other.numbers) < FEW_NUMBERS
    if few or number_overlap >= NUMBERS_SHARED_FROM:
        return Verdict.LIKELY
    return Verdict.NONE


def differences(a: LetterFacts, b: LetterFacts) -> list[Difference]:
    """The facts present on both letters that differ, the uncertain ones (one side weak:
    maybe a misread) first, otherwise in table order."""
    found = [
        Difference(field, left.value, right.value, uncertain=not (left.strong and right.strong))
        for field, left, right in _pairs(a, b)
        if not _equal(field, left, right)
    ]
    return sorted(found, key=lambda d: not d.uncertain)


def same(a: LetterFacts, b: LetterFacts) -> list[str]:
    """The names of the facts present on both letters that are equal, in table order."""
    return [field for field, left, right in _pairs(a, b) if _equal(field, left, right)]
