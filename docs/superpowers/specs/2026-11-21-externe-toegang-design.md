# Van buiten bereikbaar (external access)

Date: 2026-11-21. Branch `docs/external-access-design` (host). Design only; no product code.
Plan: `docs/superpowers/plans/2026-11-21-host-externe-toegang.md`.
Builds on SPEC sections 11 ("Toegang van buitenaf"), 13 (wizard step), 14 and 18, on the pairing
proof (D 20, `apps/devices/proof.py`) and on the two-step sign-in design
(`docs/superpowers/specs/2026-11-20-tweestapsverificatie-design.md`, branch
`docs/intake-and-2fa-design`, not built).

## 1. Intent

Stijn: "in the settings, options to make Brievie reachable from outside the home network. Not
built into the app: with Cloudflare Tunnel a setting that makes the web interface and everything
else work through it; the same for VPN; preferably configured in docker compose, with a good
compose explanation with variables on the website."

Today Brievie answers on one name (`BRIEVIE_HOSTNAME`) with Caddy's private CA; the iPhone pins
that CA. Away from home nothing works unless the user builds something by hand, and the obvious
hand-built paths (a tunnel, a reverse proxy) break in four places: the app's pin, Django's idea
of "secure", the client address of every limit, and HSTS.

## 2. Decided by the orchestrator (recorded, not open)

1. **Compose decides, the settings page shows.** Exposure is configured in `deploy/.env` and
   docker compose only. The web interface gets an admin-only page "Van buiten bereikbaar"
   (`auth=session_auth`, server admins only) that reads the configuration from the environment,
   shows a checklist of what is and is not configured and what still fails, links to the website
   docs, and never shows a token or secret.
2. **No switch in the UI.** Changing exposure needs server access. This replaces SPEC 11's
   wizard step "gebruiker plakt zijn tunnel-token" and SPEC 13's "toegang van buitenaf" wizard
   step (the wizard links to the docs instead). A DECISIONS line records the change.
3. Texts in Dutch (B1, the source) and English.

## 3. Goals, non-goals, anti-goals

- Goal: four documented ways to reach Brievie: home network (unchanged), VPN, Cloudflare
  Tunnel, own reverse proxy. Each is a few lines in `deploy/.env`; nothing else to edit.
- Goal: the iPhone app pairs and works over every way, including from outside, without a
  second pairing when a way is added later.
- Goal: the client address, "is this request secure", HSTS and the body limits are right per
  way, and a forged header from the home network or the internet cannot change them.
- Goal: the settings page tells an admin in plain words what is set, what works and what not.
- Non-goal: Brievie Connect (SPEC 11, phase 3), Let's Encrypt on Caddy itself (port 80/443 open
  on the router), Tailscale Funnel, `tailscale cert`, a WireGuard or Tailscale container in the
  stack.
- Anti-goal: a token or secret in an API response, a log line, the api container's environment
  or a process argument.
- Anti-goal: the web sign-in on the internet without a second factor, unless the admin switched
  it on knowingly and the page shows it red (section 8).
- Anti-goal: the app sending its device token to an address the server never vouched for.

## 4. What the code says today (checked, not assumed)

| Question | Answer from the code |
|---|---|
| How does the app trust the server? | `ServerTrust(caCertificateDER:)` (iOS `BrievieCore/Trust/ServerTrust.swift`): with a CA it sets that CA as the only anchor for **every** connection. `ServerConnection` holds one `caCertificateDER` for all `urls`. `WorkspaceDocuments.perform`, `UploadTransport`, `Unpairing` and `PairingService.verify` all build one `ServerTrust` from it. |
| So does a tunnel URL work? | **No.** A Cloudflare URL carries a public certificate. Anchored to the private CA it fails; `perform` ends in `.notVerified`, `PairingService.verify`/`locate` in `.unverifiable`. Pairing from outside and every later call from outside break. iOS change needed (section 9). |
| Does the server already know public names? | Yes. `proof.trust_bindings()` accepts the host of every `BRIEVIE_PUBLIC_URLS` entry that a public CA can certify (`public_host_binding`); the app mirrors it (`PairingProof+Host.swift`). The server side of link pairing over a public name exists. |
| Client address | `apps.common.net.client_ip`: with `BRIEVIE_PROXY_COUNT=1` the last `X-Forwarded-For` entry, which Caddy writes itself (it trusts no proxy; `test_caddy_replaces_a_client_sent_forwarded_for`). axes (`AXES_CLIENT_IP_CALLABLE`), `signin_limits`, `throttling` (`address_bucket(client_ip)`), sessions and audit (`valid_client_ip`) all go through it. One seam. |
| Behind a tunnel? | cloudflared would be Caddy's peer: every visitor gets cloudflared's container address. axes then locks a user for everyone on the tunnel, and the per-address limits count all visitors as one. |
| `is_secure()` | `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`. Caddy writes `X-Forwarded-Proto` from its own connection. cloudflared → Caddy over plain HTTP would give `http`: secure cookies and the CSRF origin check fail. |
| HSTS | One value, `BRIEVIE_HSTS_SECONDS`, in the `security_headers` snippet and Django. Caddy's `header` block is deferred (it deletes `Server`), so Caddy's value overwrites Django's. |
| Body limits | Caddy and Django: 2.5 MiB general, 32 MiB document upload. Cloudflare's free plan accepts request bodies up to 100 MB: fits. |
| Caching | `apps.common.errors` sets `Cache-Control: no-store` on every `/api/` answer without its own; downloads set `private, no-store`. Cloudflare does not cache them. |
| VPN | A phone on a VPN that routes to the home LAN reaches Caddy at `BRIEVIE_HOSTNAME`: same CA, same pin, nothing to change, **unless** the name is an mDNS `.local` name (does not resolve over a VPN) or the VPN gives the server its own address (Tailscale on the NAS: `100.x.y.z`, `nas.tailnet.ts.net`). Caddy has one site block for one name; another Host gets no site and Django answers 400. So: docs-only for "VPN into the LAN", a second private name for "VPN on the server". |
| Wrapper | `cmd_start` runs `compose_raw up -d --build --wait` without a profile; the update finish starts `api worker keeper` then `caddy` by name. A profile service would never start. |
| Links | `links.public_base_url` uses the first `BRIEVIE_PUBLIC_URLS` entry (the local one) for invitation and reset links; `home_network_only` is true while every URL is a home name. |
| Health check | The api healthcheck takes the first `ALLOWED_HOSTS` entry from the environment. |

## 5. The design per way

Every way keeps Caddy as the only door to the api and the internal CA for the home name. A way
adds an address; it never replaces the home one. The app tries addresses in order (home, VPN,
public) and remembers the one that worked last (iOS task I3).

### 5.1 Home network (unchanged)

`BRIEVIE_HOSTNAME` only. Nothing changes in behaviour; the Caddyfile is restructured (5.6) but
the home site answers byte for byte as before (HSTS from `BRIEVIE_HSTS_SECONDS`, default 0).

### 5.2 VPN (Tailscale, WireGuard): recommended

Two cases, both end-to-end encrypted, nothing public:

- **VPN into the home network** (WireGuard on the router, Tailscale subnet router): docs only.
  `BRIEVIE_HOSTNAME` must be a fixed IP address or a name the VPN's DNS resolves, never
  `brievie.local`. The settings page warns when the name ends in `.local`.
- **VPN on the server itself** (Tailscale package on the NAS): one optional variable,
  `BRIEVIE_VPN_HOSTNAME` (the MagicDNS name, e.g. `nas.tail1234.ts.net`, or the `100.x` address).
  Caddy serves it in the same site block with `tls internal`: same CA, same pin, no iOS change.
  It is added to `ALLOWED_HOSTS` and as the second `BRIEVIE_PUBLIC_URLS` entry (with
  `BRIEVIE_HTTPS_PORT`). A MagicDNS name is preferred over the `100.x` address: an iPhone sends no
  SNI for an IP address and the hostname check needs a name in the certificate (the plan's CI step
  checks the IP case through `default_sni`, 5.6).

An already paired phone learns the new address without pairing again (`getServerAddresses`,
section 9).

### 5.3 Cloudflare Tunnel: easiest, Cloudflare reads along

- `cloudflared` is an optional compose service under the profile `tunnel`, image
  `cloudflare/cloudflared` pinned by version and digest, `restart: unless-stopped`,
  `read_only: true`, `cap_drop: [ALL]`, `no-new-privileges`, no published port,
  `depends_on: caddy: condition: service_started`.
  Command: `tunnel --no-autoupdate --metrics 0.0.0.0:2000 run`.
- **Token:** one variable, `BRIEVIE_TUNNEL_TOKEN`, passed only to this service as `TUNNEL_TOKEN`
  (`${BRIEVIE_TUNNEL_TOKEN:-}`, never `:?`: compose interpolates services of inactive profiles
  too). The api gets only `BRIEVIE_TUNNEL_TOKEN_SET: ${BRIEVIE_TUNNEL_TOKEN:+true}` (the
  `BRIEVIE_BACKUP_DIR_SET` pattern). A deploy test asserts the token variable appears in exactly
  one service and nowhere in the Caddyfile.
- **Public name:** one variable, `BRIEVIE_PUBLIC_HOSTNAME` (e.g. `brievie.example.nl`, the
  public hostname set in the Cloudflare dashboard). `ALLOWED_HOSTS`, `BRIEVIE_PUBLIC_URLS`
  (`https://<name>`, always port 443, last in the list) follow from it, as they follow from
  `BRIEVIE_HOSTNAME` today (section 6).
- **cloudflared → Caddy:** plain HTTP to an internal Caddy listener `http://caddy:8080`, never
  published (decided, trap 7.2). In the Cloudflare dashboard the public hostname's service is
  `http://caddy:8080`. No CA pool, no "No TLS Verify" to get wrong; the hop is the compose network
  on the same host, and Cloudflare has already decrypted the request anyway.
- **Profile:** `./brievie start` turns the profile on when `BRIEVIE_TUNNEL_TOKEN` is set (section
  10). A hand `docker compose` user (Synology Container Manager) sets `COMPOSE_PROFILES=tunnel` in
  `deploy/.env`; the settings page says so when the token is set but the tunnel does not answer.
- **Is it connected?** The api asks `http://cloudflared:2000/ready` (cloudflared's own readiness
  endpoint, on the compose network, 2 s timeout). 200: connected; anything else: not.

### 5.4 Own reverse proxy (Nginx Proxy Manager, Traefik, Synology): falls out of the same settings

- The proxy terminates TLS with its own public certificate and forwards to
  `https://<server>:<BRIEVIE_HTTPS_PORT>` (Caddy's normal port). It may skip verifying Caddy's
  certificate (nginx and Synology do not verify by default) or trust the CA from
  `GET /api/v1/server/ca-certificate`.
- `BRIEVIE_PUBLIC_HOSTNAME` = the proxy's public name (served in Caddy's site block too, so the
  Host matches); `BRIEVIE_TRUSTED_PROXIES` = the address(es) Brievie sees for the proxy, space
  separated CIDRs (the settings page shows the address it sees for the admin's own request, to fill
  this in). Caddy trusts `X-Forwarded-For`/`-Proto` only from those (`servers :443 {
  trusted_proxies static …; trusted_proxies_strict }`) and marks their requests as the public
  route (5.6).
- No new port. `default_sni {$BRIEVIE_HOSTNAME}` lets a proxy that sends no SNI (nginx's
  default) complete the handshake.
- Including it costs one variable and two Caddyfile lines; it is the most common case on other
  self-hosted projects' install pages. Besluit B3 asks whether it ships in v1.

### 5.5 Which way is "on" (derived, no mode variable)

There is no `BRIEVIE_ACCESS_MODE`. The page derives the ways from what is set, so two settings can
never disagree and VPN plus tunnel can be on together:

| Way | On when |
|---|---|
| Home network | always |
| VPN on the server | `BRIEVIE_VPN_HOSTNAME` set (VPN into the LAN is invisible to the server; the page always shows its advice) |
| Cloudflare Tunnel | `BRIEVIE_TUNNEL_TOKEN` set |
| Own reverse proxy | `BRIEVIE_TRUSTED_PROXIES` set |

### 5.6 Caddy: routes, edge headers, HSTS

Caddy is the one place that knows which way a request came. It tells Django in two headers it
**always sets itself** (`header_up` overwrites any client-sent value):

- `X-Brievie-Route: private | public`: `public` on the tunnel listener and for requests from a
  trusted proxy (`remote_ip {$BRIEVIE_TRUSTED_PROXIES}`), `private` otherwise.
- `X-Brievie-Client-IP: {client_ip}`: Caddy's client address after its trusted-proxy rules.
  `X-Brievie-Peer-IP: {remote_host}` (the direct peer, for the settings page only).

Per listener (Caddy global `servers` options):

| Listener | Published | Trusted peers | Client address from | Route | HSTS |
|---|---|---|---|---|---|
| `:443` site `{$BRIEVIE_HOSTNAME} {$BRIEVIE_VPN_HOSTNAME} {$BRIEVIE_PUBLIC_HOSTNAME}`, `tls internal` | yes (`BRIEVIE_HTTPS_PORT`) | `BRIEVIE_TRUSTED_PROXIES` (compose default `127.0.0.1/32`: nobody, Caddy itself) | peer, or the right-most untrusted `X-Forwarded-For` entry from a trusted proxy | `public` from a trusted proxy, else `private` | `BRIEVIE_HSTS_SECONDS` (0), 31536000 from a trusted proxy |
| `http://:8080` (tunnel) | **never** | `private_ranges` (only containers of this compose network can reach it) | `CF-Connecting-IP` (`client_ip_headers CF-Connecting-IP`) | `public` | 31536000 |

On the tunnel listener Caddy also: redirects a request whose `X-Forwarded-Proto` is `http` to
`https://{$BRIEVIE_PUBLIC_HOSTNAME}{uri}` (308), then sets `X-Forwarded-Proto: https` and
`Host: {$BRIEVIE_PUBLIC_HOSTNAME}` towards the api (cloudflared's Host handling stops mattering).

The routes (upload limit block, general block, `handle_errors`) move into one snippet imported by
both site blocks; HSTS and the route are Caddy `vars` set per site (and per trusted-proxy matcher)
and read in the snippet, so each limit and each error answer exists once.

**HSTS is Caddy's alone.** Django's `SECURE_HSTS_SECONDS` is one value per process and cannot
differ per host; compose keeps passing 0 to the api (as today) and Caddy's deferred header decides
per route.

## 6. Addresses: one derivation in Python

Today compose derives `ALLOWED_HOSTS` and `BRIEVIE_PUBLIC_URLS` from `BRIEVIE_HOSTNAME`. With up
to three names, nested `${VAR:+…}` interpolation would be fragile and untestable. The derivation
moves into a pure module `apps/access/addresses.py` (no Django import), called from
`settings.py`:

```
derive(hostname, vpn_hostname, public_hostname, https_port) -> Addresses
  allowed_hosts = [hostname, vpn_hostname?, public_hostname?]
  public_urls   = [https://hostname[:port], https://vpn_hostname[:port]?, https://public_hostname?]
  public_url_hosts = {public_hostname}          # served with a public certificate
```

- An explicit `ALLOWED_HOSTS` or `BRIEVIE_PUBLIC_URLS` in `deploy/.env` still wins (today's
  override semantics); `brievie.E001` (every public URL's host allowed) stays.
- New `brievie.E002`: `BRIEVIE_PUBLIC_HOSTNAME` or `BRIEVIE_VPN_HOSTNAME` equals another name, or
  is not a bare host name (Caddy would refuse a duplicate site address; Django would refuse to start
  with a clear sentence first).
- `CSRF_TRUSTED_ORIGINS` needs nothing: with `X-Forwarded-Proto: https` and the Host forced to
  the public name, the browser's `Origin` equals `https://` + Host and Django's same-origin check
  passes. It stays an explicit override only.
- Compose passes `BRIEVIE_HOSTNAME`, `BRIEVIE_VPN_HOSTNAME`, `BRIEVIE_PUBLIC_HOSTNAME`,
  `BRIEVIE_HTTPS_PORT` to the api; `ALLOWED_HOSTS: ${ALLOWED_HOSTS:-}` and
  `BRIEVIE_PUBLIC_URLS: ${BRIEVIE_PUBLIC_URLS:-}` default to empty (= derive). The api healthcheck
  uses `ALLOWED_HOSTS` when set, else `BRIEVIE_HOSTNAME`.
- `links.public_base_url` and `home_network_only` consider only the addresses where the web
  interface answers: the public URL counts only while `BRIEVIE_PUBLIC_WEB=true` (section 8).
  Invitation and reset links then use the public address; otherwise the home one, as today.

## 7. The traps, settled

### 7.1 Pinning through the tunnel (iOS change)

Settled in section 9: per-address trust. Addresses the **server** lists as public-certificate
addresses (`public_urls`, received over an already verified connection) use system trust and bind
the pairing proof to the host name; every other address stays anchored to the private CA. Old app
versions keep working at home and fail away from home with their current "not verified" message;
the settings page says "Werk de app bij naar versie X of nieuwer" while a public way is on.

### 7.2 `is_secure()` behind the tunnel

Choice: **Caddy sets the proto on the tunnel listener** (`X-Forwarded-Proto: https` after the
http→https redirect), cloudflared → Caddy is plain HTTP on the unpublished `:8080`. Rejected:
cloudflared → Caddy over HTTPS against the internal CA: it needs the CA mounted into cloudflared
and three dashboard fields (`caPool`, `originServerName`) that users get wrong and "fix" with "No
TLS Verify"; it protects one hop on the same host after Cloudflare already decrypted everything.
For the own proxy Caddy listens on `:443` itself, so `is_secure()` is true by construction.

### 7.3 Client address for login limits

Django reads `X-Brievie-Client-IP` (set by Caddy, never by a client) when `BRIEVIE_BEHIND_CADDY`
is true (compose sets it); otherwise today's `BRIEVIE_PROXY_COUNT` logic. Django **never** reads
`CF-Connecting-IP`; Caddy reads it only on the unpublished tunnel listener. So:

- outside tunnel mode, and on `:443` in tunnel mode, a sent `CF-Connecting-IP` changes nothing;
- through the tunnel each visitor counts by their own address (axes, `signin_limits`, uploads);
- a sent `X-Brievie-Client-IP` or `X-Brievie-Route` is overwritten by Caddy on every listener.

Tests: Django unit tests (`CF-Connecting-IP` ignored in every configuration; the Caddy header used
only behind Caddy); Caddyfile text tests; CI adapts the real Caddyfile to JSON and asserts that
`client_ip_headers` appears only on the `:8080` server and that every `reverse_proxy` sets the
three headers; CI sends forged route headers on both listeners and checks the answer (plan T13).

### 7.4 HSTS per site

Section 5.6: private-CA names `BRIEVIE_HSTS_SECONDS` (0), public routes 31536000. Checked in CI on
a normal answer and on a 413 from each listener.

### 7.5 Body and upload limits vs Cloudflare

32 MiB upload and 2.5 MiB body fit the free plan's 100 MB. A system check warns
(`brievie.W003`) and the settings page shows a warning when the tunnel is on and
`BRIEVIE_MAX_UPLOAD_BYTES` exceeds 100,000,000. Cloudflare also cuts an origin answer after 100
seconds (524); no Brievie request waits that long (exports are jobs).

### 7.6 Internet exposure without two-factor sign-in

The app never signs in with a password: it pairs with a code made in a signed-in web session and
then uses a device token. Only the **web** sign-in is a password on the internet. So:

- New variable `BRIEVIE_PUBLIC_WEB` (default **false**). On the public route with it false, Django
  answers only: requests with `Authorization: Bearer` (device token; the endpoint's own auth still
  decides) and the unauthenticated app paths (health, CA certificate, pairing challenge, pair, pair
  with proof). Everything else gets 403 `public_web_off` ("Op dit adres werkt alleen de
  Brievie-app. Gebruik de webinterface thuis of via VPN."); a web page path gets the same sentence
  as a small HTML page. `SessionAuth` additionally refuses a session cookie on the public route.
  This lives in Django (a middleware in `apps/access/`), because only Django knows which endpoint
  is session-only; Caddy only marks the route.
- `BRIEVIE_PUBLIC_WEB=true`: the web interface works through the tunnel or proxy, as Stijn asked.
  Until two-step sign-in exists, the api logs a startup warning (`brievie.W002`), `./brievie start`
  prints it, and the settings page shows a red line "Inloggen op de webinterface kan van buiten
  zonder tweede stap".
- **Cloudflare Access** is documented as an extra for web-only use, with its limit said plainly:
  Access in front of the whole hostname blocks the app (a native app cannot do Access's browser
  login); an Access application must leave `/api/v1/` out, which also leaves the web sign-in
  endpoint out. It is therefore no substitute for two-step sign-in.
- **Order:** build two-step sign-in before `BRIEVIE_PUBLIC_WEB=true` is offered on the website as
  normal. The app-only tunnel (`BRIEVIE_PUBLIC_WEB=false`) does not wait for it. Besluit B1 and B2.

### 7.7 Privacy, said plainly

- With **Cloudflare Tunnel** (and with a reverse proxy at a hosting party) Cloudflare ends the
  encryption on its own servers: everything passing through, including scanned pages, BSN-masked
  text and the device token, is readable there. Cloudflare's terms apply.
- With a **VPN** the connection is encrypted from the phone to the home server; nobody in between
  can read it.
- The website, `docs/PRIVACY.md`, `docs/SELFHOST.md` and the settings page say so in these words.
  **VPN is the recommended default**; SPEC 11's order "Cloudflare Tunnel (makkelijkst) · Tailscale
  (meeste privacy)" becomes "VPN (aanbevolen) · Cloudflare Tunnel (makkelijkst, Cloudflare leest
  mee) · eigen reverse proxy · alleen thuis".
- Cloudflare features that rewrite pages (Rocket Loader, Email Obfuscation, automatic Web
  Analytics) break the strict CSP; Bot Fight Mode and "I'm Under Attack" challenges break the app.
  The docs list them as "uit laten".

## 8. Settings page "Van buiten bereikbaar"

- Endpoint `getExternalAccess`: `GET /api/v1/server/external-access`, `auth=session_auth`,
  server admins only (`_server_admin` pattern of `apps/audit/api.py`: 403 `forbidden`), not atomic
  (it waits up to 2 s for cloudflared; no transaction is held open meanwhile),
  `Cache-Control: no-store`.
- Response (no nullable non-primitives, no `anyOf`):

```
ExternalAccessOut
  addresses: list[AddressOut]          # url, kind: home|vpn|public, certificate: private_ca|public
  public_hostname: str = ""
  public_web: bool
  tunnel: TunnelOut                    # token_set: bool, connected: yes|no|not_set
  trusted_proxies: list[str]           # CIDRs; not secret
  second_factor_available: bool        # false until two-step sign-in is built
  seen_as: SeenAsOut                   # route: private|public, secure: bool, client_ip: str, peer_ip: str
  checks: list[CheckOut]               # code (closed list), level: ok|warning|problem
  docs_url: str
```

- Check codes (closed list; the web holds the sentences, Lingui nl/en):
  `hostname_loopback` (problem), `hostname_mdns_over_vpn` (warning: `.local` name),
  `public_hostname_missing` (problem: token or trusted proxies set, no public name),
  `public_url_overridden_without_public_host` (problem), `tunnel_not_running` (problem: token
  set, `/ready` does not answer; mentions `COMPOSE_PROFILES=tunnel`), `upload_limit_above_cloudflare`
  (warning), `public_web_without_second_factor` (problem), `public_web_off` (ok: "alleen de app"),
  `request_not_secure` (problem: the admin's own request arrived without https: a proxy
  misconfiguration), `cloudflare_reads_along` (warning, always with the tunnel), `app_update_needed`
  (warning while a public way is on and a paired device reports an app version below the first
  version with per-address trust).
- Page: one panel per way (home, VPN, tunnel, own proxy) with "aan/uit" and its addresses, the
  check list (green/orange/red with icon and text, never colour alone), "Zo ziet Brievie jouw
  verbinding" (route, https, the address it sees: helps fill in `BRIEVIE_TRUSTED_PROXIES`), and a
  link "Uitleg en voorbeelden op de website". No input fields. Nav: in the server section next to
  "Serverlogboek", for server admins only.
- Never shown or returned: `BRIEVIE_TUNNEL_TOKEN` (the api does not have it), passwords, keys.

## 9. iOS: per-address trust and server-vouched addresses

Server side (additive, old apps unaffected):

- `PairOut.public_urls: list[str] = []`: the `BRIEVIE_PUBLIC_URLS` entries served with a public
  certificate (host = `BRIEVIE_PUBLIC_HOSTNAME`).
- `getServerAddresses`: `GET /api/v1/server/addresses`, device token (and session) auth, returns
  `{urls: list[str], public_urls: list[str]}`. The QR payload and server link stay version 1 and
  unchanged.

App side (Brievie_ios, section "iOS tasks" of the plan):

- **I1 Per-address trust.** `ServerTrust` gets the set of public hosts. For a host in it: system
  roots, hostname check, no CA anchor. Any other host: the CA as the only anchor (as today).
  `ServerConnection` gains `publicURLs: [URL]?` (optional: an old stored pairing still decodes).
- **I2 Pairing from outside.** `verify`/`locate`: when the CA-anchored health check fails on a URL
  whose host a public CA can certify (`publicHostBinding != nil`), try system trust; the proof's
  binding is what TLS actually validated (CA fingerprint or host), and the server accepts either
  (`trust_bindings`). After pairing the app keeps: link/QR URLs it verified against the CA, plus
  the server's `public_urls` from `PairOut`, never a public URL taken from the link alone (the
  `storedURLs` reasoning).
- **I3 Learning and remembering addresses.** After any successful call over a verified connection
  the app refreshes `urls`/`public_urls` from `getServerAddresses` (at most once a day and when a
  call fails as unreachable everywhere), and tries the last address that worked first: away from
  home the 15 s `requestTimeout` on the home address would otherwise precede every request.

## 10. Wrapper `./brievie`

- `setup_installation`: when `BRIEVIE_TUNNEL_TOKEN` is set in `deploy/.env`, the tunnel is on.
- `start` (and `update`'s finish, `restore`'s start): first the stack as today; then, if the tunnel
  is on, `compose_raw --profile tunnel up -d --no-deps cloudflared`. A failing tunnel is a warning
  ("Brievie draait, maar de tunnel naar Cloudflare start niet. Controleer BRIEVIE_TUNNEL_TOKEN."),
  never a failed start: the home network keeps working. Every explicit service list (update's
  `api worker keeper`, then `caddy`) and every `stop`/`down` passes `--profile tunnel` when on, so
  cloudflared is stopped and recreated with the rest.
- Before starting: refuse with a Dutch sentence when `BRIEVIE_PUBLIC_HOSTNAME` or
  `BRIEVIE_VPN_HOSTNAME` is not a bare host name or equals another name; warn when the tunnel token
  is set without a public name.
- After starting: print every address (home, VPN, public) and, with `BRIEVIE_PUBLIC_WEB=true`
  before two-step sign-in exists, the red sentence of 7.6.
- The token is read by compose from `deploy/.env`; the wrapper never passes it as an argument or
  prints it (test with the fake docker: the token never appears in a logged argv).

## 11. Website: the compose page (outline)

Page "Brievie installeren met Docker Compose" with a section per way. Structure:

1. *Wat je nodig hebt*: Docker with compose v2, a fixed IP address for the server.
2. *Drie commando's*: `git clone`, `./brievie start` (or hand compose: `sh deploy/init-env.sh`, then
   `docker compose --project-directory deploy -f deploy/docker-compose.yml up -d`).
3. *Kies hoe je Brievie van buiten bereikt*: comparison table (privacy, ease, what is public, cost),
   VPN first and marked recommended; the Cloudflare sentence of 7.7 in the table.
4. One section per way with its `deploy/.env` block (below), the steps outside Brievie (Tailscale
   app; Cloudflare dashboard: create tunnel, copy token, public hostname → `http://caddy:8080`;
   proxy: forward to `https://<server>:443`, websockets not needed, upload size ≥ 32 MB), and "Zo
   controleer je het": the settings page "Van buiten bereikbaar".
5. *Alle variabelen*: the table below.
6. *Problemen oplossen*: 400 (name not in `ALLOWED_HOSTS`), app "niet geverifieerd" (old app or
   public name missing), sign-in loops (proto not https: trusted proxies), 413/Cloudflare, 524.

### 11.1 Every variable of `deploy/.env.example`

"Made" = filled in by `init-env.sh` / `./brievie`. Examples per way: H = home, V = VPN, T =
tunnel, P = own proxy (blank = same as default / not used).

| Variable | Meaning | Default | H | V | T | P |
|---|---|---|---|---|---|---|
| `POSTGRES_PASSWORD` | PostgreSQL superuser; only creates the roles | made | | | | |
| `BRIEVIE_OWNER_PASSWORD` | schema owner (migrations) | made | | | | |
| `BRIEVIE_APP_PASSWORD` | application role | made | | | | |
| `POSTGRES_PORT` | host port for PostgreSQL, development only | `5432` | | | | |
| `BRIEVIE_PUBLISH_POSTGRES` | publish PostgreSQL on 127.0.0.1 (development) | off | | | | |
| `DATABASE_URL`, `DATABASE_OWNER_URL` | host-side URLs for `make test`/`make migrate` | made | | | | |
| `SECRET_KEY` | Django secret, ≥ 50 characters | made | | | | |
| `DEBUG` | never true on a server | `false` | | | | |
| `CSRF_TRUSTED_ORIGINS` | extra https origins; normally not needed | empty | | | | |
| `BRIEVIE_HTTP_PORT` | host port for http (redirect only) | `80` | | | | |
| `BRIEVIE_HTTPS_PORT` | host port for https; part of the home address | `443` | | | | |
| `BRIEVIE_HOSTNAME` | the name or IP the phone uses at home | `localhost` | `192.168.1.20` | `192.168.1.20` | `192.168.1.20` | `192.168.1.20` |
| `BRIEVIE_VPN_HOSTNAME` **new** | the server's own VPN name (Tailscale on the server) | empty | | `nas.tail1234.ts.net` | | |
| `BRIEVIE_PUBLIC_HOSTNAME` **new** | the public name of the tunnel or proxy | empty | | | `brievie.example.nl` | `brievie.example.nl` |
| `BRIEVIE_TUNNEL_TOKEN` **new** | Cloudflare tunnel token (secret; only cloudflared gets it) | empty | | | `eyJh…` | |
| `COMPOSE_PROFILES` **documented** | only for hand compose: starts cloudflared | empty | | | `tunnel` | |
| `BRIEVIE_TRUSTED_PROXIES` **new** | address(es) of your own reverse proxy, CIDR, space separated | empty (= none) | | | | `192.168.1.5/32` |
| `BRIEVIE_PUBLIC_WEB` **new** | web interface (and web sign-in) on the public address | `false` | | | `false` | `false` |
| `BRIEVIE_HSTS_SECONDS` | HSTS for the home name; keep 0 with the private CA | `0` | | | | |
| `BRIEVIE_MAX_BODY_BYTES` | largest request body | `2621440` | | | | |
| `BRIEVIE_MAX_UPLOAD_BYTES` | largest scan upload; ≤ 100 MB behind Cloudflare | `33554432` | | | | |
| `BRIEVIE_MASTER_KEY` | encrypts documents; keep a copy | made | | | | |
| `BRIEVIE_BACKUP_DB_PASSWORD`, `DATABASE_BACKUP_URL` | read-only back-up role | made | | | | |
| `BRIEVIE_BACKUP_PASSWORD` | back-up encryption (derived by default) | derived | | | | |
| `BRIEVIE_BACKUP_DIR` / `BRIEVIE_BACKUP_REPOSITORY` | where back-ups go | empty | | | | |
| `BRIEVIE_BACKUP_S3_*`, `BRIEVIE_BACKUP_REST_*` | credentials of a remote back-up | empty | | | | |
| `BRIEVIE_BACKUP_APPEND_ONLY` | destination accepts only new data | `false` | | | | |
| `BRIEVIE_AUDIT_RETENTION_DAYS` | days the audit log keeps rows (≥ 90) | `365` | | | | |
| `BRIEVIE_SESSION_IDLE_DAYS` / `_MAX_DAYS` | web session limits | `14` / `90` | | | | |
| `BRIEVIE_AI_AUTOPILOT_ALLOWED` | may workspaces switch on the autopilot | `true` | | | | |
| `BRIEVIE_SETUP_TOKEN` | installation code for the first setup | empty (printed once) | | | | |
| `ALLOWED_HOSTS`, `BRIEVIE_PUBLIC_URLS` | overrides; normally derived from the names above | derived | | | | |
| `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` | only for local http development | `true` | | | | |

Variables `./brievie` writes itself (`BRIEVIE_VERSION`, `BRIEVIE_INSTALLATION_ID`) and
`COMPOSE_PROJECT_NAME`/`BRIEVIE_IMAGE` get a short "laat staan" note. Internal compose values
(`BRIEVIE_TUNNEL_TOKEN_SET`, `BRIEVIE_BEHIND_CADDY`, `BRIEVIE_CADDY_CONFIG`) are not documented as
settings.

Example blocks on the page, one per way, e.g. tunnel:

```
BRIEVIE_HOSTNAME=192.168.1.20
BRIEVIE_PUBLIC_HOSTNAME=brievie.example.nl
BRIEVIE_TUNNEL_TOKEN=<plak hier het token uit het Cloudflare-dashboard>
# Alleen als je zonder ./brievie start (bijvoorbeeld Synology Container Manager):
COMPOSE_PROFILES=tunnel
# Pas aanzetten als je weet wat het betekent (zie "Inloggen van buiten"):
# BRIEVIE_PUBLIC_WEB=true
```

## 12. Besluiten voor Stijn

Only questions whose answer changes what gets built.

- **B1. Tweestapsverificatie eerst?** Advies: ja voor de webinterface: bouw 2FA vóór
  `BRIEVIE_PUBLIC_WEB=true` op de website als gewone keuze staat. De tunnel voor alleen de app
  wacht er niet op. Alternatief: alles nu, met de rode regel. Verandert de bouwvolgorde en of
  `BRIEVIE_PUBLIC_WEB` er nu al in zit.
- **B2. Na 2FA: verplicht via het publieke adres?** Advies: ja: wie via het publieke adres met een
  wachtwoord inlogt, moet een tweede stap hebben ingesteld (anders 403 met "Zet eerst thuis
  tweestapsverificatie aan"). Verandert de 2FA-bouw (één regel in de inlogstap) en deze middleware.
- **B3. Eigen reverse proxy in v1?** Advies: ja (één variabele, twee regels in de Caddyfile, de
  meest gevraagde opzet). Nee scheelt plan-taak T3's proxy-deel en één CI-stap.
- **B4. Adres van de uitleg op de website.** De pagina linkt naar `https://brievie.com/…`; welk pad
  (voorstel `https://brievie.com/docs/installeren#van-buiten`)? Verandert één constante en de
  website-pagina.
