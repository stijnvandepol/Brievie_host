"""Find and mask citizen service numbers (BSN) before text is stored, logged or sent."""

import logging
import re

MASK = "[BSN]"

# Nine digits, plain or grouped the way letters print them. Between the groups a short
# run (1 to 3) of separators: any whitespace (also line breaks, no-break, thin and
# narrow spaces, which OCR and PDFs produce), dots, commas, slashes, underscores, hyphens,
# dashes, the minus sign, soft hyphens and zero-width characters. Only a digit directly
# next to it rules a candidate out: OCR glues columns together, and an old VAT id
# (NL…B01) holds the BSN, so letters and other numbers around it are no reason to let it
# through. Masking a number too many is the safe side.
#
# Every quantifier is bounded and nothing is nested, so each starting position costs a
# constant: the scan is linear in the text.
_SEP_CHARS = r"\s./,_\u00ad\u200b-\u200d\u2060\ufeff\u2010-\u2015\u2212-"
_SEP = rf"[{_SEP_CHARS}]{{1,3}}"
_CANDIDATE = re.compile(
    r"(?<!\d)"
    rf"(?:\d{{9}}|\d{{3}}{_SEP}\d{{3}}{_SEP}\d{{3}}|\d{{4}}{_SEP}\d{{2}}{_SEP}\d{{3}}"
    rf"|\d{{2}}{_SEP}\d{{2}}{_SEP}\d{{2}}{_SEP}\d{{3}})"
    r"(?!\d)"
)
# Close after the word "BSN" any grouping counts, and eight digits too: an old sofinummer
# is printed without its leading zero.
_LABELLED = re.compile(rf"(?<!\d)\d(?:[{_SEP_CHARS}]{{0,3}}\d){{7,8}}(?!\d)")
_LABEL = re.compile(r"bsn|burgerservicenummer", re.IGNORECASE)
# How far after the word "BSN" a number still counts as labelled.
_LABEL_REACH = 30


def passes_elfproef(digits: str) -> bool:
    """Whether nine digits pass the elfproef, the check every BSN passes."""
    if len(digits) != 9 or not digits.isdigit() or digits == "000000000":
        return False
    weights = (9, 8, 7, 6, 5, 4, 3, 2, -1)
    return sum(int(d) * w for d, w in zip(digits, weights, strict=True)) % 11 == 0


def _labelled_windows(text: str) -> list[tuple[int, int]]:
    """Where a number starts close after the word "BSN": the windows [end, end + reach]
    of every label, merged, in order."""
    windows: list[tuple[int, int]] = []
    for label in _LABEL.finditer(text):
        start, until = label.end(), label.end() + _LABEL_REACH
        if windows and start <= windows[-1][1]:
            windows[-1] = (windows[-1][0], until)
        else:
            windows.append((start, until))
    return windows


def find_bsns(text: str) -> list[tuple[int, int]]:
    """Spans of BSNs in `text`, in order and without overlap.

    A candidate counts when it passes the elfproef, or when it starts close after the
    word "BSN": an OCR misread breaks the check digit, and masking a labelled number too
    often is the safe side. A labelled number may also be grouped otherwise or have eight
    digits. Linear in the text: the windows are merged and walked once.
    """
    windows = _labelled_windows(text)
    spans: list[tuple[int, int]] = []
    # Labelled numbers: each merged window is scanned once, from where it starts.
    for start, until in windows:
        position = start
        while True:
            match = _LABELLED.search(text, position, until + 40)
            if match is None or match.start() > until:
                break
            spans.append(match.span())
            position = match.end()
    index = 0
    for match in _CANDIDATE.finditer(text):
        # Windows are sorted and so are the matches: one pointer walks both.
        while index < len(windows) and windows[index][1] < match.start():
            index += 1
        labelled = index < len(windows) and windows[index][0] <= match.start()
        if labelled or passes_elfproef(re.sub(r"\D", "", match.group())):
            spans.append(match.span())
    return _merged(spans)


def _merged(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


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


def holds_bsn(text: str) -> bool:
    """Whether `text` holds a BSN, or the mask token that one became."""
    return bool(find_bsns(text)) or MASK.casefold() in text.casefold()


class BsnMaskingFilter(logging.Filter):
    """Masks BSNs in every log record, whatever code wrote it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_bsns(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            # The formatter reuses exc_text when it is set, so the traceback is masked too.
            record.exc_text = mask_bsns(logging.Formatter().formatException(record.exc_info))
        return True
