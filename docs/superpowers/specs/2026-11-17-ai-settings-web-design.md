# AI settings in the web interface: design

Date: 2026-11-17. Follows `2026-11-17-ai-consent-design.md`. Based on `main` (8aedaba).
The owner delegated the choices; each one is recorded here with its reason.

## 1. Intent

Switching AI on is a terminal command today. For somebody at home that is not a
reasonable road. The administrator of a workspace must be able to do it in the web
interface, with exactly the same notice and the same recorded consent as the command.
Members and viewers see the state and nothing to press.

Non-goals: a history screen, consent per member, choosing a model per document.
Anti-goals: a second implementation of the rules beside `settings_service.configure`;
an API key that ever travels back to a browser; a consent the client can give for
something it was not shown.

## 2. Approaches

1. **The client builds the notice** from its own texts and sends `consent: true`.
   Rejected: two texts that drift apart, and a `true` says nothing about what was read.
2. **The server builds the notice; the client echoes what it agreed to. Chosen.**
   `preview` returns the notice the command prints, with the address it is about.
   `update` carries `consent_address`; the server resolves the request itself and
   refuses when the address it would show differs. A consent for service A can
   therefore never switch on service B.
3. **A signed consent token** from `preview`. Stronger binding to the exact text, but
   more machinery than a single-admin product needs; the address comparison already
   covers what a consent is about (one address).

## 3. Design

**Service** (`apps.pipeline.settings_service`, the one place for the rules):
`external_notice` moves here from the command, so the command and the API print the
same text (its last line no longer names `--mode off`). `ValueError`s become
`AiSettingsError` with a code, so the API can answer in Dutch. `configure` keeps the
stored key when `api_key` is `None` and the address does not change; a key never
moves to another address.

**API** (all under `/api/v1/workspaces/{workspace_id}`):

| Operation | Role | What |
|---|---|---|
| `getAiStatus` `GET /ai` | viewer | Gains `has_api_key: bool = False` and, for an administrator, `model`. |
| `listAiSuppliers` `GET /ai/suppliers` | viewer | Per supplier: `key`, `name`, `country`, `host`, `default_model`. |
| `previewAiSettings` `POST /ai/preview` | admin | Checks the settings, changes nothing. Returns `needs_consent`, the `notice` text, and `address`. |
| `updateAiSettings` `PUT /ai` | admin | Calls `configure` with `Actor(user, "api")`. Body: `mode`, `supplier`, `base_url`, `model`, `api_key`, `consent_address`. Returns `AiStatusOut`. |

`updateAiSettings` answers 409 `ai_consent_required` when consent is needed and
`consent_address` is not exactly the address the server resolves; 422 `ai_settings_invalid`
(with the Dutch reason) for settings that do not pass `resolve`. It is throttled per
caller and workspace (10 per 10 minutes; changed after review from per client address). Switching off needs no
consent. The key is write-only: no response and no log line holds it.

**Web.** A page "AI" in the workspace navigation, for every member. It shows the
status line. An administrator also gets a form: three choices in plain words (uit, op
je eigen netwerk, externe dienst); for external the supplier with its country and a
key field; for local the address and model; behind "Geavanceerd" a model name and an
own OpenAI-compatible address. "Opslaan" asks `preview`; when consent is needed a
dialog shows the server's notice with a checkbox "Ik heb dit gelezen en ga akkoord"
and the button stays disabled until it is ticked.

## 4. Choices

1. **No "Test de verbinding" button now.** It would be worth having only if it sends
   no letter text, which is possible (a fixed one-word prompt). But it needs a new
   provider method for both APIs and an outbound call from inside a web request, where
   the request transaction is open. A wrong key already shows at the first letter as a
   clear failure (`ai_key_rejected`). Left for the wizard (1d).
2. **The model is returned to an administrator only** (changed after review: an empty
   field that means "the default" would silently reset a chosen model when only the key
   is replaced). The full address is never returned.
3. **The checkbox instead of typing `akkoord`.** A typed word in a browser is friction
   without more meaning; the dialog, the checkbox and the server's check together are
   the explicit act.
4. **Members see the page** (state only), so the state has one fixed home.
