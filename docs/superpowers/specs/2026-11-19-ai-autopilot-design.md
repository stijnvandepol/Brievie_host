# AI autopilot: the AI applies instead of only proposing

Date: 2026-11-19. Branch `feat/ai-autopilot` (host). iOS follows separately.

## Intent

Stijn: "ik merk dat hij hem met ai best goed doet ik wil dat hij niet dingen als
voorstellen er in zet maar ook gewoon direct actie kan doorvoeren en hem onder personen
en labels kan zetten maak dit een instelling".

Today the pipeline only proposes: label and addressee proposals are `DocumentSuggestion`
rows, the read values are `Extraction` rows, and a person confirms. This adds an opt-in
per workspace where Brievie carries those proposals out itself.

## Goals, non-goals, anti-goals

- Goal: two switches per workspace, both off by default. (a) `auto_organise`: put labels
  and addressees on the document. (b) `auto_confirm`: confirm the letter (sender, type,
  letter date, summary, tasks with deadlines).
- Goal: everything automatic is recognisable, reversible, and never overrides a person.
- Non-goal: iOS screens, a per-workspace prohibition by the server admin, a learning loop,
  CLI flags for the switches.
- Anti-goal: a made-up actor (no user is recorded as having done what nobody did), a
  made-up confidence, an automatic change that returns after a person removed it.

## Approaches

1. **Apply inside the pipeline's last transaction (chosen).** The pipeline already holds
   the document's row lock when it stores proposals and runs the rules; rules in mode
   `auto` already apply changes there through `organise` with `actor=None`. Autopilot is
   one more step in that place. No new job, no race with a person: a document a person
   touched is no longer `processing` and the pipeline leaves it alone already.
2. A separate job after processing. Needs its own locking and its own answer to "what if a
   person confirmed meanwhile"; buys nothing.
3. Let the clients auto-accept. Wrong place: needs a device to be on, and every client
   would repeat the guard rails.

## Design

### Settings

- `AISettings.auto_organise` and `AISettings.auto_confirm` (booleans, default false). Only
  `apps.pipeline.settings_service` writes them (`configure_autopilot`).
- Only a workspace admin changes them; only while the workspace's AI mode is not `off`.
- Switching a switch **on** needs `acknowledged=True`: the caller showed the notice
  (`autopilot_notice()`, one Dutch text served by the API so every client shows the same):
  nobody checks, mistakes can happen, the deadlines are in the letter itself. Without it:
  409 `ai_autopilot_ack_required`. Switching off needs nothing.
- Every changed switch writes one audit row `workspace_setting_changed` with
  `setting` = `ai_auto_organise` / `ai_auto_confirm` and `value` = true/false (who and when
  are the row's own fields).
- Server-wide: `BRIEVIE_AI_AUTOPILOT_ALLOWED` (env, default true). When false, no switch
  can be switched on (409 `ai_autopilot_forbidden`) and the pipeline applies nothing, even
  where a switch is still stored as on.

### (a) Organise automatically

After the proposals are made and the rules ran (`organise_processed`), when
`auto_organise` is in force, each still-open proposal that qualifies is carried out
through `organise.add_label` / `organise.add_addressee` with `actor=None`:

- origin `ai` with confidence >= `AUTO_APPLY_FROM` (0.700; the model's own `zekerheid`,
  clamped and capped at 0.800 as today), or
- origin `name_match` with confidence >= `PRESELECT_FROM` (the existing "strong match").

The history event gets origin `ai` or `name_match` (new values), the proposal becomes
`accepted`, and the link row is marked `automatic`. A proposal whose origin is `rule`
stays a proposal: the user wrote that rule in mode "propose". The existing limits (30
labels, 10 addressees) hold. `suggestions.propose` already refuses a dismissed change, so
nothing a person said no to comes back.

### (b) Confirm automatically

When `auto_confirm` is in force, the pipeline confirms the document instead of leaving it
`needs_review`, but only when a pure decision function says every guard rail holds. It
keeps the document a proposal when any of these is true:

- no AI answer (AI off, the call failed, the answer was unusable);
- no sender, or the sender was not found in the letter, or its confidence < 0.850;
- no letter date, or it was not found in the letter / not plausible, or confidence < 0.850;
- the model's document type or an action type is not one of the known words;
- an action without description; more than 20 actions;
- a deadline that was given but not found in the letter or not plausible, with confidence
  < 0.850, in the past, or before the letter date;
- with `auto_organise` also on: a proposal is still open after (a) (a weak person or label
  match, or a rule that asks) — the person is not unambiguous.

0.850 is the existing `NEEDS_ATTENTION_BELOW`. The signals are the ones that exist: the
model's `zekerheid` per sourced value, and the deterministic checks (quote literally in
the letter, the date in the quote, plausibility). A generated value (type, summary,
description) has no source and no real confidence; it is taken as the model gave it.

The confirmation writes what a person's confirm writes (same code path): the fields, the
tasks (deadline source `in_document` with its quote, or no deadline), a `confirmed` event,
then the rules of trigger `confirmed`. Differences: `confirmed_by` stays null,
`Document.confirmed_by_ai` is true, the event has origin `ai` and no actor, each task has
`created_by_ai` true, and labels/addressees are not set by the confirmation (that is (a)).
A failure inside it rolls back to "needs review".

### A person always wins

- The pipeline only touches a `processing` document, so nothing a person confirmed or is
  editing is overwritten.
- Undo (`undoDocumentEvent`) now also reverses an automatic `label_added` /
  `addressee_added` of origin `ai` or `name_match`, and records the "no" as before.
- New `reopenDocument`: a document the AI confirmed goes back to `needs_review`: the
  confirmed fields are emptied, the AI's tasks that are still open are removed (a task a
  person ticked off stays), `confirmed_by_ai` becomes false, and a `reopened` event names
  the person. The stored proposals are still there, so the normal review follows. Only for
  a document with `confirmed_by_ai`; anything else answers 409 `document_not_reopenable`.
- AI switched off, or consent given for another external address: both switches go off
  (audited), so nothing resumes silently with another service.

### API (additive)

- `AiStatusOut`: `auto_organise`, `auto_confirm` (default false), `autopilot_allowed`
  (default true).
- `GET /workspaces/{id}/ai/autopilot` (`getAiAutopilot`): the switches, `allowed`, `notice`.
  `PUT` (`updateAiAutopilot`, admin, web session): `auto_organise`, `auto_confirm`,
  `acknowledged`.
- `DocumentSummaryOut`: `confirmed_by_ai` (false), `auto_label_ids`, `auto_addressee_ids`
  (empty lists): what is on the document now because Brievie put it there itself.
- `listDocuments`: `automatic` (bool, default false) narrows to documents with
  `confirmed_by_ai` or an automatic label/addressee. `InboxCountsOut.automatic` (0).
- `DocumentEventOut`: origins `ai`, `name_match`; kind `reopened`; `can_undo` true for the
  automatic adds. `TaskOut.created_by_ai` (false).
- `POST /workspaces/{id}/documents/{document_id}/reopen` (`reopenDocument`).

### Web

- AI page: a section "Automatisch verwerken" with the two switches (shown only while AI is
  on and the server allows it), a confirmation dialog with the notice when switching on.
- Documents: a badge "Automatisch door AI" in the list and on the document; the history
  shows the automatic lines with undo; a button "Zelf controleren" (reopen) on a document
  the AI confirmed; a filter "Automatisch door AI" on the post page.

## Rulings

1. Server-wide prohibition is an environment variable, not a table: there is no server
   settings model, the server owner already configures the server in `deploy/.env`, and a
   switch that forbids must not be changeable by the people it restrains. Default true:
   at home the admin of the workspace is the server owner. A per-workspace prohibition
   waits for the enterprise layer. Cost if wrong: an enterprise admin edits a file instead
   of a page.
2. Name matches are applied by (a) too and are recorded as `name_match`, not as `ai`: it
   is not the AI that found them. The badge still reads "Automatisch door AI" (one name
   for the feature); the history line says which it was.
3. Rule proposals are never auto-applied (the rule's author chose "propose").
4. A task without deadline is confirmed automatically: a missed deadline cannot be told
   from a letter without one. Stated in the notice and in PRIVACY/SPEC as a limit.
5. When processing fails nothing is applied automatically, not even name matches.
6. No per-workspace `AIConsentEvent` row: that table records consent about where text
   goes; the switches are settings and go to the audit log, which the app role cannot
   change or delete either.
7. The web has no review screen; "Zelf controleren" there reopens and says the check is
   done in the app.
8. The summary is stored with `summary_by_ai` true, as a person's confirm of the AI's
   text does.

## Left to the builder

Names of private helpers, test layout, the exact Dutch of labels other than the notice and
the badge, the order of migrations.
