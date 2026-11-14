"""Deterministic checks on what the AI returned (spec section 10, step 6).

Pure functions: payload and letter text in, validated values out. A value that
cannot be found in the letter gets confidence 0; a value that cannot be parsed is
dropped. The model's confidence is clamped and never raised.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from apps.documents.models import Document
from apps.pipeline.schema import ActionOut, ExtractionPayload, InvoiceOut, SourcedDate
from apps.tasks.models import Task

ZERO = Decimal("0")
# Generated text (summary, classification) has no source in the letter.
GENERATED = Decimal("0.500")


@dataclass(frozen=True)
class ValidatedValue:
    field: str
    group: int  # 0 for the document; 1..n per proposed action
    value: str
    quote: str
    confidence: Decimal


DOCUMENT_TYPES = {
    "factuur": Document.Type.INVOICE,
    "aanslag": Document.Type.ASSESSMENT,
    "beschikking": Document.Type.DECISION,
    "rekening": Document.Type.BILL,
    "herinnering": Document.Type.REMINDER,
    "aanmaning": Document.Type.DEMAND,
    "polis": Document.Type.POLICY,
    "jaaropgave": Document.Type.ANNUAL_STATEMENT,
    "loonstrook": Document.Type.PAYSLIP,
    "keuring": Document.Type.INSPECTION,
    "contract": Document.Type.CONTRACT,
    "bonnetje": Document.Type.RECEIPT,
    "informatie": Document.Type.INFORMATION,
    "overig": Document.Type.OTHER,
}
ACTION_TYPES = {
    "betalen": Task.Type.PAY,
    "reageren": Task.Type.RESPOND,
    "bezwaar": Task.Type.OBJECT,
    "aanvragen": Task.Type.APPLY,
    "keuring": Task.Type.INSPECTION,
    "opzeggen": Task.Type.CANCEL,
    "verlengen": Task.Type.RENEW,
    "aangifte": Task.Type.FILE_TAX,
    "overig": Task.Type.OTHER,
}


def validate(payload: ExtractionPayload, text: str, *, today: date) -> list[ValidatedValue]:
    values: list[ValidatedValue] = []
    values += _sender(payload, text)
    values.append(_generated("document_type", _code(DOCUMENT_TYPES, payload.document_type)))
    letter_date = _parse_date(payload.letter_date.value)
    values += _letter_date(payload.letter_date, letter_date, text, today)
    if payload.summary.strip():
        values.append(_generated("summary", payload.summary.strip()))
    for group, action in enumerate(payload.actions, start=1):
        values += _action(action, group, text, letter_date or today, today)
    if payload.invoice is not None:
        values += _invoice(payload.invoice, text, letter_date or today, today)
    return values


def _sender(payload: ExtractionPayload, text: str) -> list[ValidatedValue]:
    sender = payload.sender
    name = (sender.name or "").strip()
    if not name:
        return []
    quote = (sender.quote or "").strip()
    found = quote_in_text(quote, text) and _normalise(name) in _normalise(quote)
    confidence = _clamp(sender.confidence) if found else ZERO
    return [ValidatedValue("sender", 0, name, quote, confidence)]


def _letter_date(
    dated: SourcedDate, value: date | None, text: str, today: date
) -> list[ValidatedValue]:
    if value is None:
        return []
    quote = (dated.quote or "").strip()
    plausible = today - timedelta(days=3653) <= value <= today + timedelta(days=30)
    found = quote_in_text(quote, text) and value in dates_in(quote)
    confidence = _clamp(dated.confidence) if found and plausible else ZERO
    return [ValidatedValue("letter_date", 0, value.isoformat(), quote, confidence)]


def _action(
    action: ActionOut, group: int, text: str, reference: date, today: date
) -> list[ValidatedValue]:
    values = [_generated("action_type", _code(ACTION_TYPES, action.type), group)]
    if action.description.strip():
        values.append(_generated("action_description", action.description.strip(), group))
    deadline = _parse_date(action.deadline.value)
    if deadline is not None:
        quote = (action.deadline.quote or "").strip()
        found = quote_in_text(quote, text) and deadline in dates_in(quote)
        confidence = (
            _clamp(action.deadline.confidence)
            if found and _plausible_deadline(deadline, reference, today)
            else ZERO
        )
        values.append(
            ValidatedValue("action_deadline", group, deadline.isoformat(), quote, confidence)
        )
    return values


def _invoice(invoice: InvoiceOut, text: str, reference: date, today: date) -> list[ValidatedValue]:
    # One quote covers several invoice values, so a value may also be found
    # elsewhere in the letter.
    quote = (invoice.quote or "").strip()
    quoted = quote_in_text(quote, text)
    confidence = _clamp(invoice.confidence)
    values: list[ValidatedValue] = []

    number = (invoice.number or "").strip()
    if number:
        found = quoted and _normalise(number) in _normalise(text)
        values.append(
            ValidatedValue("invoice_number", 0, number, quote, confidence if found else ZERO)
        )

    due = _parse_date(invoice.due_date)
    if due is not None:
        found = quoted and due in dates_in(quote) | dates_in(text)
        plausible = _plausible_deadline(due, reference, today)
        values.append(
            ValidatedValue(
                "invoice_due_date",
                0,
                due.isoformat(),
                quote,
                confidence if found and plausible else ZERO,
            )
        )

    amount = _parse_amount(invoice.amount_incl)
    if amount is not None:
        found = quoted and amount in amounts_in(quote) | amounts_in(text)
        values.append(
            ValidatedValue(
                "invoice_amount_incl", 0, f"{amount:.2f}", quote, confidence if found else ZERO
            )
        )
    return values


def _plausible_deadline(deadline: date, reference: date, today: date) -> bool:
    return reference - timedelta(days=365) <= deadline <= today + timedelta(days=5 * 365)


def _generated(field: str, value: str, group: int = 0) -> ValidatedValue:
    return ValidatedValue(field, group, value, "", GENERATED)


def _code(codes: Mapping[str, str], dutch: str) -> str:
    return str(codes.get(dutch.strip().lower(), "other"))


def _clamp(confidence: float) -> Decimal:
    if confidence != confidence:  # NaN
        return ZERO
    return Decimal(str(min(1.0, max(0.0, confidence)))).quantize(Decimal("0.001"))


def _normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


def quote_in_text(quote: str, text: str) -> bool:
    """Whether `quote` is literally in `text`, ignoring case and line breaks."""
    needle = _normalise(quote)
    return bool(needle) and needle in _normalise(text)


def _parse_date(value: str | None) -> date | None:
    if not value or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _parse_amount(value: str | None) -> Decimal | None:
    if not value or not re.fullmatch(r"\d+(\.\d{1,2})?", value.strip()):
        return None
    try:
        return Decimal(value.strip())
    except InvalidOperation:
        return None


_MONTHS = {
    "jan": 1, "januari": 1, "feb": 2, "februari": 2, "mrt": 3, "maart": 3, "apr": 4,
    "april": 4, "mei": 5, "jun": 6, "juni": 6, "jul": 7, "juli": 7, "aug": 8,
    "augustus": 8, "sep": 9, "sept": 9, "september": 9, "okt": 10, "oktober": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}  # fmt: skip
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_NUMERIC_DATE = re.compile(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})(?!\d)")
_NAMED_DATE = re.compile(
    r"(?<!\d)(\d{1,2})\s+("
    + "|".join(sorted(_MONTHS, key=len, reverse=True))
    + r")\.?\s+(\d{4})(?!\d)",
    re.IGNORECASE,
)


def dates_in(text: str) -> set[date]:
    """Every date written in `text` in a common Dutch form."""
    candidates = [(int(y), int(m), int(d)) for y, m, d in _ISO_DATE.findall(text)]
    candidates += [(int(y), int(m), int(d)) for d, m, y in _NUMERIC_DATE.findall(text)]
    candidates += [
        (int(y), _MONTHS[month.lower()], int(d)) for d, month, y in _NAMED_DATE.findall(text)
    ]
    found = set()
    for year, month, day in candidates:
        try:
            found.add(date(year, month, day))
        except ValueError:
            continue
    return found


_AMOUNT = re.compile(r"(?<![\d.,])(\d{1,3}(?:[. ]\d{3})+|\d+)(?:,(\d{2}|-)|\.(\d{2}))?(?!\d)")


def amounts_in(text: str) -> set[Decimal]:
    """Every amount written in `text`: `1.234,56`, `1234.56`, `12,-`, `1 000,00`."""
    found = set()
    for whole, after_comma, after_point in _AMOUNT.findall(text):
        cents = after_comma if after_comma.isdigit() else after_point
        digits = re.sub(r"[. ]", "", whole)
        found.add(Decimal(f"{digits}.{cents}") if cents else Decimal(digits))
    return found
