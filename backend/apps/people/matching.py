"""Recognising a person's name in a letter (spec section 6.3).

Pure and deterministic: no database, no network. The result is always a
suggestion; a match of `PRESELECT_FROM` or higher is preselected on the review screen.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from apps.common.text import excerpt, find_all_phrases, normalise

PRESELECT_FROM = Decimal("0.85")

AFTER_ADDRESS_WORD = Decimal("0.950")
IN_ADDRESS_BLOCK = Decimal("0.850")
ELSEWHERE = Decimal("0.600")

MIN_NAME_LENGTH = 3
ADDRESS_WORD_REACH = 40
ADDRESS_BLOCK_LENGTH = 600
PAGE_SEPARATOR = "\f"

ADDRESS_WORDS = (
    "t.a.v.",
    "ter attentie van",
    "aan",
    "de heer",
    "dhr.",
    "dhr",
    "mevrouw",
    "mevr.",
    "mevr",
    "geachte",
    "beste",
    "familie",
    "fam.",
)


@dataclass(frozen=True)
class PersonNames:
    person_id: UUID
    names: tuple[str, ...]


@dataclass(frozen=True)
class NameMatch:
    person_id: UUID
    quote: str
    confidence: Decimal


_NORMALISED_ADDRESS_WORDS = tuple(normalise(word) for word in ADDRESS_WORDS)
_LINE_BREAKS = "\n\r\f"


def _follows_address_word(text: str, start: int) -> bool:
    """True when an address word stands directly before `start` on the same line.

    Only whitespace and `.,:;` may sit between the word and the name (other address
    words end in an address word themselves), within `ADDRESS_WORD_REACH` characters.
    """
    window_start = max(0, start - ADDRESS_WORD_REACH)
    lead = text[window_start:start]
    for line_break in _LINE_BREAKS:
        lead = lead.rsplit(line_break, 1)[-1]
    cut = len(lead) == start - window_start and window_start > 0
    cut_inside_word = cut and text[window_start - 1].isalnum()
    normalised = normalise(lead)
    for candidate in (
        normalised.rstrip(" ,:;"),
        normalised.rstrip(" ,:;").rstrip(".").rstrip(" ,:;"),
    ):
        for word in _NORMALISED_ADDRESS_WORDS:
            if not candidate.endswith(word):
                continue
            before = len(candidate) - len(word)
            if before > 0:
                if not candidate[before - 1].isalnum():
                    return True
            elif not cut_inside_word:
                return True
    return False


def _confidence(text: str, first_page_length: int, start: int) -> Decimal:
    if _follows_address_word(text, start):
        return AFTER_ADDRESS_WORD
    if start < min(ADDRESS_BLOCK_LENGTH, first_page_length):
        return IN_ADDRESS_BLOCK
    return ELSEWHERE


def match_addressees(text: str, persons: Sequence[PersonNames]) -> list[NameMatch]:
    """One match per person found in `text`: the highest confidence, then the longest name."""
    first_page_length = text.find(PAGE_SEPARATOR)
    if first_page_length == -1:
        first_page_length = len(text)
    matches: list[NameMatch] = []
    for person in persons:
        best: tuple[Decimal, int, NameMatch] | None = None
        for name in person.names:
            length = len(normalise(name))
            if length < MIN_NAME_LENGTH:
                continue
            for span in find_all_phrases(text, name, whole_words=True):
                confidence = _confidence(text, first_page_length, span[0])
                if best is None or (confidence, length) > best[:2]:
                    best = (
                        confidence,
                        length,
                        NameMatch(person.person_id, excerpt(text, span), confidence),
                    )
        if best is not None:
            matches.append(best[2])
    return matches
