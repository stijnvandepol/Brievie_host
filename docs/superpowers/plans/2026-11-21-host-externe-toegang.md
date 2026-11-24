# Host "Van buiten bereikbaar" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Brievie is reachable from outside the home network by VPN (docs, plus an optional second private name), Cloudflare Tunnel (an optional `cloudflared` service under the compose profile `tunnel`) and an own reverse proxy, all configured in `deploy/.env`; client address, `is_secure()`, HSTS and limits are right per way; the web sign-in is not on the internet unless the admin says so; an admin-only page "Van buiten bereikbaar" shows what is set and what fails; the iPhone app trusts each address the way it is served.

**Architecture:** Caddy is the only door and the only judge of the route: it serves the home, VPN and proxy names on `:443` (`tls internal`) and the tunnel on an unpublished `http://:8080`, and always writes `X-Brievie-Route`, `X-Brievie-Client-IP` and `X-Brievie-Peer-IP` itself. Django derives addresses in a pure module (`apps/access/addresses.py`), reads the route and client address only from Caddy's headers (`apps/access/route.py`, `apps/common/net.py`), gates the public route (`apps/access/middleware.py`), and reports status (`apps/access/status.py`, `getExternalAccess`). Devices learn which addresses carry a public certificate (`PairOut.public_urls`, `getServerAddresses`).

**Tech Stack:** Python 3.13, Django 6.1, Django Ninja, pytest; Caddy 2.11; docker compose v2 with profiles; `cloudflare/cloudflared` (new image); React 19, TanStack Router/Query, React Aria Components, Lingui, Vitest, MSW; Swift 6 (iOS tasks).

**Spec:** `docs/superpowers/specs/2026-11-21-externe-toegang-design.md`, cited "S 7.3" etc. Two-step sign-in: `docs/superpowers/specs/2026-11-20-tweestapsverificatie-design.md` (branch `docs/intake-and-2fa-design`, not built).

## Global Constraints

From `CLAUDE.md`:

- "`mypy --strict` and `ruff check` clean. No `Any`, no bare `except`."
- "User-facing text exists in Dutch (B1, the source) and English: in the backend always through `gettext`, in `web/` always through Lingui (`t` macros). A new string needs both" (`make messages`, `pnpm extract`; terms from `docs/GLOSSARY.md`).
- "API only under `/api/v1`, additive changes only. Every endpoint: typed schemas and an explicit camelCase `operation_id`." "Every non-2xx body: `{"code": ..., "message": ...}` via `apps.common.errors.ApiError`." "Thin endpoints".
- "A field added to an existing API schema has a default"; "Nullable fields in API schemas are primitives only ... the contract test forbids `anyOf`."
- "Endpoints that administer people ... workspace settings, the log ... use `auth=session_auth`: a device token gets 401." `getExternalAccess` is session-only and server-admin-only.
- "No document content, tokens, passwords or BSNs in logs." No log line, response or process argument holds `BRIEVIE_TUNNEL_TOKEN`; the api container never receives it.
- "No new dependency without a line in `docs/DECISIONS.md`." The `cloudflared` image gets one (Task 4).
- "No Redis, no Celery, no Node runtime container." `cloudflared` is a Go binary image, optional, under a profile.
- "Tests first for logic. Conventional commits." Every commit ends with the trailer line exactly `Co-Authored-By: Claude <noreply@anthropic.com>`.

From the environment:

- **Nobody runs Docker on this Mac.** Never `docker`, `./brievie`, `make dev`, `make test`; never connect to a local PostgreSQL; never touch Docker project `brievie`. Backend tests, compose and Caddyfile changes are verified by CI (`backend`, `stack`, `image`, `dast`, `update`, `restore` jobs) after a push of the feature branch (draft PR only after the owner's yes). `make lint`, `make typecheck` and every `pnpm` command run locally. Wrapper tests use the fake docker of `backend/tests/wrapper_support.py` (run in CI's `backend` job).
- Branch `feat/external-access` from `main`. If git reports a pack file "far too short", run `brctl download <file>`; never delete anything under `.git`. Never commit files whose names end in " 2" or " 3".

From the spec, not left to the builder:

- No mode variable: ways are derived (S 5.5). New variables exactly: `BRIEVIE_VPN_HOSTNAME`, `BRIEVIE_PUBLIC_HOSTNAME`, `BRIEVIE_TUNNEL_TOKEN`, `BRIEVIE_TRUSTED_PROXIES`, `BRIEVIE_PUBLIC_WEB` (default `false`); internal: `BRIEVIE_TUNNEL_TOKEN_SET`, `BRIEVIE_BEHIND_CADDY`.
- Tunnel hop: `http://caddy:8080`, never published; Caddy forces `X-Forwarded-Proto: https` and `Host: {$BRIEVIE_PUBLIC_HOSTNAME}` there (S 7.2).
- Django never reads `CF-Connecting-IP` (S 7.3). HSTS is Caddy's alone; the api keeps `BRIEVIE_HSTS_SECONDS` 0 (S 5.6).
- Public route with `BRIEVIE_PUBLIC_WEB=false`: only Bearer requests and the app's unauthenticated paths (S 7.6).
- Copy in the spec is used verbatim; sentences this plan adds are marked "(plan)".

## Review Focus

1. **No header from outside decides anything.** `X-Brievie-*` are overwritten by Caddy on every `reverse_proxy`; `CF-Connecting-IP` is read only by Caddy on `:8080`; `:8080` is in no `ports:`. → Tasks 2, 3, 13.
2. **The token stays in one place.** Only the `cloudflared` service; never the api, a log, an argv, a response. → Tasks 4, 9, 11.
3. **The public route is closed by default.** With `BRIEVIE_PUBLIC_WEB=false` no session can be made or used there; device tokens and pairing work. → Task 5, Task 13 (CI forged-route check).
4. **Old apps and contracts.** Additive fields with defaults, QR payload v1 unchanged, `openapi_1b/1c` fixtures still pass. → Task 7.
5. **The home installation is unchanged.** Same answers, same HSTS 0, same client address, same start. → Tasks 1–3, 11, 13.

---

## File structure

```
backend/apps/access/            # NEW app (no models, no migrations)
  __init__.py  apps.py
  addresses.py   # pure: Addresses, derive(), is_bare_host()
  route.py       # is_public_route(request), peer_ip(request)
  middleware.py  # PublicRouteGate
  status.py      # external_access_status(request) -> ExternalAccess (dataclasses), tunnel probe
  schemas.py     # ExternalAccessOut, AddressOut, TunnelOut, SeenAsOut, CheckOut, ServerAddressesOut
  api.py         # getExternalAccess, getServerAddresses
backend/apps/common/net.py      # client_ip reads X-Brievie-Client-IP behind Caddy
backend/apps/common/checks.py   # + E002, W002, W003
backend/apps/common/links.py    # web addresses only
backend/apps/accounts/auth.py   # SessionAuth refuses on the public route with public web off
backend/apps/devices/schemas.py # PairOut.public_urls
backend/apps/devices/services.py
backend/brievie/settings.py     # derive addresses, new settings, middleware, INSTALLED_APPS
backend/brievie/urls.py (or the api router module)  # mount access router
backend/tests/test_access_addresses.py  test_access_route.py  test_access_gate.py
              test_access_status.py  test_access_api.py  test_common_net.py (extended)
              test_deploy_files.py  test_deploy_caddyfile.py  test_brievie_wrapper.py (extended)
              test_system_checks.py (extended)  test_common_links.py (extended)
deploy/Caddyfile  deploy/docker-compose.yml  deploy/.env.example
brievie                          # wrapper: tunnel profile, name checks, messages
.github/workflows/ci.yml         # stack + image steps
web/src/routes/server-external-access.tsx (+ .test.tsx), nav link, locales
docs/DECISIONS.md  docs/SELFHOST.md  docs/PRIVACY.md  docs/GLOSSARY.md  docs/SPEC.md  CHANGELOG.md
```

---

### Task 1: Addresses derived in one pure function

**Files:** create `backend/apps/access/{__init__,apps,addresses}.py`, `backend/tests/test_access_addresses.py`; modify `backend/brievie/settings.py`, `backend/apps/common/checks.py`, `deploy/docker-compose.yml` (api env + healthcheck), `backend/tests/test_deploy_files.py` (rewrite `test_allowed_hosts_and_public_urls_follow_the_hostname`), `backend/tests/test_system_checks.py`.

**Interfaces:**

```python
@dataclass(frozen=True)
class Addresses:
    allowed_hosts: tuple[str, ...]
    public_urls: tuple[str, ...]          # home, vpn, public (in that order)
    public_certificate_urls: tuple[str, ...]  # the public-hostname URL(s)

def derive(*, hostname: str, vpn_hostname: str, public_hostname: str, https_port: int) -> Addresses
def is_bare_host(value: str) -> bool      # letters, digits, dots, dashes; or an IP address
```

Settings: `BRIEVIE_HOSTNAME`, `BRIEVIE_VPN_HOSTNAME`, `BRIEVIE_PUBLIC_HOSTNAME`, `BRIEVIE_HTTPS_PORT` read with defaults `"localhost"`, `""`, `""`, `443`; `ALLOWED_HOSTS` = env list if non-empty else `derive(...).allowed_hosts`; same for `BRIEVIE_PUBLIC_URLS`; `BRIEVIE_PUBLIC_CERTIFICATE_URLS` = the public-certificate URLs present in `BRIEVIE_PUBLIC_URLS`.

- [ ] Write failing tests: `test_home_only_gives_one_host_and_url`, `test_port_443_is_left_out_and_others_kept`, `test_vpn_name_comes_second_with_the_https_port`, `test_public_name_comes_last_without_port`, `test_ipv6_host_gets_brackets_in_the_url`, `test_is_bare_host_refuses_scheme_path_port_and_spaces`, `test_explicit_allowed_hosts_and_public_urls_still_win` (settings reload with env), `test_e002_when_public_name_equals_home_name`, `test_e002_when_vpn_name_is_not_a_bare_host`, `test_e001_still_reported`.
- [ ] Implement `addresses.py` (no Django import; a source test asserts `import django` absent), wire settings, add `brievie.E002` (Dutch hint via gettext).
- [ ] Compose api env: `BRIEVIE_HOSTNAME`, `BRIEVIE_VPN_HOSTNAME: ${BRIEVIE_VPN_HOSTNAME:-}`, `BRIEVIE_PUBLIC_HOSTNAME: ${BRIEVIE_PUBLIC_HOSTNAME:-}`, `BRIEVIE_HTTPS_PORT: ${BRIEVIE_HTTPS_PORT:-443}`, `ALLOWED_HOSTS: ${ALLOWED_HOSTS:-}`, `BRIEVIE_PUBLIC_URLS: ${BRIEVIE_PUBLIC_URLS:-}`. Healthcheck: `h = (os.environ.get('ALLOWED_HOSTS') or os.environ['BRIEVIE_HOSTNAME']).split(',')[0]...`.
- [ ] Rewrite `test_allowed_hosts_and_public_urls_follow_the_hostname` → `test_api_gets_the_names_and_derives_the_addresses` (asserts the env lines above and the healthcheck fallback); keep `test_env_example_needs_only_the_hostname`.
- [ ] `make lint`, `make typecheck`; commit `feat(access): derive hosts and public URLs from up to three names`.

### Task 2: Route and client address from Caddy's headers only

**Files:** create `backend/apps/access/route.py`, `backend/tests/test_access_route.py`; modify `backend/apps/common/net.py`, `backend/tests/test_common_net.py`, `backend/brievie/settings.py`.

**Interfaces:**

```python
# settings
BRIEVIE_BEHIND_CADDY: bool = env.bool("BRIEVIE_BEHIND_CADDY", default=False)
# apps.common.net
def client_ip(request) -> str | None   # behind Caddy: X-Brievie-Client-IP if present, else today's logic
# apps.access.route
def is_public_route(request) -> bool   # behind Caddy: header != "private" (missing = public); else False
def peer_ip(request) -> str            # behind Caddy: HTTP_X_BRIEVIE_PEER_IP, else REMOTE_ADDR
```

(The META key is `HTTP_X_BRIEVIE_CLIENT_IP`.)

- [ ] Failing tests: `test_cf_connecting_ip_never_counts` (parametrised over behind-Caddy true/false, proxy count 0/1, with and without `X-Brievie-Client-IP`), `test_behind_caddy_the_caddy_header_counts`, `test_not_behind_caddy_the_caddy_header_is_ignored`, `test_behind_caddy_without_header_falls_back_to_forwarded_for`, `test_missing_route_header_counts_as_public`, `test_route_header_ignored_when_not_behind_caddy`, `test_axes_and_throttles_use_the_new_address` (a request with the header through `AXES_CLIENT_IP_CALLABLE` and `address_bucket`).
- [ ] Implement; keep `BRIEVIE_PROXY_COUNT` semantics untouched for `BRIEVIE_BEHIND_CADDY=false`.
- [ ] Commit `feat(access): read route and client address only from Caddy's own headers`.

### Task 3: Caddyfile: two listeners, edge headers, HSTS per route

**Files:** modify `deploy/Caddyfile`, `backend/tests/test_deploy_caddyfile.py`, `backend/tests/test_deploy_files.py`.

Sketch (CI `stack` is the judge of syntax; adjust there, keep the properties the tests assert):

```
{
	skip_install_trust
	default_sni {$BRIEVIE_HOSTNAME}
	servers :443 {
		trusted_proxies static {$BRIEVIE_TRUSTED_PROXIES}
		trusted_proxies_strict
	}
	servers :8080 {
		trusted_proxies static private_ranges
		client_ip_headers CF-Connecting-IP
	}
}

(security_headers) {
	header {
		Strict-Transport-Security "max-age={vars.brievie_hsts}"
		# … unchanged lines …
		-Server
	}
}

(to_api) {
	reverse_proxy api:8000 {
		header_up X-Brievie-Route {vars.brievie_route}
		header_up X-Brievie-Client-IP {client_ip}
		header_up X-Brievie-Peer-IP {remote_host}
		header_up Host {vars.brievie_host}
	}
}

(brievie_routes) {
	import security_headers
	encode zstd gzip
	@documentUpload { … unchanged … }
	handle @documentUpload { request_body { max_size {$BRIEVIE_MAX_UPLOAD_BYTES} }  import to_api }
	handle { request_body { max_size {$BRIEVIE_MAX_BODY_BYTES} }  import to_api }
	handle_errors 413 { import security_headers … unchanged respond … }
	handle_errors 502 503 504 { import security_headers … unchanged respond … }
}

{$BRIEVIE_HOSTNAME} {$BRIEVIE_VPN_HOSTNAME} {$BRIEVIE_PUBLIC_HOSTNAME} {
	tls internal { protocols tls1.2 tls1.3 }
	vars brievie_route private
	vars brievie_hsts {$BRIEVIE_HSTS_SECONDS}
	# With its port: a home install on BRIEVIE_HTTPS_PORT=8443 must keep Host == Origin (CSRF).
	vars brievie_host {http.request.hostport}
	@via_proxy remote_ip {$BRIEVIE_TRUSTED_PROXIES}
	vars @via_proxy brievie_route public
	vars @via_proxy brievie_hsts 31536000
	import brievie_routes
}

# The tunnel: only cloudflared on the compose network reaches this; never published.
http://:8080 {
	vars brievie_route public
	vars brievie_hsts 31536000
	vars brievie_host {$BRIEVIE_PUBLIC_HOSTNAME}
	@plain header X-Forwarded-Proto http
	redir @plain https://{$BRIEVIE_PUBLIC_HOSTNAME}{uri} 308
	request_header X-Forwarded-Proto https
	import brievie_routes
}
```

- [ ] Failing text tests (new or rewritten, named): `test_every_proxy_to_the_api_sets_the_three_edge_headers` (one `to_api` snippet, every `reverse_proxy` in it), `test_cf_connecting_ip_is_read_only_on_the_tunnel_listener`, `test_tunnel_listener_forces_https_and_the_public_host`, `test_trusted_proxies_only_from_the_variable_on_443_and_private_ranges_on_8080`, `test_hsts_comes_from_the_route_var` (rewrite of the HSTS part of the snippet test), `test_error_responses_carry_the_security_headers` (rewritten: `import security_headers` count is now 3 in one snippet, `handle_errors` count 2, both inside `brievie_routes`), `test_caddy_replaces_a_client_sent_forwarded_for` → rewritten as `test_forwarded_for_is_trusted_only_from_configured_proxies` (no `header_up X-Forwarded-For`; `trusted_proxies` only in the two `servers` blocks), `test_document_upload_has_its_own_body_limit` (unchanged, must still pass), `test_8080_is_never_published` (not in any `ports:`), `test_the_home_route_keeps_the_host_with_its_port` (`:443` passes `{http.request.hostport}`; only `:8080` overrides Host).
- [ ] Rewrite the Caddyfile per the sketch; keep the header comments; keep `protocols tls1.2 tls1.3`.
- [ ] Commit `feat(deploy): serve the tunnel and proxy routes with their own trust and HSTS`.

### Task 4: Compose: cloudflared under the `tunnel` profile, new variables

**Files:** modify `deploy/docker-compose.yml`, `deploy/.env.example`, `backend/tests/test_deploy_files.py`, `docs/DECISIONS.md`.

- [ ] Failing tests: `test_cloudflared_is_optional_behind_the_tunnel_profile`, `test_cloudflared_is_pinned_by_version_and_digest` (no `latest`), `test_the_tunnel_token_reaches_only_cloudflared` (the token variable appears in exactly one service's text and not in the Caddyfile; api has `BRIEVIE_TUNNEL_TOKEN_SET: ${BRIEVIE_TUNNEL_TOKEN:+true}`), `test_the_token_is_never_required_by_interpolation` (no `BRIEVIE_TUNNEL_TOKEN:?`), `test_cloudflared_is_locked_down` (`read_only: true`, `cap_drop`, `no-new-privileges`, no `ports:`, `--no-autoupdate`, `--metrics 0.0.0.0:2000`), `test_caddy_gets_the_route_variables` (`BRIEVIE_VPN_HOSTNAME`, `BRIEVIE_PUBLIC_HOSTNAME`, `BRIEVIE_TRUSTED_PROXIES: ${BRIEVIE_TRUSTED_PROXIES:-127.0.0.1/32}`), `test_api_is_behind_caddy` (`BRIEVIE_BEHIND_CADDY: "true"`, `BRIEVIE_PUBLIC_WEB`, `BRIEVIE_TRUSTED_PROXIES`), `test_env_example_documents_every_new_variable` (commented lines for the five new variables and `COMPOSE_PROFILES`).
- [ ] Add the service:

```yaml
  # Optional: Cloudflare Tunnel (docs: "Van buiten bereikbaar"). Started only with the
  # profile `tunnel` (./brievie does that when BRIEVIE_TUNNEL_TOKEN is set). The token
  # reaches this container only. It forwards to Caddy's unpublished tunnel listener.
  cloudflared:
    image: cloudflare/cloudflared:<pinned version>@sha256:<digest>
    profiles: ["tunnel"]
    restart: unless-stopped
    depends_on:
      caddy:
        condition: service_started
    command: ["tunnel", "--no-autoupdate", "--metrics", "127.0.0.1:2000", "run"]  # loopback only (review of PR #53)
    environment:
      TUNNEL_TOKEN: ${BRIEVIE_TUNNEL_TOKEN:-}
    read_only: true
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
```

  Pin the newest release that is at least two weeks old on the day of building.
- [ ] `.env.example`: a block "Van buiten bereikbaar" with the five variables commented out, each with one English line and the S 7.7 privacy sentence above `BRIEVIE_TUNNEL_TOKEN`; `COMPOSE_PROFILES=tunnel` commented with "only without ./brievie".
- [ ] DECISIONS: "2026-11-2x: External access" with: compose decides, the page shows (orchestrator); `cloudflare/cloudflared` image (why: the official, maintained connector; alternative: none for Cloudflare Tunnel, Tailscale/WireGuard need no container); plain-HTTP tunnel hop (S 7.2); client address via Caddy (S 7.3); `BRIEVIE_PUBLIC_WEB` default false (S 7.6); SPEC 11/13 wizard step replaced.
- [ ] Commit `feat(deploy): add cloudflared behind the tunnel profile`.

### Task 5: The public route is app-only unless `BRIEVIE_PUBLIC_WEB`

**Files:** create `backend/apps/access/middleware.py`, `backend/tests/test_access_gate.py`; modify `backend/apps/accounts/auth.py`, `backend/brievie/settings.py` (`BRIEVIE_PUBLIC_WEB: bool = env.bool(..., default=False)`, middleware after `SecurityMiddleware`, before session/auth).

**Interfaces:**

```python
APP_PATHS: frozenset[str]   # health, server/ca-certificate, devices/pairing-challenge,
                            # devices/pair, devices/pair-proof, server/addresses (exact paths
                            # taken from the routers, checked by a test against the URL resolver)
class PublicRouteGate:      # 403 public_web_off (JSON under /api/, HTML page elsewhere)
```

- [ ] Failing tests (Django test client with `HTTP_X_BRIEVIE_ROUTE`, `BRIEVIE_BEHIND_CADDY=True`): `test_public_route_refuses_login_setup_reset_and_invitation`, `test_public_route_refuses_a_session_cookie_on_every_session_endpoint` (signed-in client, `getMe` → 401/403), `test_public_route_allows_device_token_calls`, `test_public_route_allows_pairing_and_health`, `test_public_route_web_page_gets_the_html_sentence`, `test_private_route_is_unchanged`, `test_public_web_true_opens_everything`, `test_app_paths_exist_in_the_router` (every entry resolves), `test_forged_private_header_cannot_reach_the_gate_bypass` (documented as Caddy's job; this test asserts a missing header counts as public).
- [ ] Implement middleware and the `SessionAuth.authenticate` refusal; Dutch message verbatim from S 7.6, English in the catalogue.
- [ ] Commit `feat(access): keep the public route app-only unless BRIEVIE_PUBLIC_WEB`.

### Task 6: Links point where the web works

**Files:** modify `backend/apps/common/links.py`, `backend/tests/test_common_links.py`.

- [ ] Failing tests: `test_links_use_the_public_url_when_public_web_is_on`, `test_links_stay_home_when_public_web_is_off`, `test_home_network_only_ignores_the_public_url_while_public_web_is_off`.
- [ ] Implement `web_urls()` (= `BRIEVIE_PUBLIC_URLS` minus public-certificate URLs unless `BRIEVIE_PUBLIC_WEB`); `public_base_url` prefers the public URL when present in `web_urls()`.
- [ ] Commit `feat(access): send invitation and reset links to an address where the web answers`.

### Task 7: Devices learn which addresses carry a public certificate

**Files:** modify `backend/apps/devices/schemas.py` (`PairOut.public_urls: list[str] = []`), `backend/apps/devices/services.py`; create `getServerAddresses` in `backend/apps/access/api.py` + `ServerAddressesOut`; regenerate `docs/api/openapi.json`, `web/src/api/schema.d.ts`; tests in `backend/tests/test_access_api.py`, devices tests.

- [ ] Failing tests: `test_pair_and_pair_with_proof_return_the_public_urls`, `test_pair_returns_empty_public_urls_without_a_public_name`, `test_get_server_addresses_with_device_token`, `test_get_server_addresses_with_session`, `test_get_server_addresses_without_auth_is_401`, `test_contract_additive` (the existing fixture comparison passes; no `anyOf`), `test_qr_payload_is_unchanged` (v 1, same keys).
- [ ] Implement; `make openapi`, `cd web && pnpm gen:api`; commit both generated files with the change.
- [ ] Commit `feat(devices): tell paired apps which addresses use a public certificate`.

### Task 8: Startup warnings

**Files:** modify `backend/apps/common/checks.py`, `backend/tests/test_system_checks.py`, `backend/brievie/settings.py` (`BRIEVIE_TUNNEL_TOKEN_SET`, `BRIEVIE_TRUSTED_PROXIES` as list, `BRIEVIE_SECOND_FACTOR_AVAILABLE = False` constant until two-step sign-in lands).

- [ ] Failing tests: `test_w002_public_web_without_second_factor` (public way on + `BRIEVIE_PUBLIC_WEB` + not available), `test_w002_silent_when_public_web_off`, `test_w003_upload_limit_above_cloudflare_with_tunnel`, `test_w003_silent_without_tunnel`, `test_warnings_never_print_the_token` (token-set setting only; assert no secret-looking value in messages).
- [ ] Implement as `Warning` (never refuses start; the home network must keep working).
- [ ] Commit `feat(access): warn at start about web sign-in on the internet and Cloudflare's limit`.

### Task 9: `getExternalAccess`

**Files:** create `backend/apps/access/{status,schemas,api}.py`, `backend/tests/test_access_status.py`, `backend/tests/test_access_api.py`; mount the router under `/api/v1/server/external-access`; `INSTALLED_APPS += ["apps.access"]`.

**Interfaces:**

```python
TUNNEL_READY_URL = "http://cloudflared:2000/ready"
def probe_tunnel(timeout: float = 2.0) -> Literal["yes", "no"]     # urllib, no redirects, body ignored
def external_access_status(request: HttpRequest, *, probe: Callable[[], str] = probe_tunnel) -> ExternalAccess
CHECK_CODES: tuple[str, ...]   # the closed list of S 8
```

- [ ] Failing tests (pure, probe injected): one test per check code with its trigger and its absence (`test_check_tunnel_not_running_when_token_set_and_probe_fails`, `test_check_public_hostname_missing_with_trusted_proxies`, `test_check_hostname_mdns_over_vpn`, `test_check_request_not_secure`, `test_check_app_update_needed_lists_no_device_names`, …), `test_status_never_contains_the_token` (set a token-like value in env; serialise; assert absent), `test_seen_as_reports_route_client_and_peer`.
- [ ] API tests: `test_get_external_access_server_admin_only` (403 `forbidden` for a workspace admin), `test_get_external_access_device_token_gets_401`, `test_get_external_access_no_store`, `test_probe_runs_outside_a_transaction` (view is `non_atomic_requests`).
- [ ] `docs_url` from a constant `EXTERNAL_ACCESS_DOCS_URL` (value per Besluit B4).
- [ ] `make openapi`, `pnpm gen:api`; commit `feat(access): report external access to server admins`.

### Task 10: Web page "Van buiten bereikbaar"

**Files:** create `web/src/routes/server-external-access.tsx`, `.test.tsx`; modify `web/src/router.tsx`, the server-admin nav (next to "Serverlogboek"), `web/src/locales/{nl,en}` (via `pnpm extract`), MSW handlers.

- [ ] Failing Vitest tests (by role): `shows one panel per way with on/off`, `shows each check with text and icon, not colour alone`, `shows the address Brievie sees`, `links to the website docs`, `has no input fields`, `hidden from members who are not server admins`, `renders the red line for public web without a second factor`.
- [ ] Implement with React Aria; texts B1 Dutch, English via `pnpm extract` (GLOSSARY "English in the interface": "External access", "own reverse proxy", "tunnel").
- [ ] `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`; commit `feat(web): add the external access page for server admins`.

### Task 11: Wrapper `./brievie`

**Files:** modify `brievie`, `backend/tests/test_brievie_wrapper.py` (fake docker).

- [ ] Failing tests: `test_start_without_token_never_mentions_the_tunnel_profile`, `test_start_with_token_starts_cloudflared_after_the_stack` (logged calls: main `up … --wait` without `--profile tunnel`, then `--profile tunnel up -d --no-deps cloudflared`), `test_a_failing_tunnel_is_a_warning_not_a_failed_start`, `test_update_and_restore_pass_the_profile_when_stopping_and_starting`, `test_the_token_never_appears_in_an_argv_or_output`, `test_start_refuses_a_public_name_that_is_not_a_bare_host`, `test_start_refuses_a_public_name_equal_to_the_home_name`, `test_start_prints_every_address`, `test_start_prints_the_red_sentence_with_public_web_before_second_factor`.
- [ ] Compose reads `COMPOSE_PROFILES` from `deploy/.env` by itself: the wrapper unsets/overrides it for its own calls (`COMPOSE_PROFILES=` exported empty for the main `up`) so cloudflared never joins the main `up -d --wait`; test `test_compose_profiles_in_env_does_not_pull_cloudflared_into_the_main_start`. After `up -d --no-deps cloudflared`, poll `ps --status running --services` for up to 15 s before deciding "running" or the warning.
- [ ] Implement: a `tunnel_on` flag from `env_value BRIEVIE_TUNNEL_TOKEN` (presence only; the value is never copied into a variable that is printed), name checks with `is_bare_host` logic in shell, Dutch messages from S 10.
- [ ] Commit `feat(wrapper): start the tunnel when a token is set`.

### Task 12: Docs

**Files:** `docs/SELFHOST.md` (section "Van buiten bereikbaar" = S 11 outline for the website, with the variable table), `docs/PRIVACY.md` (S 7.7 sentences), `docs/GLOSSARY.md` (Van buiten bereikbaar = external access; tunnel; eigen reverse proxy = own reverse proxy; publiek adres = public address), `docs/SPEC.md` (11: order of ways and "compose decides"; 13: wizard step links to the docs), `CHANGELOG.md`, README install section link.

- [ ] Write; check every variable of `deploy/.env.example` appears in the SELFHOST table (`test_selfhost_documents_every_env_variable` in `test_deploy_files.py`, written first).
- [ ] Commit `docs: explain external access per way and what Cloudflare can read`.

### Task 13: CI proves the routes (no token needed)

**Files:** modify `.github/workflows/ci.yml` (`stack`, `image`), `backend/tests/test_deploy_files.py` (`test_ci_checks_the_tunnel_listener`).

Add to `stack`, after "The web interface is served", with `BRIEVIE_PUBLIC_HOSTNAME=brievie.example.test` and `BRIEVIE_VPN_HOSTNAME=vpn.example.test` appended to `deploy/.env` before `up`:

- [ ] "Caddy's configuration adapts": `$COMPOSE exec -T caddy caddy adapt --config /etc/caddy/Caddyfile --adapter caddyfile` piped to a Python check: the `:8080` server alone has `client_ip_headers: ["CF-Connecting-IP"]`; every `reverse_proxy` handler sets `X-Brievie-Route`, `X-Brievie-Client-IP`, `X-Brievie-Peer-IP`.
- [ ] "The home name answers with HSTS 0": `curl -sk -D- https://localhost/api/v1/health` has `max-age=0`.
- [ ] "The tunnel listener answers with HSTS one year": `$COMPOSE exec -T api python -c` (urllib, `Host: brievie.example.test`, `X-Forwarded-Proto: https`) to `http://caddy:8080/api/v1/health` → 200 and `max-age=31536000`; `X-Forwarded-Proto: http` → 308 to `https://brievie.example.test/…`.
- [ ] "A 413 carries the route's HSTS": an oversized body via `:8080` → 413 with `max-age=31536000`; via `https://localhost` → 413 with `max-age=0`.
- [ ] "A forged route header changes nothing": via `:8080` with `X-Brievie-Route: private` → the `getMe` path (resolve it from the router) is 403 `public_web_off`; via `https://localhost` with `X-Brievie-Route: public` and `CF-Connecting-IP: 203.0.113.9` → 401 `not_authenticated` (private route).
- [ ] "The VPN name is served by the private CA": `curl -sk --resolve vpn.example.test:443:127.0.0.1 https://vpn.example.test/api/v1/health` → 200.
- [ ] "An IP address without SNI gets a certificate": `curl -sk https://127.0.0.1/api/v1/health -H 'Host: localhost'` → 200 (`default_sni`).
- [ ] "The tunnel profile is valid": `$COMPOSE --profile tunnel config -q` with `BRIEVIE_TUNNEL_TOKEN=x`; and without the token `$COMPOSE config -q` (no interpolation error).
- [ ] `image` job: a Trivy scan step of the pinned `cloudflare/cloudflared` image (same severity and ignore-unfixed settings).
- [ ] Push `feat/external-access`; read `stack`, `image`, `backend`, `web`, `dast`, `update`, `restore`. Fix syntax in the Caddyfile here if `stack` disagrees with the sketch, keeping every property the tests assert.
- [ ] Commit `ci: check the tunnel listener, the routes and HSTS per name`.

---

## iOS tasks (repo `Brievie_ios`, separate branch `feat/external-addresses`)

Start after host Task 7 is merged (`make sync-api` needs `public_urls` and `getServerAddresses`).

### I1: Per-address trust

**Files:** `Packages/BrievieKit/Sources/BrievieCore/Trust/ServerTrust.swift`, `Storage/ServerConnection.swift`, `Upload/UploadTransport.swift`, `Documents/WorkspaceDocuments.swift`, `App/Unpairing.swift`, tests in `BrievieCoreTests`.

- [ ] Failing Swift Testing tests: `publicHostUsesSystemRootsNotTheCA`, `privateHostStaysAnchoredToTheCA`, `aPublicHostNotListedByTheServerStaysAnchored`, `oldStoredConnectionWithoutPublicURLsStillDecodes`.
- [ ] `ServerTrust(caCertificateDER:publicHosts:)`; `ServerConnection.publicURLs: [URL]?`; every `ServerTrust(...)` call site passes the stored public hosts.

### I2: Pairing from outside

**Files:** `Pairing/PairingService.swift`, `Pairing/PairingProof.swift`, `Server/LiveBrievieServer*.swift`, tests.

- [ ] Failing tests: `caAnchoredAddressesWinOverSystemTrustWhateverTheLinkOrder`, `locateFallsBackToSystemTrustForACertifiableHostOnlyWhenNoAddressVerifiesWithTheCA`, `proofBindsToTheHostWhenSystemTrustValidated`, `proofBindsToTheCAWhenTheCAValidated`, `storedURLsKeepOnlyCAVerifiedLinkURLsPlusServerPublicURLs`, `aPublicURLFromTheLinkAloneIsNeverStored`.
- [ ] Implement two passes in `locate` and `pair(_:deviceName:)`: first every URL CA-anchored; only when none verifies, a second pass with system trust over the URLs whose host a public CA can certify. The trust binding follows what TLS validated; `PairOut.public_urls` fills `ServerConnection.publicURLs`; a public URL the server did not return is never stored.

### I3: Learn and remember addresses

**Files:** `Storage/ServerConnection.swift` (`lastWorkingURL: URL?`), `Documents/WorkspaceDocuments.swift`, `Upload/UploadTransport.swift`, a small `AddressRefresher`.

- [ ] Failing tests: `lastWorkingAddressIsTriedFirst`, `addressesRefreshAtMostDailyOverAVerifiedConnection`, `refreshAddsTheTunnelWithoutRepairing`, `refreshNeverAcceptsAddressesOverAnUnverifiedConnection`.
- [ ] Implement; app version with I1–I3 is the "X" of the settings page's `app_update_needed` check (host constant `FIRST_APP_WITH_PUBLIC_ADDRESSES`, set when the app release is cut).

---

## Build order

| Order | Work | Why |
|---|---|---|
| 1 | Besluiten B1–B4 answered | B1/B2 decide whether Task 5's default and Task 8's W002 ship as written |
| 2 | Host Tasks 1–4, 13 (CI steps) | the routes and the derivation; home behaviour proven unchanged in CI |
| 3 | Host Tasks 5–9 | gate, links, device addresses, warnings, status endpoint |
| 4 | iOS I1–I3 (after Task 7 merged) | without them the tunnel address fails for every app |
| 5 | Host Tasks 10–12 | page, wrapper, docs; the website page follows SELFHOST |
| 6 | Two-step sign-in (separate plan) | then `BRIEVIE_SECOND_FACTOR_AVAILABLE` becomes real and B2's rule lands |

The tunnel is announced on the website only after step 4 ships in the App Store.
