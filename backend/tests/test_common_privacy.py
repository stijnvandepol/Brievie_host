import logging

import pytest

from apps.common.privacy import BsnMaskingFilter, find_bsns, mask_bsns

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
        "ref A111222333",
        "bedrag 12 111 222 333",
        "000000000",
    ],
)
def test_non_bsn_numbers_are_kept(text: str) -> None:
    assert mask_bsns(text) == text


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
    from django.conf import settings

    handlers = settings.LOGGING["handlers"]
    assert handlers
    for handler in handlers.values():
        assert "mask_bsn" in handler.get("filters", [])


def test_tracebacks_are_masked() -> None:
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "fout", None, None)
    try:
        raise ValueError("BSN 111222333 klopt niet")
    except ValueError:
        import sys

        record.exc_info = sys.exc_info()

    BsnMaskingFilter().filter(record)
    formatted = logging.Formatter("%(message)s").format(record)

    assert "111222333" not in formatted
    assert "[BSN]" in formatted
