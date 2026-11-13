import uuid

import pytest
from django.contrib.auth import authenticate
from django.db import IntegrityError
from django.test import RequestFactory

from apps.accounts.models import User
from tests.factories import TEST_PASSWORD, UserFactory


def test_create_user_normalises_email_to_lower_case() -> None:
    user = User.objects.create_user(email="  Stijn@Example.NL ", password="x" * 12, name="Stijn")
    user.refresh_from_db()
    assert user.email == "stijn@example.nl"


def test_email_is_unique_regardless_of_case() -> None:
    UserFactory.create(email="stijn@example.nl")
    with pytest.raises(IntegrityError):
        User.objects.create_user(email="STIJN@example.nl", password="x" * 12, name="Ander")


def test_database_rejects_upper_case_email_written_around_the_model() -> None:
    user = UserFactory.create(email="stijn@example.nl")
    with pytest.raises(IntegrityError):
        User.objects.filter(pk=user.pk).update(email="Stijn@example.nl")


def test_id_is_a_uuid() -> None:
    user = UserFactory.create()
    assert isinstance(user.id, uuid.UUID)


def test_language_defaults_to_dutch() -> None:
    user = UserFactory.create()
    assert user.language == "nl"
    assert user.explanation_language == "nl"


def test_password_is_hashed_and_checkable() -> None:
    user = User.objects.create_user(email="a@example.nl", password="correct horse", name="A")
    assert user.password != "correct horse"
    assert user.check_password("correct horse")


def test_natural_key_lookup_ignores_case() -> None:
    user = UserFactory.create(email="stijn@example.nl")
    assert User.objects.get_by_natural_key("  Stijn@Example.NL") == user


def test_authenticate_accepts_mixed_case_email() -> None:
    user = UserFactory.create(email="stijn@example.nl")
    # django-axes needs the request (for the client address).
    request = RequestFactory().post("/api/v1/auth/login")
    assert authenticate(request, username="STIJN@example.nl", password=TEST_PASSWORD) == user


def test_explanation_languages_are_named_in_their_own_language() -> None:
    assert dict(User.ExplanationLanguage.choices) == {"nl": "Nederlands", "en": "English"}
