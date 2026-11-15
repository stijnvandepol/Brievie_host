"""Find and mask citizen service numbers (BSN) before text is stored, logged or sent."""

import logging
import re

MASK = "[BSN]"

# Nine digits, plain or grouped the way letters print them (dots, spaces, no-break
# spaces or hyphens between the groups). Only a digit directly next to it rules a
# candidate out: OCR glues columns together, and an old VAT id (NL…B01) holds the BSN,
# so letters and other numbers around it are no reason to let it through. Masking a
# number too many is the safe side.
_SEP = r"[.\u00a0 \t-]"
_CANDIDATE = re.compile(
    r"(?<!\d)"
    rf"(?:\d{{9}}|\d{{3}}{_SEP}\d{{3}}{_SEP}\d{{3}}|\d{{4}}{_SEP}\d{{2}}{_SEP}\d{{3}}"
    rf"|\d{{2}}{_SEP}\d{{2}}{_SEP}\d{{2}}{_SEP}\d{{3}})"
    r"(?!\d)"
)
_LABEL = re.compile(r"bsn|burgerservicenummer", re.IGNORECASE)
# How far after the word "BSN" a number still counts as labelled.
_LABEL_REACH = 30


def _passes_elfproef(digits: str) -> bool:
    if digits == "000000000":
        return False
    weights = (9, 8, 7, 6, 5, 4, 3, 2, -1)
    return sum(int(d) * w for d, w in zip(digits, weights, strict=True)) % 11 == 0


def find_bsns(text: str) -> list[tuple[int, int]]:
    """Spans of BSNs in `text`.

    A candidate counts when it passes the elfproef, or when it follows the word
    "BSN" closely: an OCR misread breaks the check digit, and masking a labelled
    number too often is the safe side.
    """
    labelled_until = [label.end() + _LABEL_REACH for label in _LABEL.finditer(text)]
    label_ends = [label.end() for label in _LABEL.finditer(text)]
    spans: list[tuple[int, int]] = []
    for match in _CANDIDATE.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        labelled = any(
            end <= match.start() <= until
            for end, until in zip(label_ends, labelled_until, strict=True)
        )
        if labelled or _passes_elfproef(digits):
            spans.append(match.span())
    return spans


def mask_bsns(text: str) -> str:
    """`text` with every BSN replaced by `[BSN]`."""
    parts: list[str] = []
    position = 0
    for start, end in find_bsns(text):
        parts.append(text[position:start])
        parts.append(MASK)
        position = end
    parts.append(text[position:])
    return "".join(parts)


class BsnMaskingFilter(logging.Filter):
    """Masks BSNs in every log record, whatever code wrote it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_bsns(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            # The formatter reuses exc_text when it is set, so the traceback is masked too.
            record.exc_text = mask_bsns(logging.Formatter().formatException(record.exc_info))
        return True
