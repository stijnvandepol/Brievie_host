"""Every configured password rule answers in Dutch (no database needed).

Django's Dutch catalogue lags behind its own messages (the minimum length's msgid
changed in Django 6), so its English sentence reached the user. The project's own
validators carry their sentences; this test fails for any validator that slips through.
"""

import pytest
from django.conf import settings
from django.contrib.auth.password_validation import (
    get_default_password_validators,
    validate_password,
)
from django.core.exceptions import ValidationError
from django.utils import translation

from apps.accounts.models import User

ENGLISH_WORDS = ("password", "must", "too ", "character", "similar", "common", "numeric")


def user() -> User:
    return User(email="anna.de.vries@example.nl", name="Anna de Vries")


@pytest.mark.parametrize(
    ("password", "expected"),
    [
        ("kort1!", "Dit wachtwoord is te kort. Gebruik minstens 12 tekens."),
        (
            "anna.de.vries@example.nl",
            "Dit wachtwoord lijkt te veel op je naam of e-mailadres.",
        ),
        ("Anna de Vries!", "Dit wachtwoord lijkt te veel op je naam of e-mailadres."),
        ("password1234", "Dit wachtwoord is te algemeen. Kies een ander."),
        ("123456789012345", "Dit wachtwoord bevat alleen cijfers. Gebruik ook letters."),
    ],
)
def test_each_rule_answers_one_dutch_sentence(password: str, expected: str) -> None:
    with translation.override("nl"), pytest.raises(ValidationError) as excinfo:
        validate_password(password, user())
    assert expected in excinfo.value.messages


def test_every_configured_validator_is_ours_and_speaks_dutch() -> None:
    validators = get_default_password_validators()
    assert len(validators) == len(settings.AUTH_PASSWORD_VALIDATORS) == 4
    with translation.override("nl"):
        for validator in validators:
            assert type(validator).__module__ == "apps.accounts.passwords", validator
            texts = [validator.get_help_text()]
            get_error_message = getattr(validator, "get_error_message", None)
            assert get_error_message is not None
            texts.append(str(get_error_message()))
            for text in texts:
                assert not any(word in text.lower() for word in ENGLISH_WORDS), text
                assert "email" not in text.replace("e-mailadres", ""), text
