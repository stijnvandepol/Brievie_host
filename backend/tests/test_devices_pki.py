import json
from pathlib import Path

import pytest
from django.test import Client
from pytest_django import Settings

from apps.devices.pki import certificate_sha256, load_ca_certificate_pem
from tests.factories import UserFactory

FIXTURE = Path(__file__).parent / "fixtures" / "ca_root.crt"
# openssl x509 -in tests/fixtures/ca_root.crt -outform der | sha256sum
FIXTURE_SHA256 = "54f4342d47de803e6812b6da3b0a57cf653dc36f03454c9232291f39834e060b"


def pairing_payload() -> dict[str, object]:
    client = Client()
    client.force_login(UserFactory.create())
    r = client.post("/api/v1/devices/pairing-codes")
    assert r.status_code == 201
    payload: dict[str, object] = json.loads(r.json()["qr_payload"])
    return payload


def test_certificate_sha256_matches_openssl() -> None:
    assert certificate_sha256(FIXTURE.read_text()) == FIXTURE_SHA256


def test_certificate_sha256_rejects_non_pem() -> None:
    with pytest.raises(ValueError, match="certificate"):
        certificate_sha256("geen certificaat")


def test_default_path_is_caddys_local_ca() -> None:
    from django.conf import settings

    assert settings.BRIEVIE_CA_CERT_PATH == "/caddy-data/caddy/pki/authorities/local/root.crt"


class TestWithoutPrivateCa:  # Review Focus 5
    @pytest.fixture(autouse=True)
    def _missing(self, settings: Settings, tmp_path: Path) -> None:
        settings.BRIEVIE_CA_CERT_PATH = str(tmp_path / "root.crt")

    def test_loader_returns_none(self) -> None:
        assert load_ca_certificate_pem() is None

    def test_endpoint_is_404(self, client: Client) -> None:
        r = client.get("/api/v1/server/ca-certificate")

        assert r.status_code == 404
        assert r.json()["code"] == "no_private_ca"

    def test_pairing_still_works_with_null_hash(self) -> None:
        payload = pairing_payload()

        assert "ca_sha256" in payload
        assert payload["ca_sha256"] is None


def test_empty_setting_means_no_private_ca(settings: Settings) -> None:
    settings.BRIEVIE_CA_CERT_PATH = ""
    assert load_ca_certificate_pem() is None


class TestWithPrivateCa:
    @pytest.fixture(autouse=True)
    def _present(self, settings: Settings) -> None:
        settings.BRIEVIE_CA_CERT_PATH = str(FIXTURE)

    def test_endpoint_serves_the_pem_without_auth(self, client: Client) -> None:
        r = client.get("/api/v1/server/ca-certificate")

        assert r.status_code == 200
        assert r.json() == {"pem": FIXTURE.read_text()}

    def test_payload_hash_equals_hash_of_served_pem(self, client: Client) -> None:
        served = client.get("/api/v1/server/ca-certificate").json()["pem"]

        payload = pairing_payload()

        assert payload["ca_sha256"] == certificate_sha256(served) == FIXTURE_SHA256
