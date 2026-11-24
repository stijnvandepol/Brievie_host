"""The public route (tunnel, own reverse proxy) is app-only unless BRIEVIE_PUBLIC_WEB (S 7.6)."""

import pytest
from django.test import Client
from pytest_django import Settings

from apps.access.middleware import (
    APP_OPERATIONS,
    open_on_public_route,
    operation_for,
    operations,
)
from brievie.api import api
from tests.factories import UserFactory
from tests.members_support import as_user, bearer, paired

PUBLIC = {"X-Brievie-Route": "public"}
PRIVATE = {"X-Brievie-Route": "private"}
SENTENCE = "Op dit adres werkt alleen de Brievie-app. Gebruik de webinterface thuis of via VPN."

CADDY_PEERS = {"127.0.0.1"}


@pytest.fixture(autouse=True)
def _caddy_at(monkeypatch: pytest.MonkeyPatch) -> None:
    """Caddy is the peer of these requests (apps.common.net.from_caddy)."""
    from apps.common import net

    monkeypatch.setattr(net, "caddy_addresses", lambda **_kwargs: frozenset(CADDY_PEERS))


@pytest.fixture(autouse=True)
def _behind_caddy(settings: Settings) -> None:
    settings.BRIEVIE_BEHIND_CADDY = True
    settings.BRIEVIE_PUBLIC_WEB = False


def assert_refused(response: object) -> None:
    assert response.status_code == 403  # type: ignore[attr-defined]
    body = response.json()  # type: ignore[attr-defined]
    assert body == {"code": "public_web_off", "message": SENTENCE}


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", "/api/v1/auth/login", {"email": "a@example.org", "password": "x" * 12}),
        ("get", "/api/v1/setup", None),
        ("post", "/api/v1/auth/password-reset", {"email": "a@example.org"}),
        ("get", "/api/v1/invitations/abc", None),
        ("get", "/api/v1/server/audit-events", None),
    ],
)
def test_public_route_refuses_login_setup_reset_and_invitation(
    method: str, path: str, payload: dict[str, str] | None
) -> None:
    client = Client()
    if method == "post":
        response = client.post(path, payload, content_type="application/json", headers=PUBLIC)
    else:
        response = client.get(path, headers=PUBLIC)
    assert_refused(response)


def test_public_route_refuses_a_session_cookie_on_every_session_endpoint() -> None:
    client = as_user(UserFactory.create(is_server_admin=True))
    assert client.get("/api/v1/me", headers=PRIVATE).status_code == 200
    # An operation that takes the device token reaches its own auth, which refuses the
    # session there: 401 as for nobody.
    for path in ("/api/v1/me", "/api/v1/devices", "/api/v1/workspaces"):
        response = client.get(path, headers=PUBLIC)
        assert response.status_code == 401, path
        assert response.json()["code"] == "not_authenticated"
    # A session-only operation never gets that far.
    assert_refused(client.get("/api/v1/server/audit-events", headers=PUBLIC))
    assert_refused(client.get("/api/v1/server/external-access", headers=PUBLIC))


def test_a_session_cookie_next_to_a_bearer_header_is_still_no_session() -> None:
    # The gate lets a Bearer request through to the endpoint's own auth; a session-only
    # endpoint then refuses the session that came along on the public route.
    user = UserFactory.create()
    client = as_user(user)
    _, token = paired(user)
    response = client.post(
        "/api/v1/devices/pairing-codes",
        {},
        content_type="application/json",
        headers={**PUBLIC, **bearer(token)},
    )
    assert response.status_code in (401, 403)
    assert response.json()["code"] in ("not_authenticated", "public_web_off")


def test_public_route_allows_device_token_calls() -> None:
    user = UserFactory.create()
    _, token = paired(user)
    response = Client().get("/api/v1/me", headers={**PUBLIC, **bearer(token)})
    assert response.status_code == 200
    assert response.json()["email"] == user.email


def test_a_wrong_device_token_gets_the_normal_401() -> None:
    response = Client().get("/api/v1/me", headers={**PUBLIC, "Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_public_route_allows_health_and_the_ca_but_no_pairing() -> None:
    # Security review of PR #53, point 5: the first pairing happens over a CA-pinned
    # connection only, never through the party that ends TLS for the public name.
    client = Client()
    assert client.get("/api/v1/health", headers=PUBLIC).status_code == 200
    ca = client.get("/api/v1/server/ca-certificate", headers=PUBLIC)
    assert ca.status_code in (200, 404)
    assert ca.status_code == 404 or "pem" in ca.json()
    for path, body in (
        ("/api/v1/devices/pairing-challenges", {}),
        ("/api/v1/devices/pair", {"code": "ABCD-EFGH", "device_name": "iPhone"}),
        ("/api/v1/devices/pair-with-proof", {}),
    ):
        response = client.post(path, body, content_type="application/json", headers=PUBLIC)
        assert response.status_code == 403
        assert response.json()["code"] == "pair_at_home"


def test_server_addresses_are_closed_on_the_public_route() -> None:
    user = UserFactory.create()
    _, token = paired(user)
    response = Client().get("/api/v1/server/addresses", headers={**PUBLIC, **bearer(token)})
    assert response.status_code == 403
    assert response.json()["code"] == "pair_at_home"
    assert (
        Client().get("/api/v1/server/addresses", headers={**PRIVATE, **bearer(token)}).status_code
        == 200
    )


def test_public_route_web_page_gets_the_html_sentence() -> None:
    response = Client().get("/instellingen", headers=PUBLIC)
    assert response.status_code == 403
    assert response["Content-Type"].startswith("text/html")
    assert SENTENCE in response.content.decode()
    assert response["Cache-Control"] == "no-store"


def test_the_html_sentence_follows_the_browser_language() -> None:
    response = Client().get("/", headers={**PUBLIC, "Accept-Language": "en"})
    assert response.status_code == 403
    page = response.content.decode()
    assert "Only the Brievie app works at this address." in page
    assert 'lang="en"' in page


def test_private_route_is_unchanged() -> None:
    client = Client()
    response = client.get("/api/v1/me", headers=PRIVATE)
    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"


def test_public_web_true_opens_everything(settings: Settings) -> None:
    settings.BRIEVIE_PUBLIC_WEB = True
    client = as_user(UserFactory.create())
    assert client.get("/api/v1/me", headers=PUBLIC).status_code == 200
    assert Client().get("/api/v1/me", headers=PUBLIC).json()["code"] == "not_authenticated"


def test_not_behind_caddy_there_is_no_public_route(settings: Settings) -> None:
    settings.BRIEVIE_BEHIND_CADDY = False
    client = as_user(UserFactory.create())
    assert client.get("/api/v1/me", headers=PUBLIC).status_code == 200


# Every operation open on the public route: the app's unauthenticated ones and those that
# take the device token. A new operation changes this list only on purpose: classify it.
OPEN_ON_PUBLIC_ROUTE = frozenset(
    {
        "acceptNewPersonSuggestion",
        "acceptSuggestion",
        "applyLabelPreset",
        "assignTask",
        "completeTask",
        "confirmDocument",
        "countLetterStatuses",
        "countMyTasks",
        "createDocument",
        "createFolder",
        "createLabel",
        "createPerson",
        "createRule",
        "deleteLabel",
        "deletePerson",
        "deleteRule",
        "dismissDuplicate",
        "dismissNewPersonSuggestion",
        "dismissSuggestion",
        "getAiAutopilot",
        "getAiStatus",
        "getCaCertificate",
        "getDocument",
        "getDocumentAccess",
        "getDocumentPage",
        "getDuplicate",
        "getHealth",
        "getInboxCounts",
        "getMe",
        "getMenuCounts",
        "getMyPostAccess",
        "getVisibility",
        "handleDocument",
        "listAiSuppliers",
        "listDevices",
        "listDocumentEvents",
        "listDocuments",
        "listDuplicates",
        "listFolders",
        "listLabels",
        "listLingeringLetters",
        "listMembers",
        "listMyTasks",
        "listPersons",
        "listRuleTemplates",
        "listRules",
        "listTaskAssignees",
        "listTasks",
        "listTrash",
        "listWorkspaces",
        "markDocumentRead",
        "markDocumentUnread",
        "markDocumentsRead",
        "mergeDuplicate",
        "reopenDocument",
        "reopenTask",
        "reorderRules",
        "restoreDocument",
        "retryDocumentProcessing",
        "revokeDevice",
        "runRule",
        "searchDocuments",
        "setDocumentAddressees",
        "setDocumentLabels",
        "setDocumentVisibility",
        "setTaskDeadline",
        "setTaskWaiting",
        "snoozeTask",
        "testRule",
        "trashDocument",
        "undoDocumentEvent",
        "updateLabel",
        "updateMyLanguage",
        "updatePerson",
        "updateRule",
    }
)


def test_every_operation_is_classified_for_the_public_route() -> None:
    found = {op.operation_id for op in operations(api) if open_on_public_route(op, api)}
    assert found == OPEN_ON_PUBLIC_ROUTE


def test_app_operations_exist_and_need_no_sign_in() -> None:
    by_id = {op.operation_id: op for op in operations(api)}
    for operation_id in APP_OPERATIONS:
        assert by_id[operation_id].auth_param is None, operation_id


@pytest.mark.parametrize(
    "operation_id",
    [
        "login",
        "setupServer",
        "createPasswordReset",
        "reauthenticate",
        "acceptInvitation",
        "createPairingCode",
        "listServerAuditEvents",
        "updateAiSettings",
    ],
)
def test_sign_in_and_session_operations_stay_closed(operation_id: str) -> None:
    by_id = {op.operation_id: op for op in operations(api)}
    assert not open_on_public_route(by_id[operation_id], api)


def test_operation_for_finds_the_operation_by_path_and_method() -> None:
    from django.test import RequestFactory

    assert operation_for(RequestFactory().get("/api/v1/me")).operation_id == "getMe"  # type: ignore[union-attr]
    assert operation_for(RequestFactory().post("/api/v1/auth/login")).operation_id == "login"  # type: ignore[union-attr]
    assert operation_for(RequestFactory().delete("/api/v1/auth/login")) is None
    assert operation_for(RequestFactory().get("/api/v1/nothing-here")) is None


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/v1/auth/login"),
        ("post", "/api/v1/setup"),
        ("post", "/api/v1/auth/password-reset"),
        ("post", "/api/v1/auth/reauthenticate"),
        ("get", "/api/v1/server/audit-events"),
        ("post", "/api/v1/devices/pairing-codes"),
        ("get", "/api/v1/nothing-here"),
        ("get", "/assets/index.js"),
    ],
)
def test_a_junk_bearer_header_opens_nothing(method: str, path: str) -> None:
    headers = {**PUBLIC, "Authorization": "Bearer junk"}
    client = Client()
    if method == "post":
        response = client.post(path, {}, content_type="application/json", headers=headers)
    else:
        response = client.get(path, headers=headers)
    assert response.status_code == 403
    if path.startswith("/api/"):
        assert response.json()["code"] == "public_web_off"


def test_forged_private_header_cannot_reach_the_gate_bypass() -> None:
    # Caddy overwrites X-Brievie-Route on every request (deploy/Caddyfile, to_api); a
    # request that reached the api without Caddy's mark, or with any other value than
    # exactly "private", is public: the most restricted route.
    client = as_user(UserFactory.create())
    for headers in ({}, {"X-Brievie-Route": "Private"}, {"X-Brievie-Route": "private, public"}):
        assert_refused(
            client.post("/api/v1/auth/login", {}, content_type="application/json", headers=headers)
        )
        assert client.get("/api/v1/me", headers=headers).status_code == 401


# --- What the gate remembers for the settings page (security review of PR #53) ---------


@pytest.fixture
def fresh_notes() -> None:
    from apps.access import observed

    observed.forget_written()


@pytest.mark.usefixtures("fresh_notes")
def test_a_public_name_request_from_an_untrusted_source_is_noted_with_its_local_peer() -> None:
    from apps.access import observed

    headers = {
        "X-Brievie-Route": "public",
        "X-Brievie-Via": "public_name",
        "X-Brievie-Peer-IP": "192.168.1.5",
    }
    assert_refused(
        Client().post("/api/v1/auth/login", {}, content_type="application/json", headers=headers)
    )
    assert observed.last_seen("public_name_untrusted") is not None
    assert observed.last_peer("public_name_untrusted") == "192.168.1.5"


@pytest.mark.usefixtures("fresh_notes")
def test_an_internet_peer_is_never_remembered() -> None:
    from apps.access import observed

    headers = {
        "X-Brievie-Route": "public",
        "X-Brievie-Via": "public_name",
        "X-Brievie-Peer-IP": "203.0.113.9",
    }
    Client().get("/api/v1/health", headers=headers)
    assert observed.last_seen("public_name_untrusted") is not None
    assert observed.last_peer("public_name_untrusted") == ""


@pytest.mark.usefixtures("fresh_notes")
def test_cloudflare_on_the_https_port_is_noted_and_shown_to_the_admin(settings: Settings) -> None:
    Client().get(
        "/api/v1/health", headers={"X-Brievie-Route": "public", "X-Brievie-Via": "cloudflare"}
    )
    admin = as_user(UserFactory.create(is_server_admin=True))
    body = admin.get("/api/v1/server/external-access", headers=PRIVATE).json()
    assert {"code": "cloudflare_on_https_port", "level": "problem"} in body["checks"]


@pytest.mark.usefixtures("fresh_notes")
def test_an_unknown_via_value_notes_nothing() -> None:
    from apps.access import observed

    Client().get(
        "/api/v1/health", headers={"X-Brievie-Route": "public", "X-Brievie-Via": "nonsense"}
    )
    assert observed.last_seen("cloudflare_on_https_port") is None


@pytest.mark.usefixtures("fresh_notes")
def test_a_proxy_without_a_forwarded_client_and_a_wrong_host_are_noted(settings: Settings) -> None:
    from apps.access import observed

    settings.BRIEVIE_PUBLIC_HOSTNAME = "brievie.example.nl"
    settings.ALLOWED_HOSTS = ["testserver", "brievie.example.nl", "192.168.1.20"]
    headers = {
        "X-Brievie-Route": "public",
        "X-Brievie-Via": "proxy",
        "X-Brievie-Peer-IP": "172.18.0.1",
        "X-Brievie-Client-IP": "172.18.0.1",
    }
    Client().get("/api/v1/health", headers={**headers, "Host": "192.168.1.20"})
    assert observed.last_seen("proxy_without_forwarding") is not None
    assert observed.last_seen("host_not_forwarded") is not None


@pytest.mark.usefixtures("fresh_notes")
def test_a_well_set_proxy_notes_nothing(settings: Settings) -> None:
    from apps.access import observed

    settings.BRIEVIE_PUBLIC_HOSTNAME = "brievie.example.nl"
    settings.ALLOWED_HOSTS = ["testserver", "brievie.example.nl"]
    headers = {
        "X-Brievie-Route": "public",
        "X-Brievie-Via": "proxy",
        "X-Brievie-Peer-IP": "192.168.1.5",
        "X-Brievie-Client-IP": "203.0.113.9",
        "Host": "brievie.example.nl",
    }
    Client().get("/api/v1/health", headers=headers)
    assert observed.last_seen("proxy_without_forwarding") is None
    assert observed.last_seen("host_not_forwarded") is None


def test_pairing_and_addresses_stay_closed_with_the_public_web_on(settings: Settings) -> None:
    settings.BRIEVIE_PUBLIC_WEB = True
    assert_refused(
        Client().post(
            "/api/v1/devices/pairing-challenges",
            {},
            content_type="application/json",
            headers=PUBLIC,
        )
    )
    user = UserFactory.create()
    _, token = paired(user)
    assert_refused(Client().get("/api/v1/server/addresses", headers={**PUBLIC, **bearer(token)}))
    assert Client().get("/api/v1/health", headers=PUBLIC).status_code == 200
