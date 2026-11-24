"""The host name check of `prepare_server` (pure)."""

import pytest

from apps.installer.prepare import check_hostname


@pytest.mark.parametrize(
    ("name", "verdict"),
    [
        ("verander-mij.local", "placeholder"),
        ("Verander-Mij.local", "placeholder"),
        ("localhost", "loopback"),
        ("nas.localhost", "loopback"),
        ("127.0.0.1", "loopback"),
        ("my nas", "invalid"),
        ("", "invalid"),
        ("nas;rm -rf", "invalid"),
        ("-leading.dash", "invalid"),
        ("mijnnas.local", "ok"),
        ("192.168.1.20", "ok"),
        ("brievie.example.nl", "ok"),
    ],
)
def test_verdicts(name: str, verdict: str) -> None:
    assert check_hostname(name).verdict == verdict


def test_only_placeholder_and_invalid_refuse() -> None:
    assert check_hostname("verander-mij.local").refused
    assert check_hostname("my nas").refused
    assert not check_hostname("localhost").refused
    assert not check_hostname("mijnnas.local").refused
