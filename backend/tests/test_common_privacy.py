import logging
import random
import sys
import time
from typing import cast

import pytest
from django.conf import settings

from apps.common.privacy import (
    BsnMaskingFilter,
    find_bsns,
    holds_bsn,
    mask_bsns,
    passes_elfproef,
)

# 111222333 passes the elfproef; 123456789 and 111222334 do not.


@pytest.mark.parametrize(
    "text",
    [
        "BSN 111222333.",
        "bsn: 111.222.333",
        "Burgerservicenummer 1112 22 333",
        "nummer 111 222 333",
        "kenmerk 11.12.22.333 staat hier",
        "111222333",
    ],
)
def test_valid_bsn_is_masked(text: str) -> None:
    masked = mask_bsns(text)
    assert "111" not in masked
    assert "[BSN]" in masked


def test_text_around_the_bsn_is_kept() -> None:
    assert mask_bsns("Uw BSN 111222333 staat op de brief.") == "Uw BSN [BSN] staat op de brief."


@pytest.mark.parametrize(
    "text",
    [
        "Tel 0612345678",
        "Factuur 123456789",
        "IBAN NL91ABNA0417164300",
        "1112223334",
        "000000000",
        "2026-11-14 12:30",
    ],
)
def test_non_bsn_numbers_are_kept(text: str) -> None:
    assert mask_bsns(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "BSN: 1112.22.333",  # 4.2.3 with dots
        "111-222-333",
        "111\u00a0222\u00a0333",  # no-break spaces
        "BSN111222333",
        "111222333 01-02-1980",  # OCR flattened two columns
        "Jansen 1980 111222333",
        "btw-id NL111222333B01",  # an old VAT id of a sole trader holds the BSN
        "ref A111222333",
        "12 111 222 333",
    ],
)
def test_bsn_next_to_other_text_or_numbers_is_masked(text: str) -> None:
    # Final review: masking too much is the safe side; these forms leaked.
    masked = mask_bsns(text)
    assert "[BSN]" in masked
    assert "222" not in masked


def test_labelled_bsn_is_masked_even_when_misread() -> None:
    # An OCR misread breaks the check digit; the label makes the intent clear.
    assert mask_bsns("BSN: 111222334") == "BSN: [BSN]"
    assert mask_bsns("Burgerservicenummer (BSN)\n111.222.334") == "Burgerservicenummer (BSN)\n[BSN]"


def test_label_does_not_reach_far() -> None:
    text = "BSN staat op uw pas. Uw factuurnummer is in dit geval 123456789"
    assert mask_bsns(text) == text


def test_several_bsns_are_all_masked() -> None:
    assert mask_bsns("111222333 en 111.222.333") == "[BSN] en [BSN]"


def test_find_bsns_returns_spans() -> None:
    assert find_bsns("x 111222333 y") == [(2, 11)]


def test_log_records_are_masked(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("tests.privacy")
    logger.addFilter(BsnMaskingFilter())
    try:
        with caplog.at_level(logging.WARNING, logger="tests.privacy"):
            logger.warning("melding over %s", "BSN 111222333")
    finally:
        logger.filters.clear()

    assert "111222333" not in caplog.text
    assert "[BSN]" in caplog.text


def test_every_configured_handler_masks_bsns() -> None:
    handlers = cast(dict[str, dict[str, object]], settings.LOGGING["handlers"])
    assert handlers
    for handler in handlers.values():
        assert handler.get("filters") == ["mask_bsn"]


def test_tracebacks_are_masked() -> None:
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "fout", None, None)
    try:
        raise ValueError("BSN 111222333 klopt niet")
    except ValueError:
        record.exc_info = sys.exc_info()

    BsnMaskingFilter().filter(record)
    formatted = logging.Formatter("%(message)s").format(record)

    assert "111222333" not in formatted
    assert "[BSN]" in formatted


def test_the_log_level_follows_brievie_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    import runpy
    from pathlib import Path

    import brievie.settings

    root = cast(dict[str, object], settings.LOGGING["root"])
    assert root["level"] == "INFO"  # the services keep their INFO lines
    monkeypatch.setenv("BRIEVIE_LOG_LEVEL", "WARNING")
    loaded = runpy.run_path(str(Path(brievie.settings.__file__)))
    assert loaded["LOGGING"]["root"]["level"] == "WARNING"


@pytest.mark.parametrize(
    ("digits", "passes"),
    [
        ("111222333", True),
        ("123456789", False),
        ("111222334", False),
        ("000000000", False),
        ("11122233", False),
        ("1112223330", False),
        ("11122233a", False),
    ],
)
def test_passes_elfproef(digits: str, passes: bool) -> None:
    assert passes_elfproef(digits) is passes


def test_holds_bsn_finds_a_number_or_the_mask() -> None:
    assert holds_bsn("Jan 111222333")
    assert holds_bsn("BSN 123456782")
    assert holds_bsn("J [BSN]")
    assert holds_bsn("[bsn]")
    assert not holds_bsn("BSN")
    assert not holds_bsn("Jan de Vries")


# --- Security review 2026-11-20: forms OCR and print produce that leaked ---


@pytest.mark.parametrize(
    "text",
    [
        "nr 111  222  333",  # two spaces: OCR of columns
        "BSN: 111 222  333",
        "111 222\n333",  # a line break inside the number
        "111\r\n222\r\n333",
        "111\u202f222\u202f333",  # narrow no-break space
        "111\u2009222\u2009333",  # thin space
        "111\u2007222\u2007333",  # figure space
        "111/222/333",
        "111,222,333",
        "111_222_333",
        "111 - 222 - 333",
        "111\u2013222\u2013333",  # en dash
        "111\u2212222\u2212333",  # minus sign
        "111\u00ad222\u00ad333",  # soft hyphen
        "111\u200b222\u200b333",  # zero-width space
        "1112 / 22 / 333",
        "11.12.22.333",
        "11 12  22 333",
    ],
)
def test_bsn_with_other_separators_is_masked(text: str) -> None:
    masked = mask_bsns(text)
    assert "[BSN]" in masked
    assert "222" not in masked


@pytest.mark.parametrize(
    "text",
    [
        "BSN 12345678",  # an old sofinummer printed without its leading 0
        "BSN: 1234 5678",
        "burgerservicenummer 1 2 3 4 5 6 7 8",
        "BSN: 11 122 2333",  # an unusual grouping, but labelled
    ],
)
def test_a_labelled_number_of_eight_or_nine_digits_is_masked(text: str) -> None:
    masked = mask_bsns(text)
    assert masked.endswith("[BSN]"), masked
    assert not any(c.isdigit() for c in masked)


@pytest.mark.parametrize(
    "text",
    [
        "Tel 06 12 34 56 78",  # ten digits
        "Datum 14-11-2026",
        "Bedrag 1.234,56",
        "Postcode 1234 AB, huisnummer 12",
        "1112223334",
    ],
)
def test_other_numbers_with_separators_are_kept(text: str) -> None:
    assert mask_bsns(text) == text


def _valid_bsns(count: int) -> list[str]:
    rng = random.Random(20261120)  # noqa: S311 - test data, not a secret
    found: list[str] = []
    while len(found) < count:
        digits = f"{rng.randrange(10**8, 10**9):09d}"
        if passes_elfproef(digits):
            found.append(digits)
    return found


_LAYOUTS = ((3, 3, 3), (4, 2, 3), (2, 2, 2, 3))
_SEPARATORS = (" ", "  ", ".", "-", "/", ",", "\n", "\u00a0", "\u202f", " / ", "\t")


def _grouped(digits: str, layout: tuple[int, ...], separator: str) -> str:
    groups, position = [], 0
    for size in layout:
        groups.append(digits[position : position + size])
        position += size
    return separator.join(groups)


def test_every_valid_bsn_in_every_printed_form_is_masked() -> None:
    # Property-style: every layout and separator, inside ordinary text.
    for digits in _valid_bsns(40):
        for layout in _LAYOUTS:
            for separator in _SEPARATORS:
                number = _grouped(digits, layout, separator)
                text = f"Geachte heer, uw nummer {number} staat hieronder."
                masked = mask_bsns(text)
                assert masked == "Geachte heer, uw nummer [BSN] staat hieronder.", repr(text)


def test_nine_digits_that_fail_the_elfproef_stay_without_a_label() -> None:
    rng = random.Random(7)  # noqa: S311 - test data, not a secret
    for _ in range(200):
        digits = f"{rng.randrange(10**8, 10**9):09d}"
        if passes_elfproef(digits):
            continue
        text = f"Kenmerk {_grouped(digits, (3, 3, 3), ' ')} van uw factuur"
        assert mask_bsns(text) == text


def test_masking_is_linear_in_labels_and_numbers() -> None:
    # Many labels and many numbers on one page: once quadratic (review L3).
    # Numbers that fail the elfproef first, so every one is checked against every label.
    page = ("123456789 " * 4000) + ("bsn " * 20000) + "bsn 123456789"
    started = time.perf_counter()
    masked = mask_bsns(page)
    elapsed = time.perf_counter() - started

    assert masked.count("123456789") == 4000
    assert masked.endswith("bsn [BSN]")
    assert elapsed < 0.5, elapsed
