"""What an own document's upload may carry, checked without the database (archive plan
Task 4, `own.validate_fields`)."""

import uuid
from datetime import date

import pytest

from apps.common.errors import ApiError
from apps.documents import own
from apps.documents.schemas import UploadMetadataIn

TODAY = date(2026, 11, 24)


def _meta(**fields: object) -> UploadMetadataIn:
    return UploadMetadataIn.model_validate(
        {"client_id": str(uuid.uuid4()), "pages": [{"sha256": "0" * 64, "text": ""}], **fields}
    )


def _document(**fields: object) -> UploadMetadataIn:
    return _meta(kind="document", title="Paspoort", document_type="identity", **fields)


def _refused(metadata: UploadMetadataIn) -> ApiError:
    with pytest.raises(ApiError) as caught:
        own.validate_fields(metadata, today=TODAY)
    assert (caught.value.status, caught.value.code) == (422, "validation_error")
    return caught.value


def test_a_letter_without_the_new_fields_is_a_letter() -> None:
    assert own.validate_fields(_meta(), today=TODAY) is None
    assert own.validate_fields(_meta(kind="letter"), today=TODAY) is None


@pytest.mark.parametrize(
    "fields",
    [
        {"title": "Brief"},
        {"document_type": "invoice"},
        {"document_date": "2026-11-01"},
        {"person_ids": [str(uuid.uuid4())]},
        {"label_ids": [str(uuid.uuid4())]},
        {"expires_on": "2030-01-01"},
        {"remind_days": 7},
        {"retain_until": "2030-01-01"},
        {"private": True},
    ],
)
def test_a_letter_may_not_carry_an_own_documents_fields(fields: dict[str, object]) -> None:
    _refused(_meta(**fields))


def test_an_unknown_kind_is_refused() -> None:
    _refused(_meta(kind="folder", title="x", document_type="other"))


def test_a_document_needs_a_title_and_an_own_documents_type() -> None:
    assert "titel" in _refused(_meta(kind="document", document_type="identity")).message
    _refused(_meta(kind="document", title="   ", document_type="identity"))
    _refused(_meta(kind="document", title="Paspoort"))
    # A letter's type is not an own document's type.
    _refused(_meta(kind="document", title="Paspoort", document_type="invoice"))
    _refused(_meta(kind="document", title="x" * 201, document_type="other"))


def test_dates_and_lead_are_validated() -> None:
    _refused(_document(document_date="2026-11-30"))
    _refused(_document(document_date="1899-12-31"))
    _refused(_document(document_date="2026-01-10", expires_on="2026-01-09"))
    _refused(_document(expires_on="2300-01-01"))
    _refused(_document(retain_until="1800-01-01"))
    _refused(_document(remind_days=14))
    data = own.validate_fields(
        _document(
            document_date="2026-01-10",
            expires_on="2031-03-12",
            remind_days=90,
            retain_until="2033-01-01",
        ),
        today=TODAY,
    )
    assert data is not None
    assert (data.document_date, data.expires_on, data.remind_days, data.retain_until) == (
        date(2026, 1, 10),
        date(2031, 3, 12),
        90,
        date(2033, 1, 1),
    )


def test_the_title_is_tidied_and_bsn_masked() -> None:
    data = own.validate_fields(
        _meta(kind="document", title="  Kopie   paspoort 111222333 ", document_type="identity"),
        today=TODAY,
    )
    assert data is not None
    assert "111222333" not in data.title
    assert data.title.startswith("Kopie paspoort ")


def test_repeated_ids_count_once() -> None:
    person = uuid.uuid4()
    data = own.validate_fields(_document(person_ids=[str(person), str(person)]), today=TODAY)
    assert data is not None
    assert data.person_ids == (person,)
