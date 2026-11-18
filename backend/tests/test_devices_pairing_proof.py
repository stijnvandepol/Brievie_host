"""Pairing from a link with a proof, never the code (D 6.2 as changed by D 20)."""

import hmac
import json
import logging
import threading
from datetime import timedelta
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID, uuid4

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from pytest_django import Settings

from apps.accounts.models import User
from apps.audit.models import ServerAuditEvent
from apps.common import crypto
from apps.devices import jobs, pki, proof
from apps.devices.models import Device, PairingChallenge, PairingCode
from apps.devices.proof import (
    PROOF_CONTEXT,
    PROOF_KDF_ITERATIONS,
    derive_proof_key,
    pairing_proof,
    trust_bindings,
)
from apps.devices.services import create_pairing_challenge, create_pairing_code
from tests.factories import UserFactory
from tests.helpers import post_json

if TYPE_CHECKING:
    from tests.helpers import Response

VECTOR = Path(__file__).parent / "fixtures" / "pairing_proof_v1.json"
HOST = "brievie.example.nl"
WRONG = "0" * 64
INVALID_CODE = {
    "code": "pairing_code_invalid",
    "message": "Deze koppelcode klopt niet of is verlopen. Maak een nieuwe code aan.",
}
INVALID_CHALLENGE = {
    "code": "pairing_challenge_invalid",
    "message": "Het koppelen duurde te lang. Probeer het opnieuw.",
}


@pytest.fixture(autouse=True)
def _public_host(settings: Settings) -> None:
    settings.BRIEVIE_PUBLIC_URLS = [f"https://{HOST}"]


@pytest.fixture
def user() -> User:
    return UserFactory.create()


@pytest.fixture
def session_client(client: Client, user: User) -> Client:
    client.force_login(user)
    return client


_addresses = (f"198.51.100.{n}" for n in range(1, 255))


def fresh_client() -> Client:
    """A client at an address of its own, so the per-address throttles stay out of the way."""
    return Client(REMOTE_ADDR=next(_addresses))


@cache
def key_for(code: str, pairing_id: str) -> bytes:
    return derive_proof_key(code, UUID(pairing_id))


def sealed_key_of(pairing_id: UUID) -> bytes:
    stored = PairingCode.objects.get(id=pairing_id).proof_key_sealed
    assert stored is not None
    return bytes(stored)


def issue(session_client: Client) -> tuple[str, str]:
    """A new code through the API: the typed code and the pairing id from its link."""
    body = post_json(session_client, "/devices/pairing-codes").json()
    fields = dict(parse_qsl(urlsplit(body["server_link"]).fragment))
    return body["code"], fields["pairing"]


def challenge(client: Client | None = None) -> str:
    r = post_json(client or fresh_client(), "/devices/pairing-challenges")
    assert r.status_code == 201, r.content
    nonce: str = r.json()["nonce"]
    return nonce


def prove(code: str, pairing_id: str, nonce: str, binding: str = HOST) -> str:
    return pairing_proof(key_for(code, pairing_id), nonce=nonce, trust_binding=binding)


def send(
    pairing_id: str, nonce: str, proof_hex: str, client: Client | None = None, **extra: str
) -> "Response":
    body = {"pairing_id": pairing_id, "nonce": nonce, "proof": proof_hex, "device_name": "iPhone"}
    return post_json(client or fresh_client(), "/devices/pair-with-proof", {**body, **extra})


def pair_by_proof(session_client: Client) -> tuple[str, str, "Response"]:
    code, pairing_id = issue(session_client)
    nonce = challenge()
    return code, pairing_id, send(pairing_id, nonce, prove(code, pairing_id, nonce))


# --- The protocol -------------------------------------------------------------------------


def test_proof_matches_the_shared_vector() -> None:
    vector = json.loads(VECTOR.read_text())
    assert vector["context"].encode() == PROOF_CONTEXT
    assert vector["iterations"] == PROOF_KDF_ITERATIONS
    assert proof.normalise_code(vector["typed_code"]) == vector["normalised_code"]

    key = derive_proof_key(vector["typed_code"], UUID(vector["pairing_id"]))

    assert key.hex() == vector["key"]
    for case in vector["cases"]:
        assert (
            pairing_proof(key, nonce=vector["nonce"], trust_binding=case["trust_binding"])
            == case["proof"]
        )


def test_trust_bindings_are_the_pin_and_the_public_hosts(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.BRIEVIE_PUBLIC_URLS = ["https://Brievie.Example.NL.", "https://[2001:db8::1]:8443"]
    monkeypatch.setattr(pki, "ca_certificate_sha256", lambda: "ab" * 32)

    assert trust_bindings() == {"ab" * 32, "brievie.example.nl", "2001:db8::1"}


def test_trust_bindings_without_a_private_ca_are_the_hosts(settings: Settings) -> None:
    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.local:8443/", "https://bücher.example"]

    assert trust_bindings() == {"brievie.local", "xn--bcher-kva.example"}


def test_the_nonce_has_the_length_the_protocol_assumes() -> None:
    assert len(create_pairing_challenge().nonce) == 43


# --- Pairing by proof ---------------------------------------------------------------------


def test_a_link_pairing_pairs_without_the_code(session_client: Client, user: User) -> None:
    code, pairing_id = issue(session_client)
    app = fresh_client()
    nonce = challenge(app)

    r = send(pairing_id, nonce, prove(code, pairing_id, nonce), app, app_version="1.6.0 (3)")

    assert r.status_code == 201, r.content
    body = r.json()
    assert set(body) == {"device_id", "token", "user"}
    assert r["Cache-Control"] == "no-store"
    assert body["user"]["id"] == str(user.id)
    device = Device.objects.get(id=body["device_id"])
    assert (device.user, device.name, device.paired_via) == (user, "iPhone", "password")
    assert device.app_version == "1.6.0 (3)"
    me = Client().get("/api/v1/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200
    [row] = ServerAuditEvent.objects.filter(code="device_paired")
    assert row.values == {"via": "proof"}
    assert (row.actor_id, str(row.target_id)) == (user.id, body["device_id"])
    code_row = PairingCode.objects.get(id=pairing_id)
    assert code_row.used_at is not None
    assert code_row.proof_key_sealed is None


def test_a_link_pairing_copies_the_sessions_sign_in_method(session_client: Client) -> None:
    from apps.accounts.models import UserSession

    UserSession.objects.update(method=UserSession.Method.SSO)
    _, _, r = pair_by_proof(session_client)

    assert r.status_code == 201
    assert Device.objects.get().paired_via == "sso"


def test_no_request_of_the_link_flow_carries_the_code(
    session_client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, pairing_id = issue(session_client)
    sent: list[bytes] = []
    original = Client.post

    def recording_post(self: Client, path: str, *args: object, **kwargs: object) -> object:
        sent.append(repr((path, args, kwargs)).encode())
        return original(self, path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Client, "post", recording_post)
    nonce = challenge()
    r = send(pairing_id, nonce, prove(code, pairing_id, nonce))

    assert r.status_code == 201
    assert len(sent) == 2
    for request in sent:
        for secret in (code, code.replace("-", ""), code.lower(), code.replace("-", "").lower()):
            assert secret.encode() not in request


def test_a_proof_bound_to_another_ca_is_refused(
    session_client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pki, "ca_certificate_sha256", lambda: "ab" * 32)
    code, pairing_id = issue(session_client)
    nonce = challenge()

    r = send(pairing_id, nonce, prove(code, pairing_id, nonce, binding="cd" * 32))

    assert r.status_code == 400
    assert r.json() == INVALID_CODE
    assert PairingCode.objects.get(id=pairing_id).failed_proofs == 1
    assert Device.objects.count() == 0
    # The same code still pairs by QR.
    qr = post_json(fresh_client(), "/devices/pair", {"code": code, "device_name": "iPhone"})
    assert qr.status_code == 201


def test_a_proof_bound_to_the_pin_is_accepted(
    session_client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pki, "ca_certificate_sha256", lambda: "ab" * 32)
    code, pairing_id = issue(session_client)
    nonce = challenge()

    r = send(pairing_id, nonce, prove(code, pairing_id, nonce, binding="ab" * 32))

    assert r.status_code == 201


def test_a_proof_bound_to_another_host_is_refused(session_client: Client) -> None:
    code, pairing_id = issue(session_client)
    nonce = challenge()

    r = send(pairing_id, nonce, prove(code, pairing_id, nonce, binding="attacker.example"))

    assert r.status_code == 400
    assert r.json() == INVALID_CODE
    assert Device.objects.count() == 0


def test_a_proof_for_another_code_or_nonce_is_refused(session_client: Client) -> None:
    code, pairing_id = issue(session_client)
    nonce = challenge()
    other_nonce = challenge()

    wrong_code = send(pairing_id, nonce, prove("AAAA-AAAA", pairing_id, nonce))
    wrong_nonce = send(pairing_id, challenge(), prove(code, pairing_id, other_nonce))

    assert wrong_code.status_code == 400
    assert wrong_nonce.status_code == 400
    assert PairingCode.objects.get(id=pairing_id).failed_proofs == 2


def test_proofs_are_compared_in_constant_time_against_every_binding(
    session_client: Client, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.BRIEVIE_PUBLIC_URLS = [f"https://{HOST}", "https://brievie.local"]
    monkeypatch.setattr(pki, "ca_certificate_sha256", lambda: "ab" * 32)
    code, pairing_id = issue(session_client)
    nonce = challenge()
    calls: list[tuple[object, object]] = []
    original = hmac.compare_digest

    def recording(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        result: bool = original(a, b)
        return result

    monkeypatch.setattr(hmac, "compare_digest", recording)

    for binding in sorted(trust_bindings()):
        calls.clear()
        r = send(pairing_id, nonce, prove(code, pairing_id, nonce, binding=binding))
        # Every candidate is compared, also after a match: no early exit.
        assert len(calls) == 3
        assert r.status_code in (201, 400)
        nonce = challenge()


def test_a_replayed_nonce_is_refused(session_client: Client) -> None:
    code, pairing_id = issue(session_client)
    nonce = challenge()
    assert send(pairing_id, nonce, prove(code, pairing_id, nonce)).status_code == 201

    second_code, second_id = issue(session_client)
    replay = send(second_id, nonce, prove(second_code, second_id, nonce))

    assert replay.status_code == 400
    assert replay.json() == INVALID_CHALLENGE
    # A refused nonce does not count against the code.
    assert PairingCode.objects.get(id=second_id).failed_proofs == 0

    expired = create_pairing_challenge()
    PairingChallenge.objects.filter(pk=expired.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    late = send(second_id, expired.nonce, prove(second_code, second_id, expired.nonce))
    assert late.status_code == 400
    assert late.json() == INVALID_CHALLENGE

    unknown = "x" * 43
    r = send(second_id, unknown, prove(second_code, second_id, unknown))
    assert r.status_code == 400
    assert r.json() == INVALID_CHALLENGE

    nonce = challenge()
    assert send(second_id, nonce, prove(second_code, second_id, nonce)).status_code == 201


def test_a_nonce_is_used_up_by_a_wrong_proof(session_client: Client) -> None:
    code, pairing_id = issue(session_client)
    nonce = challenge()
    assert send(pairing_id, nonce, WRONG).status_code == 400

    r = send(pairing_id, nonce, prove(code, pairing_id, nonce))

    assert r.json() == INVALID_CHALLENGE


def test_challenges_expire_after_five_minutes() -> None:
    challenge_row = create_pairing_challenge()

    left = challenge_row.expires_at - timezone.now()

    assert timedelta(minutes=4) < left <= timedelta(minutes=5)
    assert challenge_row.used_at is None


def test_five_wrong_proofs_burn_the_code(session_client: Client) -> None:
    code, pairing_id = issue(session_client)
    for attempt in range(1, 6):
        r = send(pairing_id, challenge(), WRONG)
        assert r.status_code == 400
        assert r.json() == INVALID_CODE
        assert PairingCode.objects.get(id=pairing_id).failed_proofs == attempt

    burned = PairingCode.objects.get(id=pairing_id)
    assert burned.used_at is not None
    assert burned.proof_key_sealed is None

    nonce = challenge()
    sixth = send(pairing_id, nonce, prove(code, pairing_id, nonce))
    assert sixth.status_code == 400
    assert sixth.json() == INVALID_CODE
    qr = post_json(fresh_client(), "/devices/pair", {"code": code, "device_name": "iPhone"})
    assert qr.status_code == 400
    assert qr.json()["code"] == "pairing_code_invalid"
    assert Device.objects.count() == 0


def test_wrong_proofs_are_counted_per_pairing_id(session_client: Client) -> None:
    _, first_id = issue(session_client)
    other = Client()
    other.force_login(UserFactory.create())
    second_code, second_id = issue(other)

    for _ in range(5):
        send(first_id, challenge(), WRONG)

    assert PairingCode.objects.get(id=second_id).failed_proofs == 0
    nonce = challenge()
    assert send(second_id, nonce, prove(second_code, second_id, nonce)).status_code == 201


def test_failed_proofs_are_counted_although_the_request_fails(session_client: Client) -> None:
    _, pairing_id = issue(session_client)

    r = send(pairing_id, challenge(), WRONG)

    assert r.status_code == 400
    with connection.cursor() as c:
        c.execute("SELECT failed_proofs FROM devices_pairingcode WHERE id = %s", [pairing_id])
        assert c.fetchone() == (1,)


def test_a_proof_for_an_inactive_user_is_refused(session_client: Client, user: User) -> None:
    code, pairing_id = issue(session_client)
    User.objects.filter(pk=user.pk).update(is_active=False)
    nonce = challenge()

    r = send(pairing_id, nonce, prove(code, pairing_id, nonce))

    assert r.json() == INVALID_CODE


def test_a_device_name_is_required(session_client: Client) -> None:
    code, pairing_id = issue(session_client)
    nonce = challenge()

    r = send(pairing_id, nonce, prove(code, pairing_id, nonce), device_name="   ")

    assert r.status_code == 422
    # The nonce is still unused and the code unharmed.
    assert send(pairing_id, nonce, prove(code, pairing_id, nonce)).status_code == 201


@pytest.mark.parametrize("bad", ["A" * 64, "0" * 63, "0" * 65, "g" * 64])
def test_a_proof_that_is_no_lower_case_hex_is_a_validation_error(
    session_client: Client, bad: str
) -> None:
    _, pairing_id = issue(session_client)

    r = send(pairing_id, challenge(), bad)

    assert r.status_code == 422
    assert PairingCode.objects.get(id=pairing_id).failed_proofs == 0


# --- What it reveals ----------------------------------------------------------------------


def _state_unknown(session_client: Client) -> str:
    issue(session_client)
    return str(uuid4())


def _state_expired(session_client: Client) -> str:
    _, pairing_id = issue(session_client)
    PairingCode.objects.filter(id=pairing_id).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    return pairing_id


def _state_used(session_client: Client) -> str:
    code, pairing_id = issue(session_client)
    qr = post_json(fresh_client(), "/devices/pair", {"code": code, "device_name": "iPhone"})
    assert qr.status_code == 201
    return pairing_id


def _state_burned(session_client: Client) -> str:
    _, pairing_id = issue(session_client)
    for _ in range(5):
        send(pairing_id, challenge(), WRONG)
    return pairing_id


def _state_without_key(session_client: Client) -> str:
    _, pairing_id = issue(session_client)
    PairingCode.objects.filter(id=pairing_id).update(proof_key_sealed=None)
    return pairing_id


def test_unknown_expired_used_and_burned_ids_answer_alike(session_client: Client) -> None:
    answers = []
    for state in (
        _state_unknown,
        _state_expired,
        _state_used,
        _state_burned,
        _state_without_key,
    ):
        pairing_id = state(session_client)
        nonce = challenge()
        app = fresh_client()
        with CaptureQueriesContext(connection) as queries:
            r = send(pairing_id, nonce, WRONG, app)
        answers.append((r.status_code, r.content, len(queries), r.get("Cache-Control")))

    assert answers[0][0] == 400
    assert json.loads(answers[0][1]) == INVALID_CODE
    assert all(answer == answers[0] for answer in answers), answers


def test_the_challenge_reveals_nothing_and_takes_no_input() -> None:
    first = post_json(fresh_client(), "/devices/pairing-challenges")
    second = post_json(fresh_client(), "/devices/pairing-challenges")

    assert first.status_code == second.status_code == 201
    assert set(first.json()) == {"nonce", "expires_at"}
    assert first.json()["nonce"] != second.json()["nonce"]


def test_the_challenge_is_rate_limited() -> None:
    app = fresh_client()
    for _ in range(20):
        assert post_json(app, "/devices/pairing-challenges").status_code == 201

    r = post_json(app, "/devices/pairing-challenges")

    assert r.status_code == 429
    assert r.json()["code"] == "too_many_attempts"
    assert post_json(fresh_client(), "/devices/pairing-challenges").status_code == 201


def test_proof_pairing_shares_the_pairing_throttle(session_client: Client) -> None:
    _, pairing_id = issue(session_client)
    app = fresh_client()
    for _ in range(6):
        r = post_json(app, "/devices/pair", {"code": "AAAA-AAAA", "device_name": "A"})
        assert r.status_code == 400
    for _ in range(4):
        assert send(pairing_id, challenge(), WRONG, app).status_code == 400

    r = send(pairing_id, challenge(), WRONG, app)

    assert r.status_code == 429
    assert PairingCode.objects.get(id=pairing_id).failed_proofs == 4


# --- What is stored, and for how long -----------------------------------------------------


def test_the_code_is_never_stored(user: User) -> None:
    pairing_code, code = create_pairing_code(user)
    key = derive_proof_key(code, pairing_code.id)
    secrets_ = [code, code.replace("-", ""), key.hex()]

    with connection.cursor() as c:
        for table in connection.introspection.table_names():
            c.execute(f'SELECT * FROM "{table}"')  # noqa: S608 - names from introspection
            for row in c.fetchall():
                for value in row:
                    raw = bytes(value) if isinstance(value, memoryview | bytes) else b""
                    assert key not in raw, table
                    assert not any(s in str(value) for s in secrets_), table

    sealed = sealed_key_of(pairing_code.id)
    assert key not in sealed
    assert proof.open_proof_key(sealed, pairing_code.id) == key


def test_the_stored_key_needs_the_master_key_and_its_own_row(
    user: User, settings: Settings
) -> None:
    first, _ = create_pairing_code(user)
    second, _ = create_pairing_code(UserFactory.create())
    sealed = sealed_key_of(first.id)

    with pytest.raises(crypto.DecryptionError):
        proof.open_proof_key(sealed, second.id)
    settings.BRIEVIE_MASTER_KEY = "6b" * 32
    with pytest.raises(crypto.DecryptionError):
        proof.open_proof_key(sealed, first.id)


def test_a_key_moved_to_another_row_proves_nothing(session_client: Client) -> None:
    code, first_id = issue(session_client)
    other = Client()
    other.force_login(UserFactory.create())
    _, second_id = issue(other)
    sealed = PairingCode.objects.get(id=first_id).proof_key_sealed
    PairingCode.objects.filter(id=second_id).update(proof_key_sealed=sealed)
    nonce = challenge()

    # A proof made with the first code, sent for the second id.
    r = send(
        second_id, nonce, pairing_proof(key_for(code, first_id), nonce=nonce, trust_binding=HOST)
    )

    assert r.status_code == 400
    assert Device.objects.count() == 0


def test_the_key_is_forgotten_when_the_code_is_used_by_qr(session_client: Client) -> None:
    pairing_id = _state_used(session_client)

    assert PairingCode.objects.get(id=pairing_id).proof_key_sealed is None


def test_expired_keys_and_challenges_are_forgotten() -> None:
    user = UserFactory.create()
    old, _ = create_pairing_code(user)
    PairingCode.objects.filter(id=old.id).update(expires_at=timezone.now() - timedelta(seconds=1))
    live, _ = create_pairing_code(UserFactory.create())
    stale = create_pairing_challenge()
    PairingChallenge.objects.filter(pk=stale.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    fresh = create_pairing_challenge()

    jobs.forget_expired_pairing_material(timestamp=0)

    assert PairingCode.objects.get(id=old.id).proof_key_sealed is None
    assert PairingCode.objects.get(id=live.id).proof_key_sealed is not None
    assert list(PairingChallenge.objects.values_list("pk", flat=True)) == [fresh.pk]


def test_forgetting_expired_material_is_periodic_with_a_queueing_lock() -> None:
    from procrastinate.contrib.django import app as procrastinate_app

    task = procrastinate_app.tasks["forget_expired_pairing_material"]
    assert task.queue == "pipeline"
    assert task.queueing_lock == "forget_expired_pairing_material"
    periodic = {
        entry.task.name: entry.cron
        for entry in procrastinate_app.periodic_registry.periodic_tasks.values()
    }
    assert periodic["forget_expired_pairing_material"] == "*/5 * * * *"


def test_old_challenges_are_removed_when_a_new_one_is_made() -> None:
    old = create_pairing_challenge()
    PairingChallenge.objects.filter(pk=old.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    kept = create_pairing_challenge()

    new = create_pairing_challenge()

    assert set(PairingChallenge.objects.values_list("pk", flat=True)) == {kept.pk, new.pk}


def test_a_new_code_forgets_expired_keys(user: User) -> None:
    old, _ = create_pairing_code(UserFactory.create())
    PairingCode.objects.filter(id=old.id).update(expires_at=timezone.now() - timedelta(seconds=1))

    create_pairing_code(user)

    assert PairingCode.objects.get(id=old.id).proof_key_sealed is None


def test_a_code_from_before_the_update_has_no_proof_path(session_client: Client) -> None:
    typed, pairing_id = issue(session_client)
    PairingCode.objects.filter(id=pairing_id).update(proof_key_sealed=None)
    nonce = challenge()

    r = send(pairing_id, nonce, prove(typed, pairing_id, nonce))

    assert r.json() == INVALID_CODE
    assert PairingCode.objects.get(id=pairing_id).failed_proofs == 0
    qr = post_json(fresh_client(), "/devices/pair", {"code": typed, "device_name": "iPhone"})
    assert qr.status_code == 201


# --- Nothing secret in logs, audit rows or answers ----------------------------------------


@pytest.fixture
def all_logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG)
    return caplog


def test_no_code_key_nonce_or_proof_in_logs_audit_or_answers(
    session_client: Client, all_logs: pytest.LogCaptureFixture
) -> None:
    code, pairing_id = issue(session_client)
    key_hex = key_for(code, pairing_id).hex()
    secrets_: list[str] = [code, code.replace("-", ""), key_hex]
    answers: list[bytes] = []
    for binding in ("attacker.example", HOST):
        nonce = challenge()
        proof_hex = prove(code, pairing_id, nonce, binding=binding)
        secrets_ += [nonce, proof_hex]
        answers.append(send(pairing_id, nonce, proof_hex).content)
    nonce = challenge()
    secrets_.append(nonce)
    answers.append(send(pairing_id, nonce, prove(code, pairing_id, nonce)).content)

    logged = "\n".join(
        f"{record.getMessage()} {record.args!r} {record.exc_text or ''}"
        for record in all_logs.records
    )
    audit = repr(list(ServerAuditEvent.objects.values()))
    for secret in secrets_:
        assert secret not in logged
        assert secret not in audit
        for answer in answers:
            assert secret.encode() not in answer


# --- Both flows, one code -----------------------------------------------------------------


def test_a_1c_app_still_pairs_by_qr_with_the_code(session_client: Client) -> None:
    code, _ = issue(session_client)

    r = post_json(fresh_client(), "/devices/pair", {"code": code, "device_name": "iPhone"})

    assert r.status_code == 201
    assert set(r.json()) == {"device_id", "token", "user"}
    assert r["Cache-Control"] == "no-store"
    [row] = ServerAuditEvent.objects.filter(code="device_paired")
    assert row.values == {"via": "code"}


def test_a_code_used_by_proof_no_longer_pairs_by_qr(session_client: Client) -> None:
    code, _, r = pair_by_proof(session_client)
    assert r.status_code == 201

    qr = post_json(fresh_client(), "/devices/pair", {"code": code, "device_name": "iPhone"})

    assert qr.status_code == 400
    assert Device.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_qr_and_proof_at_once_give_one_device(settings: Settings) -> None:
    settings.BRIEVIE_PUBLIC_URLS = [f"https://{HOST}"]
    pairing_code, code = create_pairing_code(UserFactory.create())
    pairing_id = str(pairing_code.id)
    nonce = create_pairing_challenge().nonce
    proof_hex = prove(code, pairing_id, nonce)
    barrier = threading.Barrier(2)
    statuses: list[int] = []

    def by_qr() -> None:
        try:
            barrier.wait(timeout=10)
            r = post_json(
                Client(REMOTE_ADDR="192.0.2.1"),
                "/devices/pair",
                {"code": code, "device_name": "QR"},
            )
            statuses.append(r.status_code)
        finally:
            connection.close()

    def by_proof() -> None:
        try:
            barrier.wait(timeout=10)
            r = send(pairing_id, nonce, proof_hex, Client(REMOTE_ADDR="192.0.2.2"))
            statuses.append(r.status_code)
        finally:
            connection.close()

    threads = [threading.Thread(target=by_qr), threading.Thread(target=by_proof)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(statuses) == [201, 400]
    assert Device.objects.count() == 1
