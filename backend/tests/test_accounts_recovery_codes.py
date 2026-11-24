"""Recovery codes (spec two-step sign-in, S 2): ten one-time codes, stored as keyed hashes."""

import re

from apps.accounts import recovery_codes
from apps.common.alphabet import CODE_ALPHABET

KEY = bytes(range(32))
OTHER_KEY = bytes(range(1, 33))


def test_the_alphabet_is_the_pairing_alphabet() -> None:
    assert recovery_codes.ALPHABET is CODE_ALPHABET
    # 32 characters: five bits each, fifty bits per code.
    assert len(set(CODE_ALPHABET)) == 32


def test_recovery_codes_are_ten_unique_from_the_alphabet() -> None:
    codes = recovery_codes.new_codes()

    assert len(codes) == 10
    assert len(set(codes)) == 10
    pattern = re.compile(f"[{CODE_ALPHABET}]{{5}}-[{CODE_ALPHABET}]{{5}}")
    assert all(pattern.fullmatch(code) for code in codes)


def test_two_sets_differ() -> None:
    assert set(recovery_codes.new_codes()).isdisjoint(recovery_codes.new_codes())


def test_normalise_drops_hyphens_spaces_and_case() -> None:
    assert recovery_codes.normalise(" abcde-fghjk ") == "ABCDEFGHJK"
    assert recovery_codes.normalise("ABCDE FGHJK") == "ABCDEFGHJK"


def test_recovery_hash_ignores_hyphen_and_case() -> None:
    expected = recovery_codes.code_hash(KEY, "ABCDE-FGHJK")

    assert recovery_codes.code_hash(KEY, "abcdefghjk") == expected
    assert recovery_codes.code_hash(KEY, "abcde fghjk") == expected
    assert len(expected) == 64
    assert "ABCDE" not in expected


def test_recovery_hash_depends_on_the_key() -> None:
    assert recovery_codes.code_hash(KEY, "ABCDE-FGHJK") != recovery_codes.code_hash(
        OTHER_KEY, "ABCDE-FGHJK"
    )


def test_recovery_codes_import_no_django() -> None:
    source = (recovery_codes.__file__ or "").replace(".pyc", ".py")
    with open(source, encoding="utf-8") as handle:
        assert "django" not in handle.read()
