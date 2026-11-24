# Two-step sign-in (TOTP)

Date: 2026-11-20. Branch `docs/intake-and-2fa-design` (host). Design only; no code yet.
Builds on and refines section 10 of `2026-11-17-fase-1d-leden-rollen-sso-design.md`.

## Intent

Brievie holds a household's or an office's whole paper post: tax, health, salary, contracts.
Today a password is the only thing between the sign-in page and all of it, and once the
server is reachable from outside (1d part D, a tunnel) that page is on the internet. An
organisation asks for a second factor before anything else; for many it is a box on an
insurance or ISO form. At home it should be available, recommended for the server owner,
and never in the way.

The audit codes `second_factor_added`, `second_factor_removed` and `second_factor_cleared`
already exist (`apps/audit/codes.py`); nothing writes them yet.

## Goals, non-goals, anti-goals

- Goal: a user adds an authenticator app (TOTP, RFC 6238) and gets recovery codes; signing in
  with a password then takes a second step.
- Goal: an organisation can require it, per workspace or for the whole server, with a grace
  period; a lost phone has a way back that leaves a trace.
- Goal: home: off, offered, never required; one page "Beveiliging".
- Non-goal (v1): passkeys/WebAuthn (outlook), SMS or e-mail codes (no SMTP; SMS is weak),
  "remember this browser for 30 days", asking the code again for sensitive settings (1d 19.5;
  this design makes it possible), a second factor in the iOS app.
- Anti-goal: a sign-in path that skips the second step (invitation, reset, setup).
- Anti-goal: an admin who can take over an account unseen by clearing its second factor.
- Anti-goal: a secret or recovery code readable from the database, a log or a later response.

## Design

### 1. The code (TOTP)

- RFC 6238 with the parameters every authenticator app supports: HMAC-SHA1, 6 digits,
  30-second steps, a 20-byte secret from `secrets.token_bytes`.
- **Besluit: written with the standard library** (`hmac`, `hashlib`, `struct`, `base64`,
  `secrets`), about 40 lines in `apps/accounts/totp.py`, tested against the RFC 6238
  appendix B vectors. No new dependency, so no `docs/DECISIONS.md` line for a library.
  `pyotp` (MIT) was considered: it adds nothing we would not still test ourselves (replay,
  window, rate limit are ours either way). `django-otp` stays rejected (1d 5.3).
- **Clock drift: accept the current step and one step either side** (±30 s). Not more: every
  extra step multiplies the guessing chance. No automatic drift learning. At set-up, a code
  that matches only at 2–10 steps away is not accepted, but the answer says why: "De klok van
  je telefoon of van de server loopt niet gelijk. Zet op je telefoon de tijd op automatisch."
  (409 `second_factor_clock_drift`). The docs remind that the server needs NTP (Synology: on by
  default).
- **Replay**: the row keeps `last_used_step`; a code is accepted only for a step greater than
  that, and the update happens under `select_for_update` in the same transaction as the
  sign-in. So a code seen over a shoulder or in a proxy cannot be used again, not even in the
  same 90-second window.
- The `otpauth://totp/Brievie%20(<host>):<email>?secret=…&issuer=Brievie%20(<host>)` URI and
  the secret as text (groups of four) are shown once at set-up. The QR code is drawn by the
  web (the library it uses for pairing); the server never makes an image of the secret.

### 2. Storage

- `SecondFactor` (server table, not workspace content): `user` (one-to-one), `secret_sealed`
  (bytes), `confirmed_at` (null until confirmed), `last_used_step` (bigint, 0),
  `failed_count` (int), `locked_until` (null), `lockouts` (int, for doubling), timestamps.
- **The secret is sealed** with `apps.common.crypto.seal`, under a key derived from the master
  key: `HKDFExpand(info=b"brievie/second-factor/v1")`, AAD `second-factor/<user_id>`. Derived,
  not the master key itself, so the purpose is bound in the key; same pattern as
  `derive_backup_password`. A database dump without the master key gives no secrets. The
  sealed secret is in the back-up (1d 12).
- `RecoveryCode`: `user`, `code_hash` (char 64), `used_at` (null). Ten codes of ten characters
  from the pairing alphabet, shown `XXXXX-XXXXX` (50 bits each).
- **Besluit: recovery codes hashed with HMAC-SHA256 under a key derived from the master key**
  (`info=b"brievie/recovery-codes/v1"`), not plain SHA-256 and not Django's password hasher.
  50 bits unsalted SHA-256 can be brute-forced from a leaked dump on GPUs in about a day per
  code; with a secret key it cannot. Django's PBKDF2 would make checking ten codes take
  seconds. Comparison in constant time; lookup by hash.
- An unconfirmed `SecondFactor` (set-up started, never finished) is removed after one hour by
  the existing hourly session job.

### 3. The half-signed-in state

The rule from 1d stands: the check sits where a session is created, not on the sign-in page.

- **New: `apps.accounts.signin.begin_sign_in(request, user, *, via, method)`**, called by every
  place that today calls `start_session` (password login, setup, accepting an invitation,
  completing a reset). It decides:
  - no second factor needed → `start_session` as today;
  - the user has a confirmed second factor (and `method` is password) → **pending, step
    `code`**;
  - the user must have one, has none, and the grace period is over → **pending, step
    `setup`**;
  - must have one, has none, within the grace period → `start_session`, plus a notice
    "Zet tweestapsverificatie aan vóór 4 december. Daarna kun je pas inloggen als je het hebt
    aangezet."
- **Pending** is a key in the (not authenticated) Django session: `{user_id, via, method,
  step, started_at, failures}`. The session key is cycled when it is written (no fixation)
  and again by `login()` later. It lives five minutes; then the user starts with the password
  again. Nothing in a pending session is signed in: `request.user` is anonymous, every normal
  endpoint answers 401, `UserSessionMiddleware` has nothing to check.
- **`start_session` gets a guard of its own**: it takes a `second_factor: SecondFactorPassed |
  None` argument and raises when the user needs a second factor and none was passed. A
  `SecondFactorPassed` value is made only by `apps.accounts.second_factor.verify_*` (and by
  `begin_sign_in` for users who need none, and by the SSO path). So a new caller that forgets
  the second step fails in tests, not in production. `start_session` stays the only caller of
  Django's `login`.
- `UserSession` gains `second_factor` (bool, default false): true when this session passed the
  second step or came through SSO. Shown in "Waar je bent ingelogd" ("met tweestapsverificatie")
  and used to end sessions after a grace period (5).
- **Besluit: no session before the code.** The alternative, a full session with a "needs code"
  flag that every endpoint must respect, puts the safety in every endpoint instead of one
  place. The pending key in an anonymous session puts it in one place.

A reset does not bypass the second step: after choosing a new password the user still enters a
code or a recovery code. So an admin who makes a reset link cannot sign in with it.

### 4. Rate limiting and lockout

- The password step keeps django-axes (5 failures, 15 minutes, per user and address).
- The code step, per pending sign-in: 5 wrong codes and the pending state is dropped (back to
  the password, which axes counts).
- The code step, per account (`SecondFactor.failed_count`): 5 wrong codes within 15 minutes
  lock the second step of that account for 15 minutes, doubling with each further lockout
  (`lockouts`) up to 24 hours; a correct code resets both counters. A locked account answers
  429 `second_factor_locked` for code and recovery code alike.
- Why per account and not only per address: an attacker who has the password can switch
  addresses. With ±1 step there are 3 valid codes of a million per attempt; 5 tries per 15
  minutes without doubling would be 480 a day (about 0.14% a day, 40% a year). With doubling it
  is about 35 tries on the first day and 5 a day after that (under 1% a year), and the user
  sees it (below).
- The endpoint also gets Ninja's throttle per client address (10 per minute), in the database
  cache like `PairingThrottle`.
- Every wrong code is an audit row (6) and, after a lockout, a notice the user sees at the next
  sign-in: "Iemand heeft op 20 november met jouw wachtwoord geprobeerd in te loggen, maar had
  de code niet. Verander je wachtwoord als jij dat niet was."

### 5. Who must have it

Optional per account by default. Two layers can require it:

- **Server-wide**: `BRIEVIE_REQUIRE_SECOND_FACTOR` = `none` (default), `server_admins`,
  `everyone`. **Besluit: an environment variable**, like `BRIEVIE_AI_AUTOPILOT_ALLOWED`: there
  is no server settings page yet, the server owner already configures `deploy/.env`, and a
  rule that restrains admins must not be switched off by them in a page. It moves to the
  server settings page when part C builds one.
- **Per workspace**: `require_second_factor` (workspace setting, admin, default off) with
  `grace_days` (0–30, default 7). Audited as `workspace_setting_changed` (`setting` =
  `require_second_factor`, `value` = on/off).
- A second factor is a property of the **account**, which belongs to the whole server. A user
  in two workspaces must have it when **any** of them requires it. Stricter only, never looser,
  so one workspace's admin cannot weaken another's rule. The "Beveiliging" page says why:
  "Werkruimte Kantoor vraagt tweestapsverificatie."
- **Grace**: from the moment a requirement first applies to a user (`required_since`, stored on
  the user, set by the job below or at sign-in), sign-ins within the grace period go through
  with a notice; after it, the pending step `setup` is the only way in. Existing sessions
  without `second_factor` are ended by the hourly session job when the grace period is over
  (audit `session_ended`, `reason` = `second_factor_required`). Server-wide `everyone` has a
  grace of `BRIEVIE_SECOND_FACTOR_GRACE_DAYS` (7).
- SSO sign-ins satisfy every requirement (8).
- While required, a user cannot remove their second factor (409 `second_factor_required`), only
  replace it (new phone).

### 6. Audit (closed list, `apps.audit.codes`)

Reused, now written:

- `second_factor_added` — set-up confirmed (also when it replaces an older one: value
  `replaced`).
- `second_factor_removed` — the user removed it.
- `second_factor_cleared` — an admin or the command cleared it; values `via` (`admin`,
  `command`) and `devices_revoked` (count).

New codes:

- `second_factor_recovery_used` — a recovery code signed somebody in; value `left` (count).
- `second_factor_recovery_renewed` — new recovery codes made (the old ones stop).

Extended (allowed value keys added, additive):

- `sign_in_failed`: new key `step` = `password` / `second_factor` / `recovery_code`.
- `sign_in_locked_out`: `via` gets the value `second_factor`.
- `sign_in_succeeded`: new key `second_factor` (`totp`, `recovery_code`, `none`, `sso`).
- `session_ended`: `reason` gets the value `second_factor_required`.

No code, secret or recovery code is ever a value.

### 7. Lost phone, new phone

- **New phone, old one still there**: "Beveiliging" → "Nieuwe telefoon": a new secret, confirmed
  with a code from the new app, after a code (or recovery code) from the old one. Atomic: the
  old secret works until the new one is confirmed.
- **Lost phone, recovery code at hand**: on the code step "Gebruik een herstelcode". The code is
  used up, the user signs in, the page then pushes "Nieuwe telefoon" and shows how many codes
  are left; at two or fewer: "Je hebt nog 2 herstelcodes. Maak nieuwe aan."
- **Lost phone, no recovery code**: an admin clears the second factor under the rule for
  account actions (1d 6.3): a server admin for anyone; a workspace admin only for a member
  whose single membership is that workspace and who is not a server admin. Clearing:
  - asks the admin's own password again (the admin's session must have `second_factor` true
    when the admin has one);
  - offers, checked by default, to **unlink the member's devices** ("De verloren telefoon kan
    nog bij de post. Ontkoppel de apparaten van Jan."), because the lost phone usually carries
    a paired app;
  - ends the member's sessions;
  - writes `second_factor_cleared` and a notice for the member ("Je tweestapsverificatie is op
    20 november gewist door Anne."), as for a reset link: no unseen takeover;
  - when the member must have it, their next sign-in leads straight to set-up (no new grace).
- **Server admin locked out**: `uv run python manage.py clear_second_factor --email <address>
  [--keep-devices]` on the server, like `reset_password`. Audit `second_factor_cleared` with
  `via` = `command`, actor `system`.

### 8. Devices and SSO

- **Paired iPhones are not asked for a code.** A device token is 256 random bits, kept in the
  phone's Keychain behind the app's own Face ID lock, never typed, expires after 180 days
  unused, and can be revoked by the user or an admin. It is itself a possession factor. What
  matters is how a phone gets one, and that always passes the second step: a pairing code is
  made from a web session (which passed the code when the account has one); proof pairing
  after an invitation uses the session that accepting the invitation created through
  `begin_sign_in`. So with two-step sign-in on, **pairing a new phone needs a web sign-in with
  the code**. Turning it on does not unlink phones that are already paired; the "Beveiliging"
  page lists them with "Ontkoppel" for whoever wants a clean start.
- **SSO**: a sign-in through the identity provider is not asked; the provider does the second
  factor (1d 10). It is a property of the sign-in, not of the account: a password sign-in on
  an account that also has an SSO identity is asked like any other. SSO sessions count as
  `second_factor` true and satisfy requirements; that is the organisation's choice of
  provider. The emergency password sign-in of a server admin (1d 9.4) is asked when that admin
  has a second factor, and `server_admins` makes sure they do.

### 9. Screens and texts (web, B1)

- "Beveiliging" (own account): status "Tweestapsverificatie staat uit/aan"; "Aanzetten";
  "Nieuwe telefoon"; "Nieuwe herstelcodes"; "Uitzetten".
- Set-up: "1. Open een authenticator-app op je telefoon, zoals Microsoft Authenticator, Google
  Authenticator of de app Wachtwoorden op je iPhone. 2. Scan deze code. 3. Typ de 6 cijfers die
  de app laat zien." Then the codes: "Bewaar deze herstelcodes op papier of in je
  wachtwoordmanager. Elke code werkt één keer. Je ziet ze maar één keer." with "Downloaden" and
  "Ik heb ze bewaard" (required before closing).
- Sign-in step: "Typ de code uit je authenticator-app." / "Code klopt niet. Probeer het
  opnieuw." / "Te vaak een verkeerde code. Probeer het over 15 minuten opnieuw." / link
  "Telefoon kwijt? Gebruik een herstelcode."
- Admin, "Leden" → member → "Tweestapsverificatie wissen".
- Workspace settings → "Beveiliging": "Tweestapsverificatie verplicht voor iedereen in deze
  werkruimte" with "Leden hebben … dagen om het aan te zetten."
- Home: nothing is pushed on members. The server admin sees one line on the start page while
  the server has a public address and they have no second factor: "Je server is van buitenaf
  bereikbaar. Zet tweestapsverificatie aan." (1d 19.5).

## Data model

- `SecondFactor`, `RecoveryCode` (2): server tables (`BaseModel`: UUID `id`, `created_at`,
  `updated_at`), no RLS (accounts are not workspace content, like `UserSession`).
- `User.second_factor_required_since` (datetime, null).
- `UserSession.second_factor` (bool, default false).
- Workspace settings: `require_second_factor` (bool, false), `second_factor_grace_days` (int, 7)
  in the existing workspace settings model or a new `WorkspaceSecurity` (builder's choice).
- Settings: `BRIEVIE_REQUIRE_SECOND_FACTOR` (`none`), `BRIEVIE_SECOND_FACTOR_GRACE_DAYS` (7).

## API sketch (additive, `/api/v1`)

- `login` — unchanged request. New answer **202** `SignInStepOut` {`step`: `code` | `setup`,
  `expires_in_seconds`} when a second step follows; 200 `UserOut` as today otherwise. Only the
  web signs in with a password (the app pairs), and the web ships with the server.
- `POST /auth/login/code` — `verifySignInCode` (`auth=None`, pending state required): `code`
  (6 digits) or `recovery_code`. 200 `UserOut` / 401 `second_factor_invalid` / 401
  `sign_in_expired` / 429 `second_factor_locked`.
- `GET /me/second-factor` — `getMySecondFactor` (session): `enabled` (false), `required`
  (false), `required_by` (`none` / `server` / `workspace`; a workspace name list
  `required_by_workspaces`, default empty), `grace_until` (str, nullable, default null),
  `recovery_codes_left` (0).
- `POST /me/second-factor/setup` — `beginSecondFactorSetup`: `secret`, `otpauth_uri`.
  `Cache-Control: no-store`.
- `POST /me/second-factor/setup/confirm` — `confirmSecondFactorSetup`: `code`. Answers
  `recovery_codes` (list of ten). `no-store`. Ends the user's other sessions (1d 7.2).
- `POST /me/second-factor/recovery-codes` — `renewRecoveryCodes`: `code`. `no-store`.
- `DELETE /me/second-factor` — `removeMySecondFactor`: `password`, `code`. 409
  `second_factor_required`.
- `POST /users/{user_id}/second-factor/clear` — `clearUserSecondFactor`: `password` (the
  admin's), `revoke_devices` (true). 403 under the account-action rule.
- `GET`/`PUT /workspaces/{id}/security` — `getWorkspaceSecurity` / `updateWorkspaceSecurity`
  (admin): `require_second_factor`, `grace_days`.
- `UserOut.has_second_factor` (false); `SessionOut.second_factor` (false).

All of these use `auth=session_auth` (a device token gets 401), except that the three set-up
calls also accept the pending state with step `setup` (a small auth class
`PendingSetupAuth`), so a user who must set it up can do so before they have a session. No
`anyOf`; nullable fields are primitives with a default; every new field has a default.

**iOS impact: none.** The app never signs in with a password. Later, the app may link to
"Beveiliging" on the web.

## Security and privacy

- Secrets sealed under a derived key; recovery codes keyed-hashed; neither in a log, an audit
  row or any response after the one that shows them (`no-store`).
- One place decides the second step (`begin_sign_in` + the guard in `start_session`).
- Replay blocked per step; window ±1; per-account lockout with doubling; the user is told.
- Admin clearing is audited, notified, needs the admin's password, and offers to unlink devices.
- Time matters: a server clock that is off breaks codes for everyone. The web compares the
  server's `Date` response header with the browser's clock and, for a server admin, shows a
  warning on the start page when they differ more than 30 s ("De klok van de server loopt
  niet gelijk. Inloggen met een code kan dan mislukken.").

## Home and organisation defaults

| | Home | Organisation |
|---|---|---|
| Second factor | optional, offered under "Beveiliging" | optional, or required per workspace |
| Server-wide rule | `none` | `server_admins` or `everyone` in `deploy/.env` |
| Grace | — | 7 days (0–30) |
| Lost phone | recovery code, or the server owner runs the command | recovery code, or an admin clears it |
| SSO | — | the provider does the second step |

## Risks

- **Locking out the only admin of a home server.** Recovery codes plus the management command
  cover it; the set-up flow cannot be closed before "Ik heb ze bewaard".
- **Support load in offices** (lost phones). The admin clear is two clicks and leaves a trace.
- **Phishing**: TOTP can be relayed by a fake sign-in page in real time. Passkeys fix that;
  they are the next step, not this one.
- **Clock drift** on cheap NAS boxes without NTP: the drift hint and the clock warning surface it.

## Outlook: passkeys (WebAuthn)

Phishing-resistant and nicer to use (Face ID on the iPhone). Not v1 because: a passkey is bound
to one domain (the relying party id), and a Brievie server is reached under several names (a
`.local` name at home, a tunnel name outside, sometimes a bare address, which WebAuthn
refuses); it needs a library (`webauthn` by Duo Labs, BSD-3) and a DECISIONS line; and the
recovery story must exist first, which this design builds. When it comes: a passkey counts as
a second factor (and later as a password replacement), stored next to `SecondFactor`, the same
`begin_sign_in` decides.

## Test approach

- Tests first for `totp.py`: the RFC 6238 SHA-1 vectors, the ±1 window, step boundaries, a code
  for a used step refused, drift detection at 2–10 steps.
- `begin_sign_in` and the `start_session` guard: every caller (login, setup, invitation, reset)
  with and without a second factor, required and in grace; a test that calls `start_session`
  without a pass for a user who needs one and expects the error.
- Pending state: expires after five minutes, five wrong codes drop it, the key is cycled, no
  endpoint but the code and set-up calls accept it.
- Lockout and doubling with a fake clock; audit rows and the notice.
- Recovery codes: hashing with the derived key, one use, constant-time compare, renewal stops
  the old ones.
- Admin clear: authority rule of 6.3, password re-check, devices unlinked, sessions ended,
  notice, audit; the management command.
- SSO sign-in not asked; password sign-in on an SSO-linked account asked.
- Contract test: additive, no `anyOf`, `no-store` on the three secret responses.

## Left to the builder

Helper names, where the workspace setting lives, test layout, migration order, the web
components and the exact Dutch of texts not quoted here, the QR library the web already has.

## Calls made on the owner's behalf

1. Standard library TOTP; no `pyotp` or `django-otp`.
2. The secret sealed under a key derived from the master key (not the master key itself);
   recovery codes keyed-hashed with another derived key.
3. No session before the code: a pending state in an anonymous session, plus a guard inside
   `start_session`.
4. A reset link does not skip the second step.
5. Server-wide rule as an environment variable for now; per-workspace rule as a setting; the
   strictest wins for an account.
6. Per-account lockout with doubling, and a notice to the user after a lockout.
7. Admin clearing offers to unlink the member's devices by default.
8. `login` answers 202 for a second step (web only; iOS unaffected).

## Update 2026-11-21 (plan `2026-11-21-host-tweestapsverificatie.md`)

- **External access** (`2026-11-21-externe-toegang-design.md`, B1/B2): this design is what must
  land before `BRIEVIE_PUBLIC_WEB=true` is a normal choice. Added rule, subject to Stijn's answer
  to B2: a password sign-in on the public route (`apps.access.route.is_public_route`) of a user
  without a confirmed factor is refused (403 `second_factor_needed_outside`, "Zet eerst thuis
  tweestapsverificatie aan."), with no pending `setup` step, so a password alone cannot enrol a
  factor from the internet. The plan then sets `BRIEVIE_SECOND_FACTOR_AVAILABLE = True`.
- **Pairing and re-authentication**: for a user with a factor, `createPairingCode` needs a session
  that passed the second step, and `reauthenticate` takes the code as well as the password. A
  device token is a possession factor (8), so getting one must not be easier than signing in.
- **Clearing a factor** follows `account_authority` as the code has it now (three conditions,
  including "the member reads no private post there"), not the two conditions of 7.
- **Order**: merge point M1 (everything but the per-workspace requirement, "Nieuwe telefoon" and
  the clock warning) precedes public web sign-in; M2 follows.
