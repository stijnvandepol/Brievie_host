# Mijn taken: a personal to-do list made from your post

Date: 2026-11-21. Covers `Brievie_host` (backend, `web/`) and, as a short list of fields
and screens, `Brievie_ios`. Design only; nothing here is built. Plan:
`docs/superpowers/plans/2026-11-21-host-mijn-taken.md`.

**Neighbours.** The rights design `2026-11-19-zichtbaarheid-postkamer-inrichting-design.md`
(cited "S 4.1") and its plan `2026-11-20-host-wie-ziet-wat.md` (cited "WZW T15"): its
rule of sight, the task policy (S 4.9.3, R39), `viewers()` and `visible_documents` are on
`main` up to merge point M1; the leak suite (WZW T12–23) and private post, departments
and switching (T24–47) are not built yet. The trash (`feat/trash`, DECISIONS "Trash for
letters") hides a trashed letter's tasks and deletes them on purge.

## 1. Intent

Stijn: users always sign in as themselves, and each gets a to-do list made from their own
post, updated by itself as post comes in, "so they know what to do even without looking at
the post". One page answers: *what do I have to do, by when, for whom, and why*, without
opening a letter.

## 2. What exists (verified on `main`, efdd05e)

- `Task` (`apps/tasks/models.py`): letter (nullable), `type`, `description`, `deadline`
  with `deadline_source` and `deadline_quote`, `status` open/done, `completed_at`,
  `assigned_to` (never written by any code path), `created_by`, `created_by_ai`.
- API: `listTasks` (`status` open/done/all, at most 500, every visible task of the
  workspace), `completeTask`, `reopenTask` (each writes a `task_completed` /
  `task_reopened` event on the letter). No create, assign or edit endpoint (WZW P11).
- Tasks are made only when a letter is confirmed: by a person (`review.confirm_document`,
  `created_by` set) or by the autopilot (`created_by_ai`, only when every action has a
  deadline and every payment a deadline, `autopilot.decide_confirmation`).
- The task policy follows the letter (`EXISTS` on the document under its own policy); a
  letterless task is seen by those who see all post, its maker and its assignee (R39).
- The amount of an invoice exists only as a proposal (`Extraction.invoice_amount_incl`);
  nothing confirmed holds an amount.
- Web: no tasks page; a letter shows its tasks (`components/task-list.tsx`). "Te doen"
  (`routes/inbox.tsx`, nav count = review + unassigned) lists *letters* that wait.
- iOS: one post screen with scopes "Te doen", "Alle post" and one per person
  (`DocumentsModel+Scope.swift`); a confirmed letter shows its tasks with tick boxes;
  local deadline reminders (`ReminderPlanner`, 09:00, lead 0/1/2/3/7 days, body without
  letter text unless "toon afzender") and the phone calendar (`CalendarSync`) both read
  **every** open task of the workspace through `listTasks`.

## 3. Goals, non-goals, anti-goals

- Goal: a per-member list "Mijn taken", derived live from confirmed letters, in web and
  app, with the action, the date and days left, the amount, the sender, for whom and why.
- Goal: the phone's reminders and calendar follow that list, not the whole workspace.
- Goal: in an organisation, department and unnamed post become tasks someone picks up.
- Non-goal (v1): tasks made by hand, recurring tasks, a stand-in during absence, a team
  overview, e-mail or push digests, an ICS feed.
- Anti-goal: a task's words reaching anyone who may not read its letter, through a row,
  a count, a reminder or a name list; a copied "my list" that can drift from the post.

## 4. Naming and the screen question

**Name: "Mijn taken"** (task: *taak*, already the word on every letter and in the app).
Rejected: "Te doen" (taken: the review list, shipped in web and app), "Actielijst"
(office word), "Wat moet ik doen?" (a question is a poor menu label), "Taken" (in a
household "Taken" reads as chores of everyone; "Mijn" says it is yours).

**Two screens, not one.** "Te doen" and "Mijn taken" stay separate, linked to each other:

- They hold different things for different people: "Te doen" is *letters* to check or
  name, worked by whoever sorts post (in an organisation one receptionist, hundreds of
  rows, with a person picker per row); "Mijn taken" is *actions* for the person the post
  is for, with a tick box. One screen would bury the one under the other for exactly the
  people the feature is for.
- Merging would rename a shipped screen in web, app, glossary and the WZW plan (T44).
- They meet where it helps: "Te doen"'s description becomes "Brieven die je nog moet
  nakijken of een naam moet geven. Wat je daarna moet doen, staat bij Mijn taken."; and
  "Mijn taken" shows, when letters wait for this member, one line on top: "Er wachten
  nog 3 brieven op nakijken. Daar kunnen nieuwe taken uit komen. Naar Te doen".
- The navigation order becomes: **Mijn taken**, Te doen, Alle post, …. The landing page
  after sign-in stays "Te doen" in v1 (question Q1).

## 5. Whose tasks

### 5.1 One rule, per member

For a signed-in member *u* and an open task *t* that *u* can see (the task policy decides,
unchanged) on a letter that is not in the trash, the server gives *t* one **reason**:

| Reason | When | Shown as |
|---|---|---|
| `assigned` | `t.assigned_to = u` | "Aan jou gegeven door Anne" |
| `addressee` | nobody assigned, and the letter is on a person linked to *u* | "Voor jou" (or "Voor jou en Piet") |
| `department` | nobody assigned, departments on, the letter is on a department *u* is in | "Voor Finance" |
| `unassigned` | nobody assigned, *u* sorts post (flagged, or an admin while nobody is flagged), and no addressee is covered (S 4.1 rule 4, the same SQL function `brievie_covered_post_ids`) | "Zonder naam" |
| `other` | anything else *u* can see (assigned to someone else, someone else's post *u* sees because everyone sees all) | "Voor Piet" / "Bij Piet" |

A letterless task (only old ones exist; v1 makes none) is `assigned` to its assignee, else
`addressee` for its maker, else `other`. The rule reuses the four functions of S 4.9.1;
there is no second definition of "my post".

A task on a letter for two linked persons is one task: both see it, either ticks it off,
both see "Afgevinkt door Anne".

### 5.2 Per workspace kind (simple default, optional layer)

| | Thuis (`kind = home`) | Organisatie (`business`) |
|---|---|---|
| "Mijn taken" (the list and the nav count) | `assigned`, `addressee`, `department`, `unassigned` | `assigned`, `addressee` |
| "Op te pakken" (a second part on the same page) | not shown | `department`, `unassigned`, each with "Ik doe dit" |
| "Alle taken" (a filter) | every task the member can see (`other` included) | same, under the policy (so with "Iedereen" off: only post the member may read) |

At home the default sorters are the admins (S 4.1), so tasks of post without a name, or
for a child without an account, land with the parents, not with the children: the same
answer as "who sorts that post". An organisation keeps the personal list short and makes
shared work visible as work to pick up. The split follows `Workspace.kind`, not the
switches: a household that turns "Iedereen" off still wants one list.

### 5.3 Assignment and hand-over (v1) and what comes later

- **v1:** "Ik doe dit" (assign to yourself), "Geef aan…" (to another member), "Niet meer
  aan mij" (release). One rule: a task is assigned only to a member who can read its
  letter, never to a viewer (422 `assignee_cannot_read`, S 4.10, WZW P11), checked with
  `visibility.viewers()`. Whoever can edit the letter may assign or release. Each change
  is a history event on the letter (`task_assigned`, `task_unassigned`) with the actor and
  the assignee's name. No notice: the task appears in the assignee's list and count.
- **When the assignee loses sight** (the letter is handed to someone else, made private,
  the member removed or made a viewer, a switch narrows sight): the assignment is released
  as the system (event `task_unassigned`, origin `system`) and the task falls back to its
  reason by addressee. The former assignee sees nothing of it.
- **Later:** a stand-in during absence (part of delegation, R43); a notice or push on
  hand-over (B2); assigning letterless tasks; tasks made by hand.

## 6. A row: knowing what to do without the letter

Each row, in this order (web and app):

1. **Tick box** labelled with the action: "Klaar: Betaal de aanslag" (screen readers read
   the action, not "checkbox").
2. **The action** in plain words: the task's `description` (B1 by the review or the AI),
   with the type as a small word when the description does not start with it.
3. **When:** "Vandaag", "Morgen", "Nog 5 dagen (1 dec)", "3 dagen te laat (18 nov)";
   overdue in the warning colour *and* in words (never colour alone).
4. **Amount** for a payment when known: "€ 1.234,56".
5. **From and for:** "Van Belastingdienst · Voor Piet" (sender of the letter; for whom by
   the reason, 5.1).
6. **Why:** "Uit de brief: 'te betalen vóór 1 december 2026'" (`deadline_quote`), only
   for a deadline read from the letter; "Datum zelf ingevuld" for a manual one.
7. **Needs a date:** a task without a date whose letter names a term gets the badge
   "Datum nodig" and "De brief noemt een termijn: 'binnen zes weken na dagtekening'.
   Vul de datum in." with a button "Datum invullen".
8. A link "Brief bekijken" (secondary: the row must be enough).

**Groups** (headings, in this order, each with its count): "Te laat", "Binnen 7 dagen"
(today up to and including today + 6), "Later", "Zonder datum" (with "Datum nodig" rows
first). Within a group by date, then by creation. "Binnen 7 dagen" instead of "Deze week":
on a Saturday "this week" is two days, which hides Monday's deadline.

**Done:** ticking keeps the row in place, struck through, with "Ongedaan maken" until the
page is left or refreshed; then it moves to **"Afgerond"** (a tab), which lists tasks
completed in the last **30 days** with "Afgevinkt door Anne op 20 nov" and "Toch niet
klaar". Older done tasks stay on their letter (and in its history); nothing is deleted.

## 7. Derived, never copied: how the list updates itself

The list is a query over `Task` joined to its letter, computed per request. No table holds
"my list", so every change of the post is in it at the next read:

| Event | Effect |
|---|---|
| A letter is confirmed (by a person or the autopilot) | its tasks appear for whoever the reason gives |
| A task is ticked off / reopened | it leaves / returns; "Afgerond" shows who did it |
| The letter's addressees change (hand-over, a name picked in Te doen, a department) | reasons change on the next read; an assignee who lost sight is released (5.3) |
| A person is linked to or unlinked from a member; departments or switches change | same: the reason is computed from live links |
| The letter goes to the trash | its tasks leave every list and count (`feat/trash` filters them); a restore brings them back as they were |
| The letter is purged | its tasks are deleted with it (trash ruling 13) |
| The letter is reopened (`reopenDocument`) | the AI's open tasks are removed (existing), the letter is back in Te doen |
| A date is filled in ("Datum invullen") | the task moves from "Zonder datum" to its group |
| A deadline is corrected | only by reopening and confirming the letter again, or "Datum invullen" (a manual date) |

The phone sees changes at its next refresh (app start, pull to refresh, background
refresh); the server pushes nothing yet.

## 8. Visibility and privacy

- **The task policy stays the only gate.** A task is listed, counted, offered as a
  reminder or named in a hand-over list only through a query that runs as the member,
  under the task policy (S 4.9.3). The new filters narrow; they never widen.
- **An assignee who may not read the letter sees nothing**, not the title, not the date,
  not that a task exists. A title can be the content ("Betaal deurwaarder", "Reageer op
  ontslagbrief"). Assignment is refused up front (422) and released when sight goes (5.3),
  so this state is short-lived and, by the policy, invisible.
- **Counts** (nav, group headings, "Op te pakken", "Afgerond") are SQL counts of the same
  query under the policy; there is no count of hidden tasks anywhere, also not for admins.
- **"Geef aan…"** lists only members who can read the letter (`viewers()`, minus viewers),
  names only (a member list is a directory, S 4.10).
- **Reminders and calendar** keep their rule: no letter text on the lock screen unless the
  user turned on "toon afzender"; the calendar event title is the task type
  ("Brievie: Betalen"), as now.
- **No new log line** holds a description, sender, amount or quote.
- `term_quote` and `amount` are stored BSN-masked / as `Decimal`, like every other field.

## 9. Everyone signs in as themselves

Concretely:

- **One account per person.** A shared account ("ons gezin") would merge everyone's list
  and hide who ticked what off. Brievie does not detect sharing; it explains it.
- **Each phone is paired to its own member** (a device token acts with that member's
  rights, D 6). A phone shared by two people shows one person's list.
- **A member linked to no person** sees only tasks given to them, and the page says why:
  "Je account hangt nog niet aan een naam. Daarom zie je hier alleen taken die iemand aan
  je geeft. Vraag een beheerder om je te koppelen bij Personen." (The answer carries
  `has_person`.)
- **Texts** (setup, invitation, pairing; the builder adds them where these screens are):
  - setup, under the first account: "Maak straks voor iedereen een eigen account. Dan
    ziet ieder alleen zijn eigen taken en zie je wie wat heeft gedaan.";
  - invitation dialog: "Iedereen logt in met een eigen account. Deel geen wachtwoord.";
    the invitation of a person (`person_id`) adds: "Deze persoon ziet daarna de taken
    van post op zijn of haar naam.";
  - pairing page: "Koppel alleen je eigen telefoon. Wie een andere telefoon gebruikt,
    koppelt die met een eigen account."
- **What an admin sees of others' lists.** Thuis: through "Alle taken", every task (an
  admin sees all post there), each with "Voor Piet". Organisatie: only tasks of letters
  the admin may read (with "Beheerders lezen alle post" off: their own and what they
  sort). A **team overview** per department (open and overdue per department and per
  member, counts under the admin's or lead's own policy, no titles of post they may not
  read) is the organisation's optional layer **after v1**; v1 builds nothing for it except
  that `count_my_tasks` already counts per reason.

## 10. Reminders and digests

- The app's reminders and calendar read `listMyTasks` (`part=mine`) instead of `listTasks`.
  In an organisation, "Op te pakken" gets no reminders until someone picks the task up.
  An older server without `listMyTasks` (404) keeps today's behaviour (all open tasks).
- The calendar setting gains "Welke taken: Mijn taken (standaard) / Alle taken".
- "Datum nodig" tasks get no reminder (no date); the app shows their count on the list.
- **Outlook only:** a daily or weekly digest ("Deze week: 3 taken, 1 te laat") needs the
  server to send mail or push, which it does not (B2, Q3 of the rights design). When it
  comes, it reads `count_my_tasks`, carries counts only, never titles.

## 11. API (additive, under `/api/v1`)

**Decision: a new operation `listMyTasks`, plus new defaulted fields on `TaskItemOut`.**
Extending `listTasks` with `mine=true` would change a list the phone calendar trusts
(it deletes events whose task no longer comes back), and the answer needs a wrapper
(counts, `has_person`, `today`). `listTasks` stays as it is, gaining the new fields.

| Operation | Method, path | Auth | Answer |
|---|---|---|---|
| `listMyTasks` | GET `/workspaces/{id}/my-tasks?part=mine\|to_pick_up\|all&status=open\|done` | session or device | `MyTasksOut` |
| `countMyTasks` | GET `/workspaces/{id}/my-tasks/counts` | session or device | `MyTaskCountsOut` |
| `assignTask` | POST `/workspaces/{id}/tasks/{task_id}/assign` body `{user_id: uuid \| null = null}` (null releases) | member+ | `TaskItemOut`; 422 `assignee_cannot_read`, 422 `task_without_letter`, 404 `task_not_found` |
| `listTaskAssignees` | GET `/workspaces/{id}/tasks/{task_id}/assignees` | member+ | `list[TaskAssigneeOut]` (`user_id`, `name`, `is_me`) |
| `setTaskDeadline` | POST `/workspaces/{id}/tasks/{task_id}/deadline` body `{deadline: date \| null = null}` | member+ | `TaskItemOut`; 404 |

- `MyTasksOut`: `items: list[TaskItemOut]`, `today: date`, `has_person: bool`,
  `truncated: bool`, `waiting_letters: int` (review + unassigned the member sees, for the
  line in 4), `split: bool` (true for Organisatie: show "Op te pakken").
- `MyTaskCountsOut`: `mine`, `overdue`, `within_week`, `needs_date`, `to_pick_up` (ints).
- New on `TaskItemOut`, all with defaults: `deadline_quote: str = ""`, `term_quote: str =
  ""`, `needs_deadline: bool = False`, `amount: str = ""` (a decimal string "1234.56";
  empty when unknown: a nullable `Decimal` would render as `anyOf`), `days_left: int |
  None = None`, `group: str = ""` (`overdue`, `within_week`, `later`, `no_date`, empty for
  done), `reason: str = ""`, `for_names: list[str] = []`, `assigned_to_id: UUID | None =
  None`, `assigned_to_name: str = ""`, `assigned_by_name: str = ""`,
  `completed_by_name: str = ""`, `document_type: str = ""`, `can_edit: bool = False`.
- `TaskIn.amount: str = ""` (the review may send it); `DocumentEventOut` kinds gain
  `task_assigned`, `task_unassigned`, `task_deadline_set` (clients skip unknown kinds).
- Caps: open parts at most 500 rows (`truncated` says so), "Afgerond" at most 200 rows of
  the last 30 days. No pagination in v1: a person with 500 open tasks has another problem.
- `today` and `days_left` are computed in Europe/Amsterdam (as notices, `NOTICE_ZONE`).
- **Performance:** both reads must pass the 250 ms rule (WZW P1) at 100,000 letters and
  20,000 tasks, as a plain member, a department member, a sorter and an admin, E on and
  off; they are added to the `visibility-perf` job. The member's person ids, department
  ids, sorting flag and covered ids are read once per request and passed as arrays
  (`addressee_ids && %s`, GIN index); a partial index `tasks_task_open` on
  (`workspace_id`, `deadline`) `WHERE status = 'open'` serves the list and counts.

## 12. Model changes

- `Task.amount`: `DecimalField(max_digits=12, decimal_places=2, null=True)`; set at
  confirm from `TaskIn.amount`; by the autopilot only for a `pay` task, from
  `invoice_amount_incl` when its quote holds that amount (the validation already gives
  zero confidence otherwise). Old tasks: none (no backfill: a guessed amount is worse
  than none).
- `Task.term_quote`: `CharField(200, blank)`; at confirm, for a task without a date, the
  words of the letter that name a term, from a pure `autopilot.term_phrase(text)` built on
  the same linear-time patterns as `mentions_term`. Old tasks: none.
- `Task.completed_by`, `Task.assigned_by`: FK user, null, `SET_NULL`.
- `DocumentEvent.Kind`: `task_assigned`, `task_unassigned`, `task_deadline_set`.
- Partial index `tasks_task_open`.

## 13. Web

- **Navigation:** "Mijn taken" first under the workspace, icon `checklist`, count = `mine`;
  aria label "Mijn taken, 4 open, waarvan 1 te laat". The count turns to the warning style
  when `overdue > 0` (plus the words in the aria label).
- **Route** `/w/$workspaceId/taken`, page title "Mijn taken", description (Thuis) "Wat je
  met je post moet doen, op datum. Je hoeft de brief er niet voor te openen."
  (Organisatie) "Taken die bij jou horen. Taken van je afdeling en post zonder naam staan
  bij Op te pakken."
- **Tabs** (React Aria `Tabs`): "Open", "Afgerond". A filter `ToggleButtonGroup` "Mijn
  taken" / "Alle taken". Organisatie: under the groups a second section "Op te pakken"
  with its count and "Ik doe dit" per row.
- **Rows** as section 6; group headings are `h2` with counts; the tick box is a real
  checkbox; "Ongedaan maken" is announced in a live region ("Afgevinkt: Betaal de
  aanslag").
- **Dialogs:** "Datum invullen" (a `DatePicker`, "Welke datum noemt de brief?" plus the
  term quote); "Geef aan…" ("Aan wie geef je deze taak?", only members who can read the
  letter: "Alleen wie deze brief mag lezen, staat in de lijst.").
- **Empty states:** open, none: "Je hebt geen open taken. Nieuwe taken komen hier vanzelf
  zodra je post is nagekeken."; no person: the sentence of section 9; Afgerond, none:
  "Je hebt de laatste 30 dagen niets afgevinkt."; Op te pakken, none: "Er ligt niets op
  te pakken."; a viewer (cannot tick): "Je kunt taken bekijken, maar niet afvinken."
- **On a letter** (`task-list.tsx`): each task shows its amount, "Voor …" / "Aan Anne
  gegeven", and the actions "Ik doe dit" / "Geef aan…" / "Datum invullen" where allowed.
- "Te doen": the new description (section 4).

## 14. iOS (planned in `Brievie_ios`)

- Sync the API; add `listMyTasks`, `countMyTasks`, `assignTask`, `listTaskAssignees`,
  `setTaskDeadline` to the generator config; decode the new `TaskItemOut` fields.
- **Screen "Mijn taken"**: first entry of the scope menu, above "Te doen", and the screen
  the app opens on when Q1 says so; the groups, row and texts of sections 6 and 13;
  swipe "Klaar" with undo; "Afgerond"; "Op te pakken" for Organisatie.
- **Reminders and calendar** read `listMyTasks(part: mine)`; fall back to `listTasks` on
  404; the calendar setting of section 10.
- **Review**: an amount field on a payment task, prefilled from the invoice proposal (the
  review is where amounts become facts); `TaskIn.amount`.
- **Letter detail**: the task row gains amount, for whom, "Ik doe dit".
- **Pairing screen**: the sentence of section 9.

## 15. Security and audit

- Every new endpoint: typed schemas, camelCase operation id, `ApiError` bodies; the
  mutating ones in `transaction.atomic`, `min_role=member`, the task loaded `FOR UPDATE`
  as the member (404 like an unknown id when hidden or trashed).
- **History, not the audit log.** Completing, reopening, assigning, releasing and dating a
  task are `DocumentEvent`s on the letter, with the actor (or origin `system` for a
  release), so "who did what" lives with the post, under the letter's policy. The audit
  log stays for admin actions on people and settings (its closed code list is unchanged).
- Assignment writes the new `assigned_to` as the system after the `viewers()` check (S
  4.9.3's note: the update policy would otherwise refuse a row the actor cannot see once
  changed); the check and the write are in one transaction under the task's row lock.
- `system_actor()` callers: `apps/tasks/services.py` is on the list already; the new
  `apps/tasks/assignment.py` joins it.

## 16. Rulings (calls made on the owner's behalf)

| # | Ruling | Why | Cost if wrong |
|---|---|---|---|
| MT1 | Name "Mijn taken"; "Te doen" keeps its meaning | no rename of a shipped screen; "taak" is the word already used | two to-do-sounding entries; mitigated by the descriptions and the cross-link |
| MT2 | Two screens, linked; nav order Mijn taken, Te doen | different people, rows and actions | a home user looks in two places; one line on top bridges it |
| MT3 | One reason per task from live links; no stored list | "updated automatically" without a sync job that can drift | a query per page view (measured, 250 ms) |
| MT4 | Thuis: one list including department and unnamed post (via the sorters, default the admins); Organisatie: "Mijn taken" + "Op te pakken"; split by `Workspace.kind` | simple at home, shared work visible in an office | a household with a non-admin who pays the bills flags them as sorter |
| MT5 | v1 has assign-to-self, hand-over and release; no stand-in, no notice | an office cannot share work without it; delegation is later (R43) | a colleague learns of a hand-over from the count, not a message |
| MT6 | An assignee who may not read the letter sees nothing, and the assignment is released when sight goes | a title is content | a hand-over must be redone after a letter moves |
| MT7 | New `listMyTasks`, `listTasks` unchanged in meaning | the calendar's delete logic trusts `listTasks` | two list operations |
| MT8 | `amount` as a decimal string, empty when unknown | no `anyOf` in the contract | clients parse a string |
| MT9 | Groups by "Binnen 7 dagen", not calendar week | no weekend cliff | none known |
| MT10 | Done tasks shown 30 days under "Afgerond", never deleted | undo and "who did it" without clutter | a task done 31 days ago is found on its letter only |
| MT11 | Task changes are letter history, not audit rows | history follows the letter's sight; audit is for admins | an admin cannot see who ticked a task on a letter they may not read (correct) |
| MT12 | "Datum nodig" from the letter's term phrase, set at confirm; a manual date through `setTaskDeadline` | a term without a date must not disappear under "Zonder datum" | old undated tasks never get the badge |
| MT13 | Reminders follow "mine"; "Op te pakken" gets none | a reminder for someone else's task is noise and a leak risk on a lock screen | an unclaimed department task can pass unnoticed: it is counted in nav and shown on top |
| MT14 | No backfill of `amount` or `term_quote` | a guess is worse than nothing | older tasks look plainer |

## 17. Tests (the builder's list)

- Pure: `reason_for` over every row of 5.1 and both kinds; `group_for`/`days_left` around
  midnight Amsterdam, today, today + 6, today + 7, overdue; `term_phrase` on the 20 trial
  letters, linear time on a 1 MB adversarial text, masked BSNs.
- Service, in the `office()` world (WZW `visibility_support`) in states E on/off, D on/off,
  sorter flagged or not: each member's `mine`, `to_pick_up`, `all` and counts; trashed
  letters gone and back after restore; hidden letters absent from items *and* counts; a
  letter for two persons appears for both and once ticked for both.
- Assignment: to a reader ok; to a non-reader, a viewer, a non-member 422; by a viewer 403
  (existing role error); hidden task 404; release on hand-over, on private, on member
  removal, on demotion to viewer; history events with actor and origin.
- API: contract against `openapi_1c.json` (no field removed, all new ones defaulted, no
  `anyOf`); device token can list, count, tick and assign.
- Perf: `list_my_tasks` and `count_my_tasks` in the `visibility-perf` job.
- Web: by role, the groups, the tick and undo, the empty states, the dialogs, the nav
  count and its aria label; Lingui catalogue updated.

## 18. Questions only the owner can answer

- **Q1. Should Brievie open on "Mijn taken" instead of "Te doen"** (web after sign-in,
  and the app)? It is the screen that matches "know what to do without looking at the
  post", but it moves the start of everyone who sorts post. *Meanwhile:* the start stays
  "Te doen"; "Mijn taken" is first in the navigation.

Decided without asking, but worth a glance: MT4 (at home, tasks of post without a name go
to the admins by default, not to every member).
