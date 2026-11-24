"""The second step's code (spec two-step sign-in, S 1): RFC 6238 with the standard library."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from apps.accounts import totp

# RFC 6238 appendix B: the SHA-1 secret is the ASCII string "12345678901234567890".
RFC_SECRET = b"12345678901234567890"


def at(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, tz=UTC)


@pytest.mark.parametrize(
    ("seconds", "eight_digits"),
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ],
)
def test_rfc6238_sha1_vectors(seconds: int, eight_digits: str) -> None:
    # Six digits are the last six of the RFC's eight (the truncation is modulo 10^digits).
    assert totp.code_at(RFC_SECRET, totp.step_of(at(seconds))) == eight_digits[-6:]


def test_window_accepts_one_step_either_side_only() -> None:
    now = at(1111111111)
    step = totp.step_of(now)
    for offset in (-1, 0, 1):
        code = totp.code_at(RFC_SECRET, step + offset)
        assert totp.match(RFC_SECRET, code, now=now, last_used_step=0) == step + offset
    for offset in (-2, 2):
        code = totp.code_at(RFC_SECRET, step + offset)
        assert totp.match(RFC_SECRET, code, now=now, last_used_step=0) is None


def test_step_boundary() -> None:
    assert totp.step_of(at(29.999)) == 0
    assert totp.step_of(at(30.0)) == 1


def test_code_for_a_used_step_refused() -> None:
    now = at(1234567890)
    step = totp.step_of(now)
    code = totp.code_at(RFC_SECRET, step)

    assert totp.match(RFC_SECRET, code, now=now, last_used_step=step) is None
    assert totp.match(RFC_SECRET, code, now=now, last_used_step=step + 1) is None
    assert totp.match(RFC_SECRET, code, now=now, last_used_step=step - 1) == step


def test_a_code_of_a_later_step_still_passes_after_an_earlier_one() -> None:
    now = at(1234567890)
    step = totp.step_of(now)
    later = totp.code_at(RFC_SECRET, step + 1)

    assert totp.match(RFC_SECRET, later, now=now, last_used_step=step) == step + 1


def test_drift_reported_between_2_and_10_steps() -> None:
    now = at(1234567890)
    step = totp.step_of(now)

    assert totp.drift_steps(RFC_SECRET, totp.code_at(RFC_SECRET, step - 3), now=now) == 3
    assert totp.drift_steps(RFC_SECRET, totp.code_at(RFC_SECRET, step + 10), now=now) == 10
    assert totp.drift_steps(RFC_SECRET, totp.code_at(RFC_SECRET, step + 11), now=now) is None
    # Within the window it matches, so there is no drift to report.
    assert totp.drift_steps(RFC_SECRET, totp.code_at(RFC_SECRET, step + 1), now=now) is None


@pytest.mark.parametrize("code", ["", "12345", "1234567", "12a456", "١٢٣٤٥٦", "123 45x", None])
def test_non_digits_refused(code: str | None) -> None:
    now = at(1234567890)
    assert totp.match(RFC_SECRET, code or "", now=now, last_used_step=0) is None
    assert totp.drift_steps(RFC_SECRET, code or "", now=now) is None


def test_spaces_in_a_code_are_ignored() -> None:
    now = at(1234567890)
    code = totp.code_at(RFC_SECRET, totp.step_of(now))

    assert totp.match(RFC_SECRET, f" {code[:3]} {code[3:]} ", now=now, last_used_step=0)


def test_new_secret_is_twenty_random_bytes() -> None:
    first, second = totp.new_secret(), totp.new_secret()

    assert len(first) == 20
    assert first != second


def test_otpauth_uri_encodes_label_and_has_no_padding() -> None:
    uri = totp.otpauth_uri(RFC_SECRET, host="brievie.local", email="anne+post@example.nl")
    parts = urlsplit(uri)
    query = parse_qs(parts.query)

    assert parts.scheme == "otpauth"
    assert parts.netloc == "totp"
    assert parts.path == "/Brievie%20%28brievie.local%29:anne%2Bpost%40example.nl"
    assert query["secret"] == ["GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"]
    assert "=" not in query["secret"][0]
    assert query["issuer"] == ["Brievie (brievie.local)"]
    assert query["algorithm"] == ["SHA1"]
    assert query["digits"] == ["6"]
    assert query["period"] == ["30"]


def test_grouped_secret_is_base32_in_groups_of_four() -> None:
    assert totp.grouped_secret(RFC_SECRET) == "GEZD GNBV GY3T QOJQ GEZD GNBV GY3T QOJQ"


def test_totp_imports_no_django() -> None:
    source = (totp.__file__ or "").replace(".pyc", ".py")
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "django" not in text


def test_a_day_of_steps_never_repeats_a_match_for_one_code() -> None:
    # Over a stretch of time the same code is accepted only for the steps it belongs to.
    start = at(1700000000)
    step = totp.step_of(start)
    code = totp.code_at(RFC_SECRET, step)
    matches = {
        totp.match(RFC_SECRET, code, now=start + timedelta(seconds=30 * i), last_used_step=0)
        for i in range(-5, 6)
    }
    assert matches - {None} == {step}
