# Recorded consent for AI: design

Date: 2026-11-17. Covers `Brievie_host` (backend and `web/`). Based on `main`
(phase 1c is merged).

## 1. Intent

With mode `external` the BSN-masked text of every letter, plus the names of the
workspace's labels and the names and name variants of its persons, goes to an AI
service. Today `configure_ai` switches that on without saying so, nothing records who
decided, and the web interface only mentions it on the labels and persons pages. The
owner wants the product itself to ask and to remember: no letter is read by an outside
service unless somebody was told exactly what leaves the server and said yes.

Non-goals: a web settings screen for AI (comes with the wizard, 1d), consent per
letter or per member, a general audit log.
Anti-goal: a confirmation that is ceremony only. The record must be what lets the
pipeline send text, not a note beside it.

## 2. Approaches

1. **Columns on `AISettings`** (`consented_at`, `consented_by`). Small, but the row is
   overwritten: withdrawal and a change of supplier leave no trace. Rejected.
2. **An append-only table `AIConsentEvent`, chosen.** One row per decision; the latest
   row is the current state. Gives the history the task asks for and one place for the
   API to read "since when".
3. **A generic audit log** with AI as its first user. Wanted later for enterprise
   (SSO, roles, audit log), but its shape is not known yet; building it now is guessing.

## 3. Design

**Table `pipeline_aiconsentevent`** (`WorkspaceModel`, RLS enabled and forced, never
updated or deleted by the application):
`event` (`granted`, `changed`, `switched_off`), `mode`, `supplier` (`mistral`, `openai`,
`claude`, `custom`, or empty), `host` (host of the base URL only), `model`,
`actor` (FK to user, null) and `actor_label` (`command line` when there is no user).
`created_at` is the moment. The state in force is `AISettings.consented_base_url`
(added after review: no dependence on row order or the clock). No API key, no path or query of the URL, no document text.

**Service.** `settings_service.configure` gains `consent: Consent | None`
(`Consent(actor, actor_label)`; built as `actor: Actor` and `consent: bool`). It writes the settings and the event in one
transaction and raises when a switch to `external` comes without consent. `supplier`
becomes `custom` when the base URL's host is not the preset's host, so a Mistral label
never sits on somebody else's server.

**Command.** Before switching to `external`, `configure_ai` prints in Dutch: what is
sent per letter (masked text, names of labels, names and name variants of persons;
never the page images, ids or rules), to which service, host and country (Mistral:
France, EU; OpenAI and Claude: United States; custom: unknown, look it up yourself),
that the service's own terms and retention apply, and that the administrator decides
for everyone whose post is in the workspace. Then it asks to type `akkoord`. Anything
else, or no terminal, is a refusal with a non-zero exit. Scripts pass
`--accept-external-ai`; the notice is still printed. Mode `off` writes `switched_off`
when AI was on.

**API.** `AiStatusOut` (`GET /workspaces/{id}/ai`, every member) gains optional,
nullable primitives with a default: `supplier`, `host`, `since` (datetime of the
current consent). Still no model, full address or key. The 1b contract is unchanged.

**Web.** One line every member sees: "AI aan: tekst van brieven gaat naar Mistral
(api.mistral.ai, Frankrijk), ingesteld op 17 november 2026." With `local`: "AI aan op
het eigen netwerk: er gaat niets naar buiten." With `off`: "AI uit."

## 4. Choices put to the owner (2026-11-17: all four taken as proposed)

1. **Consent for `local` too?** Proposal: no question (nothing leaves the network), but
   the event is recorded and a one-line notice is printed.
2. **Ask again on a change?** Proposal: a new confirmation whenever the host changes
   (built stricter after review: whenever the address changes, so also port or path)
   (the text goes to another company). A new model or key on the same host is recorded
   as `changed` without a question.
3. **Notice for every member or only the administrator?** Proposal: the line is
   visible to every member, always; no per-member "gelezen"-click until members can be
   invited (1d), because a workspace has one account today.
4. **Does the pipeline enforce it?** Proposal: yes. `provider_for` gives an external
   provider only when the latest event is a consent for the configured host; otherwise
   the document fails with `ai_not_configured`. A workspace already on `external`
   therefore stops reading letters after the upgrade until `configure_ai` is run once.

## 5. Left to the builder

Exact Dutch wording (B1), where in the web shell the line sits (built: under the title
of the Inbox and Post pages), names of tests.
