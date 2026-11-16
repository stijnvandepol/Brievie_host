# Phase 1c "Ordenen": design

Date: 2026-11-16. Covers `Brievie_host` (backend and `web/`) and `Brievie_ios`.
Plans: `docs/superpowers/plans/2026-11-16-host-fase-1c.md` and, in the iOS repo,
`docs/superpowers/plans/2026-11-16-ios-fase-1c.md`.

## 1. Intent

After 1b a letter can be scanned, read by the AI, reviewed in the app and turned into
tasks. It then sits in one flat list that only the app can show. Phase 1c makes the
post findable and makes it sort itself:

- you can see the post in the web interface;
- everything scanned lands in an inbox and then moves to somebody's name
  ("deze brief is voor papa"), by hand, by a rule, or on a proposal from the AI;
- you make your own labels (for example Belastingdienst) and get a starting set that
  fits the kind of workspace; labels are also proposed or applied automatically;
- a tab "Automatisering" where you say: when these words or this sender occur, put
  this label on it or put it on this person's name;
- deadlines appear in your calendar.

The owner's reason: a household's post belongs to different people and to different
subjects, and nobody wants to file by hand. Sorting must cost at most one tap, and a
wrong automatic choice must be visible and reversible.

## 2. Decisions taken with the owner (2026-11-16)

1. **A person can be linked to a member.** Post is always on the name of a `Person`.
   Every member gets a person automatically; a person without an account can be linked
   to an account later and keeps all their post.
2. **Everyone in the workspace sees every document.** There is exactly one account per
   workspace today (inviting members does not exist yet), so restricting does nothing.
   "Privé" and "Zorg is private by default" (spec 14) arrive together with inviting
   members (1d).
3. **Proposal first, automatic per rule.** A label or addressee from the AI is always a
   proposal the user confirms. A rule starts in mode `propose`; the user can switch a
   rule to `auto`. Every change is in the document's history and can be undone.
4. **Calendar through EventKit in the iPhone app.** A subscribed ICS feed cannot be
   fetched by Google or Outlook from a home server on a private address. An ICS feed
   per member can follow once outside access exists.
5. **Label presets** (section 7.3): ten labels for Thuis, nine for Zakelijk, created
   with the workspace and offered again for an existing one. Departments are not in the
   preset: the user adds several labels at once ("Sales, Finance, HR"), also at setup.
6. **Scope.** 1c is persons, inbox, post in the web, labels, automatic labelling,
   rules, calendar, and ticking off a task (a paid bill must leave the calendar).
   Folders, NL rules (calculated deadlines), search, the Today screen and local
   notifications move to a follow-up step.

## 3. Goals, non-goals, anti-goals

**Goals**

- G1. See the post in the web: list, filters, a document with its page images, text,
  summary, tasks and history.
- G2. Persons, "op naam van" (one or more persons per document), by hand in web and app.
- G3. An inbox with three states: new, to review, without owner.
- G4. Own labels with a colour, presets per workspace kind, managed in the web, shown
  and edited on a document in web and app.
- G5. Proposals for addressee and labels in two layers: deterministic first (name
  variants of persons, rules), then the AI choosing from a closed list.
- G6. A rules engine as a pure function, a rule builder with "test on existing
  documents", a tab "Automatisering", history and undo of what rules did.
- G7. Open tasks with a deadline in the phone's calendar; tasks can be ticked off.

**Non-goals for 1c** (not built, not prepared beyond what is named here)

- Money: entries, amounts as rule conditions, the household book.
- IMAP, Berichtenbox notifications, watch folder, web upload, server OCR.
- Full-text search. (Filters on person, label and inbox state are in.)
- Folders on documents and the rule action "move to folder"; folder templates.
- NL rules and calculated deadlines; the rule action "create task".
- Visibility per document (`document_acl`), the rule action "change visibility",
  "Zorg private by default". See decision 2.
- Relations (business): phase 2. Persons exist in both workspace kinds.
- Inviting members, the Leden screen. 1c only prepares the link person ↔ member.
- Learned rule suggestions ("three times the same correction", spec 9). The history
  records hand corrections, so this can be built on it later.
- Reviewing and confirming a document in the web: that stays in the app. The web shows
  a document and lets you change addressees, labels and task status.
- The Today screen, the tab bar and local notifications in the app. Calendar alarms
  give the reminders for now.
- Rule conditions on amount, date range and AI confidence; nested condition groups;
  the rule actions remove label, assign to member, create entry, send notification.
  (Removing a label as a proposal has no sensible form on the review screen; the
  rule format is versioned so it can be added.)
- History and undo in the app (web only). Managing labels and rules in the app.
- An ICS feed.

**Anti-goals** (would make 1c a failure even if it works)

- A label or person that the AI invented: only ids from the closed list are ever stored.
- A rule that changes documents silently: every rule change is an event in the history
  with the rule's name, and can be undone.
- A rule or proposal that comes back after the user said no on that document.
- Document text, a name or a label name in a log line.
- A breaking change to `/api/v1`: the 1b app must keep working against a 1c server.

## 4. Constraints

From `CLAUDE.md` and `docs/DECISIONS.md`, unchanged: English code, Dutch B1 text through
`gettext` and Lingui; UUID `id`, `created_at`, `updated_at`; every new table subclasses
`WorkspaceModel` with RLS enabled and forced and `same_workspace_fields` for its foreign
keys; `mypy --strict`; additive API with camelCase `operation_id`; error body
`{"code", "message"}`; thin endpoints, one service function, `transaction.atomic`; jobs
take ids only and open short workspace contexts, none during an external call; text is
BSN-masked before it is stored, logged or sent; nullable API fields are primitives and
nullable inputs have a default; no `anyOf` in the contract; no new dependency without
a decision line. 1c needs **no new dependency** in the host or the web. In the app it
uses EventKit, which ships with iOS.

Web: the strict CSP stays as it is (`img-src 'self' data:`, `style-src 'self'`, no
inline styles). That shapes two things: page images are plain `<img>` elements whose
`src` is the existing authenticated endpoint (no blob URLs), and label colours come
from a fixed palette of CSS classes (no free colour picker).

## 5. Approaches considered

**A. Label keywords on the label, rules as a separate, later layer.** Each label holds
its own keyword list; the pipeline matches them; the rules engine comes on top for
everything else. Simple to start, but two mechanisms do the same thing (text contains →
label), with two places to look when a label appears, and the Automatisering tab would
not show the most common automation.

**B. One mechanism: the rules engine is the deterministic layer (chosen).** A preset
creates labels *and* the rules that recognise them ("tekst bevat het woord APK → label
Auto", mode `propose`). Everything automatic except name matching is a rule the user can
see, change, switch to automatic or delete on one tab. Name matching of persons stays
built in, because it is driven by the name variants on the person and needs no
configuration.

**C. AI only.** Send the label and person list to the AI and let it sort. No rules
engine in 1c. Dropped: it does nothing with AI `off` (spec acceptance: rules work
without AI), it is not deterministic, and the owner asked for the Automatisering tab.

**Where proposals live.** Considered: more `Extraction` fields (`label`, `addressee`)
with the id as text. Chosen: a table `DocumentSuggestion` with real foreign keys and a
state. A suggestion outlives the review (a rule can propose on a confirmed document),
must remember "dismissed", and must disappear with its label or person; `Extraction`
rows are free text and are replaced on every pipeline run.

**Apply before or after the review.** Considered: nothing is applied until the user
confirms the review; automatic rules only pre-select. Chosen: an `auto` rule really
applies when processing ends, so the inbox already shows "Papa" and the labels, and the
history says which rule did it. Everything else waits as a suggestion.

## 6. Persons and "op naam van"

### 6.1 Model (`apps.people`)

`Person(WorkspaceModel)`:

| Field | Notes |
|---|---|
| `display_name` | 1–150 characters, trimmed, BSN-masked; unique per workspace ignoring case |
| `name_variants` | JSON list of strings (0–20, each 2–100 characters): how the name is written on letters ("J. de Vries", "Jan de Vries") |
| `user` | FK to `accounts.User`, nullable, `SET_NULL`; unique per workspace when set. The linked member |
| `archived_at` | nullable; an archived person stays on their documents but is not offered, matched or proposed |

`DocumentAddressee(WorkspaceModel)` in `apps.documents` (spec table `document_person`;
the glossary's term is addressee): `document` (CASCADE), `person` (PROTECT), unique
together. A document has zero or more addressees ("voor papa en mama").

`Person.user` points outside the workspace tables, so `save()` checks that the user has
a membership of this workspace; the other foreign keys use `same_workspace_fields`.

**Member ↔ person.** `people.services.ensure_person_for_member(workspace, user)` creates
(or finds) the person linked to a member: display name and first variant are the user's
name. `create_workspace` calls it for the admin. A data migration does it for every
existing membership (it enters each workspace's RLS context, because the owner role is
under forced RLS too). Linking an existing person to a member is
`PUT …/persons/{id}/member` (admin). The invitation flow of 1d must offer "this is an
existing person" and link instead of creating a second one; that is the reason the link
is built now.

Persons exist in Thuis and Zakelijk workspaces. The spec lists them under Thuis only;
in a business a letter is just as often for one colleague, and relations (phase 2) are
the sender side, not the addressee side.

### 6.2 API

All under `/api/v1/workspaces/{workspace_id}`; reading needs `viewer`, changing `member`
unless stated.

| Operation | Method and path | Notes |
|---|---|---|
| `listPersons` | `GET /persons` | `PersonOut {id, display_name, name_variants, user_id (uuid or null), archived, document_count}`; archived ones last |
| `createPerson` | `POST /persons` | `PersonIn {display_name, name_variants = [], archived = false}`; 409 `person_exists` |
| `updatePerson` | `PUT /persons/{person_id}` | whole body, same schema |
| `deletePerson` | `DELETE /persons/{person_id}` | 204; 409 `person_in_use` when a document or a rule refers to them (archive instead) or when linked to a member |
| `linkPersonToMember` | `PUT /persons/{person_id}/member` | admin; `{user_id: uuid or null = null}`; null unlinks; 409 `member_already_linked`; 404 `member_not_found` |
| `listMembers` | `GET /members` | `MemberOut {user_id, name, role, person_id (uuid or null)}`; needed to pick the member to link |
| `setDocumentAddressees` | `PUT /documents/{document_id}/addressees` | `{person_ids: [uuid]}` (0–10): the document's addressees become exactly this list; returns `DocumentOut`; 409 `document_processing` while processing; 404 `person_not_found`; an archived person cannot be added |

`DocumentSummaryOut` gains `addressee_ids: [uuid]`, `label_ids: [uuid]`,
`inbox_state` and `open_suggestion_count`. Clients join ids with `listPersons` and
`listLabels` themselves.

### 6.3 Name matching (deterministic, `apps.people.matching`)

A pure function: `match_addressees(text, persons) -> list[NameMatch(person_id, quote,
confidence)]`. No database, no network.

- Names are the display name plus the variants of every non-archived person. A name
  shorter than 3 characters is ignored.
- Text and names are normalised the same way (case folded, accents removed, whitespace
  collapsed; `apps.common.text.normalise`, shared with the rules engine). A name matches
  as a whole phrase on word boundaries: "Jan de Vries" does not match "Jan de Vriesema".
- Confidence: 0.95 when an address word stands directly before the name on the same
  line, within 40 characters, with nothing between them but whitespace, punctuation
  and other address words (`t.a.v.`, `ter attentie van`, `aan`, `de heer`, `dhr.`,
  `mevrouw`, `mevr.`, `geachte`, `beste`, `familie`, `fam.`): "T.a.v. de heer J. de
  Vries" counts, "de beste in hypotheken voor Jan de Vries" does not; 0.85 when the
  name stands in the first 600 characters of the first page (the address block);
  0.60 elsewhere.
- `quote` is the matched words with up to 40 characters of the letter before them, so
  "waar staat dit?" works.
- The longest match per person wins; several persons can match one letter.

The result is always a suggestion (section 8). A name match of 0.85 or higher is
**preselected** on the review screen: one tap on "Bevestigen" accepts it. This is a
builder's call inside decision 3: it stays a proposal the user confirms, and the quote
shows why.

## 7. Labels

### 7.1 Model (`apps.labels`)

`Label(WorkspaceModel)`:

| Field | Notes |
|---|---|
| `name` | 1–50 characters, trimmed, BSN-masked; unique per workspace ignoring case |
| `color` | one of `grey, blue, teal, green, yellow, orange, red, purple` (default `grey`) |
| `system_key` | empty for an own label; for a preset label its stable key (`belastingdienst`, `zorg`, …), unique per workspace when not empty |

`DocumentLabel(WorkspaceModel)` in `apps.documents`: `document` (CASCADE), `label`
(CASCADE), unique together.

**System versus own.** A system label is one that came from a preset; `system_key`
lets later features find it whatever the user renamed it to (Zorg private by default,
the tax-return folder). The user may rename, recolour and delete it like any label. An
own label has no key. Nothing else differs.

**Colour.** Yes, from a fixed palette of eight named colours. A label is always shown
with its name; colour is never the only signal (WCAG 1.4.1). The web has one CSS class
per colour (`label-blue`), the app maps the key to a colour with enough contrast in
light and dark mode; an unknown key (a later palette) shows as grey.

Deleting a label removes it from its documents (the UI says from how many), from its
suggestions, and deletes the preset rule that belongs to it (the rule with the same
`system_key`). It is refused with 409 `label_in_use` while any other rule names it in a
condition or an action; the message names that rule. Deleting a label writes no event
per document: earlier events keep the label's name as text.

### 7.2 API

| Operation | Method and path | Notes |
|---|---|---|
| `listLabels` | `GET /labels` | `LabelOut {id, name, color, system_key, document_count}`, by name |
| `createLabel` | `POST /labels` | `LabelIn {name, color = "grey"}`; 409 `label_exists` |
| `updateLabel` | `PUT /labels/{label_id}` | |
| `deleteLabel` | `DELETE /labels/{label_id}` | 204; 409 `label_in_use` |
| `applyLabelPreset` | `POST /labels/preset` | admin; creates the preset labels (and, from slice 5, their rules) that are missing; idempotent; returns the label list |
| `setDocumentLabels` | `PUT /documents/{document_id}/labels` | `{label_ids: [uuid]}` (0–30): exactly this set; `DocumentOut`; 409 `document_processing`; 404 `label_not_found` |

Adding several labels at once is a client matter: the web splits "Sales, Finance, HR"
on commas and calls `createLabel` per name, skipping names that exist.

### 7.3 Presets

Data in `apps/labels/presets.py`: per workspace kind a list of
`PresetLabel(key, name, color, recognise)`. `recognise` becomes one rule per label
(section 9.6), mode `propose`, match `any`.

**Thuis**

| Label | Colour | Recognised by |
|---|---|---|
| Belastingdienst | blue | words: belastingdienst, toeslagen |
| Zorg | red | words: zorgverzekering, zorgverzekeraar, CAK, huisarts, ziekenhuis, apotheek, tandarts |
| Verzekering | purple | words: polis, polisblad, verzekering, verzekeringen |
| Wonen | green | words: hypotheek, huur, huurovereenkomst, WOZ, VvE |
| Energie en water | yellow | words: energie, gas, stroom, waterbedrijf, drinkwater |
| Auto | teal | words: APK, RDW, kenteken, wegenbelasting, motorrijtuigenbelasting |
| Bank | grey | sender contains "bank"; words: rekeningafschrift, creditcard |
| Inkomen | green | words: loonstrook, jaaropgave, UWV, SVB, pensioen |
| Gemeente | orange | words: gemeente, gemeentelijke belastingen, waterschap |
| Abonnementen | teal | text contains "abonnement"; words: contributie, lidmaatschap |

**Zakelijk**

| Label | Colour | Recognised by |
|---|---|---|
| Belastingdienst | blue | words: belastingdienst, naheffing, aangifte |
| Inkoop | orange | document type is invoice |
| Verkoop | green | words: offerte, opdrachtbevestiging |
| Bank | grey | sender contains "bank"; words: rekeningafschrift, creditcard |
| Personeel | purple | words: loonstrook, loonheffing, pensioenfonds, UWV, arbo |
| Verzekering | purple | words: polis, polisblad, verzekering, verzekeringen |
| Contracten | teal | document type is contract |
| KVK en overheid | grey | sender has the word KVK, RVO or "Kamer van Koophandel"; sender contains "gemeente" |
| Huisvesting | yellow | words: huur, huurovereenkomst, energie, VvE |

The label names are the owner's choice. The recognition words differ from the first
sketch on purpose (builder's call): `btw`, `bank`, `telefoon`, `internet` and `KVK` as
words in the letter text stand on nearly every invoice or letterhead and would put the
label on everything, so they are dropped or moved to the sender. For the same reason
Contracten is recognised by the document type only: "overeenkomst" and "algemene
voorwaarden" stand in the footer of almost every supplier invoice. A test runs every
preset over a small set of sample letters: an ordinary invoice from a webshop may get
no label in Thuis and only Inkoop in Zakelijk.

**When.** `create_workspace` applies the preset of the workspace's kind. `SetupIn` gains
`extra_labels: [string] = []` (0–20 names; the setup form shows the field "Afdelingen"
for Zakelijk) which become own labels. For a workspace that exists already, the Labels
page shows "Startset toevoegen" while a preset label is missing; `applyLabelPreset`
adds only what is missing and never touches a label that exists (matched on
`system_key`, then on name).

## 8. Suggestions: one place for every proposal

`DocumentSuggestion(WorkspaceModel)` in `apps.documents`:

| Field | Notes |
|---|---|
| `document` | CASCADE |
| `action` | `add_label`, `add_addressee` |
| `label` / `person` | nullable FKs (CASCADE); exactly one is set, matching the action (check constraint) |
| `origin` | `rule`, `name_match`, `ai` |
| `rule` | nullable FK (`SET_NULL`), plus `rule_name` as text |
| `quote` | the words in the letter that led to it; may be empty (BSN-masked) |
| `confidence` | 0–1, three decimals; a rule's proposal has 1.000 |
| `preselected` | true once a name match of 0.85 or higher contributed to this row |
| `state` | `open`, `accepted`, `dismissed` |

Unique on (`document`, `action`, `label`, `person`) with `nulls_distinct=False` (one of
the two foreign keys is always null): one suggestion per possible change per document. When two origins propose the same thing, the row keeps the strongest
origin (`rule` over `name_match` over `ai`) and the highest confidence.

**`dismissed` is the document's memory of "no".** It is set when the user dismisses a
suggestion, leaves a shown suggestion out when confirming the review, removes by hand
something a rule or suggestion had added, or undoes a rule action. Nothing automatic
ever proposes or applies a dismissed change on that document again. Adding the same
thing by hand later is always possible (and sets the row to `accepted`).

API: `DocumentOut` gains `suggestions: [SuggestionOut {id, action, label_id (uuid or
null), person_id (uuid or null), origin, rule_name, quote, confidence (decimal string),
preselected}]` with the open ones only. `preselected` comes from the server (a name
match of 0.85 or higher; never for a rule's or the AI's proposal alone), so web and app
agree. `acceptSuggestion` and
`dismissSuggestion` are `POST /documents/{document_id}/suggestions/{suggestion_id}/accept`
and `…/dismiss` (member; both idempotent; return `DocumentOut`).

**The review.** `ConfirmIn` gains three fields with defaults: `sets_organisation: bool =
false`, `label_ids: [uuid] = []`, `addressee_ids: [uuid] = []`. With `true`, confirm
makes the document's labels and addressees exactly these lists in the same
transaction: open suggestions whose change is in the lists become `accepted`, the other
open ones `dismissed`. With `false` (the 1b app, which does not know these fields)
labels, addressees and suggestions are left alone. A flag instead of nullable lists,
because a nullable list is not a primitive (contract rule). `Document` gains `confirmed_organisation` (JSON, null when
confirmed without the flag): the sorted label and addressee ids as the user confirmed
them. The idempotent repeat of a confirm compares the request with that snapshot, not
with the document's current labels, because the rule run that follows a confirm may add
to them.

## 9. Rules engine and the tab "Automatisering"

### 9.1 Model (`apps.rules`)

`Rule(WorkspaceModel)`: `position` (order, unique per workspace), `name` (1–100),
`active`, `mode` (`propose` | `auto`), `stop`, `definition` (JSON, below), `system_key`
(empty, or the preset label's key). The spec's two JSON columns (`voorwaarden`,
`acties`) are one versioned document here, so the format can grow.

```json
{
  "version": 1,
  "match": "all",
  "conditions": [{"field": "text", "op": "contains_word", "value": "APK"}],
  "actions": [{"type": "add_label", "label_id": "…"}]
}
```

Migration order: `rules/0001` creates `Rule` only; the `documents` migration of slice 5
adds the `rule` foreign keys of `DocumentEvent` and `DocumentSuggestion`; `rules/0002`
then creates `RuleRun`. In general a foreign key is added in the slice that introduces
its target (`DocumentEvent.label` in H3, `.rule` in H5).

`RuleRun(WorkspaceModel)`: `rule` (CASCADE), `document` (CASCADE), `trigger`
(`processed`, `confirmed`, `manual`), `outcome` (`applied`, `proposed`), time is
`created_at`. One row per rule that applied or proposed at least one change to a document in one
run; a rule that did not match, or whose changes were all there already or dismissed,
leaves nothing. It feeds the counters on the rule ("12 keer toegepast").

### 9.2 Conditions and actions in 1c

`match` is `all` (AND) or `any` (OR) over one flat list of 1–20 conditions. Nested
groups are a later version of the format.

| Field | Operators | Value |
|---|---|---|
| `text` (the letter) | `contains_word`, `contains`, `not_contains_word` | 2–100 characters |
| `sender` | `contains_word`, `contains`, `equals` | 2–200 characters |
| `document_type` | `is`, `is_not` | a document type code |
| `label` | `has`, `has_not` | a label id |
| `addressee` | `is`, `is_empty` | a person id / nothing |
| `source` | `is` | `scan`, `upload`, `mail`, `watch_folder` |

`contains_word` matches a whole word or phrase on word boundaries after normalising
(case, accents, whitespace); `contains` is a plain substring after the same
normalising. In the builder: "bevat het woord" and "bevat de tekst".

Actions (1–10 per rule): `add_label` (a label id) and `add_addressee` (a person id). At most 200 rules per workspace.

### 9.3 The engine is a pure function (`apps/rules/engine.py`)

No Django import, no database.

```python
@dataclass(frozen=True)
class DocumentFacts:
    text: str
    sender: str
    document_type: str
    source: str
    label_ids: frozenset[UUID]
    addressee_ids: frozenset[UUID]

    # Open suggestions on the document: not there yet, but hoped for.
    hoped_label_ids: frozenset[UUID] = frozenset()
    hoped_addressee_ids: frozenset[UUID] = frozenset()

    # Changes the user dismissed on the document: never planned, so never built on.
    dismissed_label_ids: frozenset[UUID] = frozenset()
    dismissed_addressee_ids: frozenset[UUID] = frozenset()

def evaluate(facts: DocumentFacts, rules: Sequence[RuleSpec]) -> Evaluation: ...
```

`RuleSpec` is the parsed, validated rule. `Evaluation` holds `matched_rule_ids` (rules
whose conditions held, whether or not anything would change) and `planned`, a list of
`PlannedAction(rule_id, rule_name, action, target_id, mode, quote)`.

- Rules run in `position` order; inactive rules are skipped. A matching rule with
  `stop` ends the run after its own actions.
- An action that changes nothing (the label is there already, or this run already
  planned it) is not planned. A target that is only an open suggestion from name
  matching or the AI is still planned, so the suggestion gets the rule as its origin.
- Later rules see what earlier rules did. The engine keeps two states: **settled**
  (the facts plus every planned `auto` action) and **hoped** (settled plus the
  document's open suggestions plus every planned `propose` action). So the order in
  which name matching, AI picks and rules are stored does not matter. A rule matches when it matches the hoped state.
- A planned action is `auto` only when its rule is in mode `auto` **and** the rule also
  matches the settled state. A rule that matches only thanks to a proposal nobody
  accepted yet is itself a proposal. So "label Zorg → op naam van mama" (auto) never
  fires on a Zorg label that is still a suggestion.
- A change the user dismissed on the document is never planned and never enters the
  settled or hoped state, so no later rule builds on it: when the user removed Zorg,
  "label Zorg → op naam van mama" does not fire. Actions whose label no longer exists
  or whose person no longer exists or is archived are dropped from the rules before
  `evaluate`, for the same reason.
- `quote`: for a `text` or `sender` condition that matched, the matched words with up
  to 40 characters before them; the first one when several matched; empty otherwise.

"Test on existing documents" calls `evaluate` with the one rule being edited and stops
there. The real run hands the result to the executor. Same code, minus the last step.

### 9.4 Executing (`apps/rules/executor.py`) and when rules run

`facts_for(document)` builds the facts from the database: the text is the masked OCR
text; `sender` and `document_type` are the confirmed values, or before confirmation the
AI's proposals (a sender only when its confidence is above 0); the dismissed
suggestions become `dismissed_label_ids` and `dismissed_addressee_ids`.

`execute(document, planned, trigger)` runs inside the workspace context and a
transaction, with the document row locked:

- a planned change with a `dismissed` suggestion on this document is skipped;
- a target that no longer exists or is archived is skipped;
- `auto`: the change is made, a history event is written (origin `rule`, rule id and
  name), a `RuleRun` with `applied`;
- `propose`: an `open` suggestion is created or strengthened, a `RuleRun` with
  `proposed`.

Rules run at three moments, never on a hand edit of labels or addressees (no loops, no
surprises):

1. **Processed**: at the end of the pipeline, in the same short transaction that stores
   the proposals, together with name matching and the AI's picks (section 10).
2. **Confirmed**: at the end of `confirm_document`, after the user's own choices were
   stored, now with the confirmed sender and type. This is where sender rules do their
   work when AI is `off`.
3. **Manual**: "Toepassen op bestaande post" on a rule. `runRule` queues the job
   `apply_rule_to_existing(workspace_id, rule_id)`, which walks the workspace's
   documents that are not `processing` in batches of 50, each batch in its own short
   transaction, evaluating only that rule.

### 9.5 API (rules: read `member`, change `admin`)

| Operation | Method and path | Notes |
|---|---|---|
| `listRules` | `GET /rules` | `RuleOut {id, position, name, active, mode, stop, match, conditions: [{field, op, value}], actions: [{type, label_id (uuid or null), person_id (uuid or null)}], system_key, applied_count, proposed_count}` in order |
| `createRule` | `POST /rules` | `RuleIn` (the same fields without id, position, counters); appended at the end; 422 with a Dutch sentence that names what is wrong |
| `updateRule` | `PUT /rules/{rule_id}` | |
| `deleteRule` | `DELETE /rules/{rule_id}` | 204; history events keep the rule's name |
| `reorderRules` | `PUT /rules/order` | `{rule_ids: [uuid]}`: every rule exactly once, else 422 |
| `testRule` | `POST /rules/test` | `RuleIn` (not stored) → `RuleTestOut {total_count, checked_count, matched_count, documents: [DocumentSummaryOut]}`; checks the newest 500 documents that are not `processing` (`total_count` is how many there are), counts those whose conditions hold, returns the first 50 |
| `runRule` | `POST /rules/{rule_id}/run` | 202 `{queued: true}`; 409 `rule_inactive` |

Conditions and actions are flat objects (`value` is a string; ids travel as strings in
`value` for conditions and as `label_id` / `person_id` for actions), validated per field
and operator in the service. A discriminated union would put `oneOf`/`anyOf` in the
contract.

### 9.6 Preset rules

From slice 5 on, `apply_preset` also creates one rule per preset label that has none
(`system_key` equal to the label's key): name "Label <name>", mode `propose`, match
`any`, the conditions of section 7.3, one `add_label` action. They appear on the
Automatisering tab like any rule and can be edited, switched to automatic, or deleted;
a deleted preset rule comes back only when the user presses "Startset toevoegen".

### 9.7 The tab "Automatisering" (web)

- The list: rules in order with move up / move down buttons (keyboard-friendly, no drag
  library), per rule its name, one sentence that reads the rule back ("Als de tekst het
  woord APK bevat → label Auto"), a switch Voorstellen / Automatisch, a switch Actief,
  and the counters.
- The builder (new and edit, one page): name; "Als [alle | een van] deze voorwaarden
  kloppen" with condition rows (field, operator, value: text field, or a picker for
  type, label, person, source); "Dan" with action rows; "Voorstellen of automatisch
  toepassen"; "Stop daarna met de volgende regels".
- "Test op bestaande post" works on the unsaved form: "12 van 143 documenten passen",
  with links to the first 50.
- After saving: "Toepassen op bestaande post". It runs over all post, not only the 500
  the test looked at, and says so before it starts ("12 van de nieuwste 500 passen; de
  regel wordt op al je post toegepast").
- Target: a new rule in under a minute (spec 13).

## 10. Automatic labelling and the AI-proposed addressee

Two layers, in this order, at the end of processing:

1. **Deterministic**: name matching (6.3) and the rules engine (9). Works with AI `off`.
2. **AI from a closed list**: the same extraction call that reads the letter also picks
   labels and addressees, only from what the workspace has.

### 10.1 What the AI is asked

The provider call changes from `extract(text)` to `extract(request)` with
`ExtractionRequest(text, persons, labels)`. `persons` and `labels` are tuples of
`Choice(alias, name, also)`: per request the non-archived persons get the aliases
`P1…Pn` and the labels `L1…Ln` (at most 50 persons and 100 labels, by name). The real
ids never leave the server; the model copies a short alias more reliably than a UUID.

The user message gets a block after the letter:

```
Personen (op naam van): P1 = Jan de Vries (ook: J. de Vries); P2 = Emma de Vries
Labels: L1 = Auto; L2 = Belastingdienst; L3 = Zorg
```

Two schema fields are added (Dutch names, like the rest):

```json
"op_naam_van": [{"id": "P1", "bron": "T.a.v. de heer J. de Vries", "zekerheid": 0.9}],
"labels": [{"id": "L2", "zekerheid": 0.8}]
```

In the JSON schema sent to the provider, `id` is an `enum` of exactly the aliases of
this request, so strict structured output already refuses anything else. When a list is
empty its property is left out of the schema and of the prompt block. The prompt says:
choose only from the list, an empty list when nothing fits, never a new name, at most
three labels.

### 10.2 Validation (deterministic, `apps/pipeline/validation.py`)

`validate_choices(payload, text, request) -> list[ValidatedChoice(kind, target_id,
quote, confidence)]`:

- an `id` that is not an alias of this request is **dropped**: not stored, not shown,
  never turned into a new label or person. The log gets a count and a code, no text;
- a duplicate id counts once;
- addressee: `bron` must be literally in the letter (the 1b quote rule); when it is
  not, the pick is dropped. The quote need not contain the person's name ("aan de
  ouders van Emma"), so the model's confidence is clamped to 0–1 and capped at 0.80,
  as for labels;
- label: no quote (a classification, like the document type); the model's confidence
  is clamped to 0–1 and capped at 0.80, because nothing in the letter proves it;
- at most three labels, highest confidence first.

Every valid pick becomes a suggestion with origin `ai` unless it is dismissed on this
document or the change is already there. It is never preselected and never applied
(decision 3). The alias-to-id mapping is the one built for the request; before storing,
the ids are checked again in the third short transaction (a label deleted while the AI
was reading is skipped).

### 10.3 What leaves the network

With AI `external`, in addition to the masked letter text (1b): **the names of the
workspace's labels and the display names and name variants of its persons**. Never ids,
never rules, never page images. With `local` the same goes to the address inside the
network only; with `off` nothing. `docs/PRIVACY.md` gets this row, and the Labels and
Personen pages say it in one sentence when AI is `external`. For that sentence: `getAiStatus`
(`GET /workspaces/{workspace_id}/ai`, viewer) returns `AiStatusOut {mode}` and nothing
else (no address, no model, no key).

Label names are sent because the model cannot choose from a list it does not see.
Person names are in the letters already, so they add little to what the service
receives; a workspace switch "geen namen naar de AI" was considered and left out until
somebody asks for it.

## 11. The inbox

Three states, derived, not stored (`inbox_state` on a document):

| State | Meaning | Rule |
|---|---|---|
| `new` | just arrived, being read | status `processing` |
| `review` | waits for you | status `needs_review` or `failed` |
| `unassigned` | confirmed, on nobody's name | status `done` and no addressee |
| (empty) | sorted | status `done` with an addressee |

`listDocuments` gains optional query parameters `inbox_state`, `person_id` and
`label_id` (still the newest 200). `getInboxCounts` (`GET /inbox/counts`) returns
`{new, review, unassigned}` for the badges. A document leaves the inbox when it is
confirmed and has an addressee; it is then found under "Post" and under the person. A
suggestion that a rule makes on such a document does not bring it back: the row shows a
"voorstel" mark (`open_suggestion_count`) in every list, and that is all.

In the web the Inbox page has the three states as tabs. `review` rows say "Controleer
deze brief in de app" (review stays in the app); `unassigned` rows have a person picker
right in the row, so sorting a backlog is one choice per letter.

## 12. Post in the web interface

New routes under a workspace (Dutch paths, like `/koppel-app`):

| Route | Screen |
|---|---|
| `/w/$workspaceId/inbox` | Inbox with the three tabs |
| `/w/$workspaceId/post` | all documents, filters person and label (in the URL's search part) |
| `/w/$workspaceId/post/$documentId` | one document |
| `/w/$workspaceId/personen` | persons |
| `/w/$workspaceId/labels` | labels |
| `/w/$workspaceId/automatisering` (+ `/nieuw`, `/$ruleId`) | rules |

The sidebar shows the workspace's name and these entries (Inbox with its count) above
"Werkruimtes" and "Apparaten". With one workspace, `/` goes straight to its inbox; with
several, the Werkruimtes page links each one.

**The document page**, read-only for the content:

- Left the pages: `<img src="/api/v1/workspaces/{id}/documents/{id}/pages/{n}">`, one
  under the other, `loading="lazy"`, with alt text "Pagina 2 van 3". The endpoint
  exists since 1b: it checks the session, decrypts the sealed file in memory and
  answers `Cache-Control: private, no-store`. Same origin, so `img-src 'self'` allows it
  and the session cookie goes along; nothing is decrypted to disk and nothing changes in
  the CSP. A viewer may see pages (as in 1b).
- Right: sender, type, letter date, status; the summary with the AI mark when
  `summary_by_ai`; "Op naam van" and "Labels" as editable chip lists with a picker;
  open suggestions with "Toevoegen" / "Negeren" and their quote; the tasks with a
  checkbox; "Tekst van de brief" (the masked OCR text) in a collapsed section;
  "Geschiedenis".
- A document in `needs_review` shows the AI's proposals read-only with the notice that
  reviewing happens in the app.

## 13. History and undo

`DocumentEvent(WorkspaceModel)` in `apps.documents`:

| Field | Notes |
|---|---|
| `document` | CASCADE |
| `kind` | `label_added`, `label_removed`, `addressee_added`, `addressee_removed`, `confirmed`, `task_completed`, `task_reopened` |
| `origin` | `user`, `rule`, `suggestion` (the user accepted a proposal), `undo` |
| `actor` | FK user, nullable (null for a rule in a job) |
| `rule`, `label`, `person` | nullable FKs with `SET_NULL`, each with a `_name` text copy so the history stays readable after a delete |
| `undoes` | nullable FK to the event this one reverses |

Every change of labels and addressees on a document writes an event, whoever made it:
one service (`documents.organise`) is the only code that adds or removes them. The one
exception is deleting a whole label or person, which cascades without events. This is the
per-document history of spec 9; the server-wide `audit_log` of spec 12 stays for later.

`listDocumentEvents` (`GET /documents/{id}/events`) returns `DocumentEventOut {id,
created_at, kind, origin, actor_name, rule_name, label_name, person_name, can_undo,
undone}` newest first. Names are plain strings (empty when not applicable).

`undoDocumentEvent` (`POST /documents/{id}/events/{event_id}/undo`, member): for an
event with origin `rule` that was not undone yet. It reverses the change, writes an
event with origin `undo` that points at it, and sets the matching suggestion to
`dismissed`, so the rule will not do it again on this document. When the state already
differs (the label is gone anyway) it answers 409 `event_not_undoable`. Hand changes
have no undo button: you reverse them by hand, which is the same click.

A manual run over many documents is undone per document. The builder shows the match
count before the run starts; an "undo this whole run" is left for when it is needed.

## 14. Tasks

Small, because the calendar needs it: `listTasks` (`GET /tasks?status=open|done|all`,
default `open`) returns `TaskItemOut {id, type, description, deadline, deadline_source,
status, completed_at (datetime or null), document_id (uuid or null), sender,
updated_at}`; `completeTask` and `reopenTask` (`POST /tasks/{task_id}/complete`,
`…/reopen`, member, idempotent) return it too and write a document event. `TaskOut`
inside `DocumentOut` is unchanged.

## 15. The app (iOS)

- **Lists.** The post screen gets a filter bar: Inbox (with the three states as
  sections), Alle post, and one entry per person. Rows show the addressee names and
  label chips.
- **Review.** Two new sections, "Op naam van" and "Labels": the current values as
  chips, suggestions as chips to tap (preselected ones already on, each with its
  origin: "Herkend in de brief", "Voorstel van AI", "Regel: <naam>" and "Waar staat
  dit?" when there is a quote), and a picker for the rest. "Nieuwe persoon" is possible
  from the picker; labels are created in the web. Confirm sends
  `sets_organisation: true` with the chosen lists.
- **A confirmed document** shows the same two sections, editable
  (`setDocumentAddressees`, `setDocumentLabels`, accept/dismiss), and its tasks with a
  tick box.
- **Calendar** (section 16).
- No history, no undo, no rules in the app in 1c.

## 16. Calendar

In the app, per paired workspace, a switch "Zet deadlines in mijn agenda".

- The app asks for full calendar access (`NSCalendarsFullAccessUsageDescription`): it
  must find and change the events it made, which write-only access cannot.
- It creates one calendar per workspace, named "Brievie – <werkruimte>", in the phone's
  default calendar source and only ever reads and writes there (the calendar's
  identifier is kept per workspace; when the user deleted it, the next sync makes it
  again). Switching the option off offers to remove that calendar; nothing else in the
  user's calendars is touched.
- One all-day event per **open task with a deadline**, on the deadline day: title
  "<taaksoort>: <omschrijving>", notes with the sender and "Uit Brievie", URL
  `brievie://task/<task>`. The app registers the `brievie` URL scheme; opening such a
  link opens the app (after the lock) on that workspace's post. The URL's main job is to
  tell which event belongs to which task. Alarms 7 days before, 2 days before and on the
  day at 09:00 (spec 8.7), which covers reminders until notifications are built.
- **The calendar is the state.** There is no local table of task ↔ event. A sync reads
  the events of the workspace's calendar from one year back to three years ahead (EventKit
  searches at most four years at a time; a deadline further away gets its event once it
  comes inside that window), reads
  `listTasks` (open), and a pure planner returns what to create, update and delete,
  matched on the URL. A task that was ticked off or lost its deadline loses its event.
- A sync runs when the app becomes active, after a confirm and after ticking a task.
  There is no background sync in 1c: a deadline confirmed on another device appears the
  next time this app opens.
- All tasks of the workspace are synced (there is one account). When members exist,
  this becomes "tasks of post on my name".
- The setting's text says that the events contain the task's text and the sender, and
  go wherever that calendar syncs (usually iCloud). That is the user's own choice per
  phone; the server sends nothing to a calendar service.

## 17. Data flow at the end of processing

```
load text ──► AI call (no transaction; request carries the closed lists)
          └─► validate values (1b) + validate choices (10.2)
third short transaction, document locked and still `processing`:
  store proposals (1b)
  name matching            → suggestions (origin name_match)
  AI picks                 → suggestions (origin ai)
  rules: facts → evaluate → execute (trigger processed)
                           → auto: labels/addressees + events
                           → propose: suggestions (origin rule)
  status needs_review
```

A document whose processing fails for good (`mark_failed`) still gets the deterministic
layer in that same transaction: name matching and the rules, without AI picks. The
user reviews it by hand and finds the proposals there.

Proposing (name matching, AI picks, rules) runs in a savepoint inside that
transaction. When it fails, only its own writes are rolled back: the AI's proposals
are still stored and the status still becomes `needs_review` (or `failed` with the
original code); the log names the exception type and the document id, nothing more.

Persons, labels and rules are read into plain dataclasses in the first short
transaction; ids are checked again when storing. A retry of the job replaces the `open`
suggestions of this run and never touches `accepted` or `dismissed` ones.

## 18. Errors and edge cases the plans must test

- The AI returns an id outside the list, an id of another workspace's label, or the
  same id twice.
- A label or person is deleted or archived between the AI call and storing.
- The user removes an auto-applied label in the review: confirm's rule run must not
  put it back.
- A confirm is repeated after its answer was lost, and an `auto` rule added a label in
  between: the repeat answers 200, not 409.
- Processing fails for good: the name proposal is still there.
- An `auto` rule that depends on a label that is only proposed stays a proposal.
- Two rules propose the same label: one suggestion, one event when accepted.
- Undo twice; undo after the label was already removed by hand.
- The 1b app confirms without the new fields: auto-applied labels stay.
- A rule with a deleted label id in its definition cannot exist (delete is refused);
  one with an archived person is skipped at execution.
- A person named "Jan" and a letter that mentions "januari"; a variant of two letters.
- Every new endpoint from another workspace's member: 404 `workspace_not_found`; every
  new job with another workspace's id: nothing changes.
- Calendar: access refused; the workspace's calendar deleted by the user; the same task
  changed on the server; a deadline more than three years away; two workspaces.

## 19. Slices (build order)

Each ends in something the owner can see. Host slices are tasks groups of the host
plan; the iOS plan follows the host slices it needs.

| # | Slice | Shows |
|---|---|---|
| H1 | Post in the web | the post list and a document with its pages in the browser |
| H2 | Persons, "op naam van", inbox, history | Personen page; put a letter on papa's name; the three inbox tabs |
| H3 | Labels and presets | Labels page with the startset; labels on a letter; filter on label |
| H4 | Suggestions: names and AI | a letter with papa's name arrives with the proposal "Papa"; AI proposes a label |
| H5 | Rules engine, preset rules, undo | a preset rule proposes "Belastingdienst"; an automatic rule applies and can be undone from the history |
| H6 | Tab Automatisering | build a rule, test it on existing post, run it |
| H7 | Tasks: list and tick off | tick a task in the web |
| I1 | App: persons and labels in the list, inbox | (needs H2, H3) |
| I2 | App: review with addressee, labels, suggestions | (needs H4) |
| I3 | App: edit after confirming, tick off tasks | (needs H7) |
| I4 | App: calendar | deadlines in the phone's calendar |

## 20. Deviations from `docs/SPEC.md`

- Section 16 puts folders, NL rules, search (host) and the Today screen, local
  notifications (app) in 1c; they move to a follow-up step (decision 6). Persons, the
  inbox and post in the web are pulled into 1c.
- Spec 7: persons are Thuis only → both kinds (6.1).
- Spec 12: `document_person` is `DocumentAddressee`; `document_acl` and
  `document.zichtbaarheid` are not built (decision 2); `rule` has one JSON `definition`
  and a `mode`; proposals for labels and addressees live in `DocumentSuggestion`, not in
  `extraction`; history is `DocumentEvent`, `audit_log` stays for later.
- Spec 10 schema: `op_naam_van` carries an id from a closed list instead of a free
  name; `labels_voorstel` (free strings) is `labels` with ids from a closed list.
- Spec 9: conditions and actions are a subset (9.2; no "remove label"); "Leren" is not built; rules do
  not run on every change, only at the three moments of 9.4.
- Spec 8.7 and decision 4: EventKit in the app instead of an ICS feed per member.
- Spec 14: "Zorg private by default" waits for members (decision 2). Until then
  `docs/PRIVACY.md` says that every member of a workspace sees all its post.

## 21. Left to the builder

Names of private helpers, the exact Dutch sentences (B1, je-vorm), the page layout
within the existing design (sidebar, tables, tags), test file names, the order of
commits inside a task, and how the web splits its components. Not left to the builder:
anything in sections 6–14 that names a field, an operation, a status code or a rule of
behaviour.
