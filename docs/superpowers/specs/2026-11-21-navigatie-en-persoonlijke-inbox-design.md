# Navigation, "Mijn post" and what is unread

Date: 2026-11-21. Covers `Brievie_host` (backend, `web/`) and `Brievie_ios` (screens,
navigation, visual style). Design only; nothing here is built. Plan:
`docs/superpowers/plans/2026-11-21-host-navigatie-en-persoonlijke-inbox.md`.

**Neighbours.** "Mijn taken" (`2026-11-21-mijn-taken-design.md`, cited "MT S 5.3"; server
and web merged in PR #36); the letter status design on branch `docs/letter-status-design`
(`2026-11-21-briefstatus-design.md`, cited "LS 4.1": Nieuw / Openstaand / Wacht op reactie
/ Afgehandeld); the people design on branch `docs/people-accounts-design`
(`2026-11-21-mensen-en-accounts-design.md`, cited "PA 9": one section "Mensen"); the rights
design (`2026-11-19-zichtbaarheid-postkamer-inrichting-design.md`, cited "RS 4.9"). The web
half of "Wie ziet wat" is being built on `feat/wie-ziet-wat-5` (Inbox rename, Afdelingen,
"Wie kan mijn post zien?"); this design is written against `main` (`ba477f0`) and names the
tasks that wait for that branch.

## 1. Intent (owner's decisions, 21-11-2026)

1. The start screen is the **personal inbox**, always the signed-in person's own. A side
   menu on the left: **Inbox** at the top, below it the personal inbox, then **Taken**,
   then the rest, **Instellingen** at the bottom.
2. It must be easy to see which letters are **new to me** (not yet looked at).
3. In an organisation, the web shows letters that **lie unreviewed or are never opened**,
   so nothing is left without being picked up. At home this is not needed.
4. Tasks: a member may **pick up a task nobody has taken**, only on post they have rights
   to. A task someone else has is not taken over by yourself; only that person or an admin
   hands it on.
5. A **workspace is a separate space**, as if it were a second installation. The switch
   "did nothing" once; switching must be clear and nothing may mix between workspaces.
6. **Mensen** (members and persons as one section, PA 9) gets its place in the new menu.
7. (Added the same day, iPhone) The app keeps a calm, card-based style "like Revolut, with a
   touch of the web interface" (section 10).

## 2. What exists (verified on `main`, `ba477f0`)

- **Web routing** (`web/src/router.tsx`): `/` (`routes/home.tsx`) lists workspaces and, with
  exactly one, redirects to `/w/$id/inbox`. Everything of a workspace is under
  `/w/$workspaceId/…` (`routes/workspace.tsx` checks membership in `beforeLoad` and returns
  `{ workspace }` as route context). Pages read it with `xRoute.useRouteContext()`.
- **Web menu** (`components/page.tsx`, `WorkspaceNav`): workspace name as a section title,
  then Mijn taken (count mine, warning when overdue), Te doen (count review + unassigned),
  Alle post, Prullenbak, Personen, Leden, Labels, Automatisering (not for viewers), AI,
  Instellingen (admins); then, outside the workspace, "Werkruimtes" (to `/`, hidden with
  one workspace) and "Apparaten". The account menu (top right) holds sessions, password,
  language, server log, sign out.
- **Counts**: `getInboxCounts` (`documents.services.inbox_counts`, one aggregate over
  `visible_documents`) and `countMyTasks` (`tasks.services.count_my_tasks`). Two requests per
  page for the menu.
- **Assignment** (`apps/tasks/assignment.py`): `assign` checks that the assignee is a
  regular reader of the letter and not a viewer; it does **not** check who the caller is
  relative to the current assignee. Any member who sees a task can give it to any reader,
  take it from a colleague, or clear a colleague's assignment through the API. The web
  (`components/task-actions.tsx`) shows "Ik doe dit" on every task not assigned to me, so
  also on a colleague's task, and "Geef aan…" on every task.
- **Viewed**: nothing records that a member opened a letter. `DocumentEvent` is letter
  history, shown to everyone who sees the letter.
- **Notices** (`AccountNotice`, `GET /me/notices`): account-wide, not tied to a workspace,
  although several codes name one (`emergency_access_switched_on/off`,
  `private_without_readers`, and the sight notices of branch 5). The web shows them above
  every page of every workspace.
- **iOS** (`Brievie_ios`, `origin/main`): one `NavigationStack`; the root is the post list
  of the current workspace with a title menu of scopes (Inbox, Alle post, one per person;
  `DocumentsModel+Scope.swift`) and a gear to Settings, where the workspace is chosen
  (`SettingsView`, `WorkspacesModel.select`). The post screen is rebuilt per workspace
  (`.id(InboxIdentity)`). The iOS "Mijn taken" plan (`docs/ios-my-tasks-plan`) adds Mijn
  taken as an entry in that title menu and keeps Inbox as the start screen.
  `BrievieUI/Brand.swift` defines only `brand` (navy `#16293F` / `#9DB8D6`) and `onBrand`.

### 2.1 Why the workspace switch "did nothing"

Commit `daa62fc` (19-11) records it: with **exactly one workspace**, the menu item
"Werkruimtes" linked to `/`, and `/` redirects straight to the only workspace's inbox, so
the click landed on the page you were on. The fix then was to **hide** the item with one
workspace; nothing was redesigned. What is left on `main`:

- The brand link "Brievie" in the app bar still goes to `/` and so, with one workspace,
  still does nothing visible.
- With several workspaces, switching is a detour: menu → overview table → workspace →
  always "Te doen", never where you were or your own post.
- **Pages do not remount on a switch.** TanStack Router keeps the same component when only
  `$workspaceId` changes (`/w/A/inbox` → `/w/B/inbox`). The shell keys only the search
  field by workspace id; `routes/inbox.tsx` reads `workspace.id` in a lazy `useState`
  initialiser (`isDismissedPending`), which then keeps workspace A's value in B. Today no
  route links from one workspace to the same page of another, so this is latent; the new
  switcher would expose it.
- The workspace list's query key `["workspaces"]` is the prefix of every per-workspace key
  (`["workspaces", id, …]`): invalidating the list (`routes/invitation.tsx`) refetches every
  query of every workspace. Not a leak, but it mixes the two on purpose of nothing.
- No test switches between two workspaces and checks what the page then asks the server.

No server path that crosses workspaces was found by reading: every count and list starts
from a `Membership` of the path's workspace and runs under RLS (`getInboxCounts`,
`countMyTasks`, `listMyTasks`, `searchDocuments`, the trash, duplicates). Notices are the
one place that shows workspace-related text in every workspace (2, above). Section 9 adds a
test that asserts the isolation instead of trusting the reading.

## 3. Goals, non-goals, anti-goals

Goals: a menu in the order of the owner's decision on web and iPhone; "Mijn post" as the
start screen; a per-member unread state in lists and counts; a "Blijft liggen" signal for
organisations; picking up only unassigned tasks; switching workspaces as a clear move
between separate spaces.

Non-goals: e-mail or push notifications for unread or lingering post; read receipts; a
"who has read this" list; paging beyond the existing 200-row cap; changes to who sees what.

Anti-goals: anything that tells a member that someone else has or has not read a letter by
name; a count that includes letters the viewer may not see; unread state in the letter's
history or the audit log.

## 4. The menu

### 4.1 Web: order and entries

The side menu (`navpane`) of a workspace, top to bottom:

| # | Entry (NL / EN) | Route | Count | Who |
|---|---|---|---|---|
| — | **Workspace switcher** (name, kind; chevron when more than one) | — | — | everyone |
| 1 | **Inbox** | `/w/$id/inbox` | review + unassigned; plus "Blijft liggen" warning (5) | everyone |
| 2 | **Mijn post** / My post | `/w/$id/mijn-post` (new) | unread in Mijn post | everyone |
| 3 | **Mijn taken** / My tasks | `/w/$id/taken` | mine; warning style when overdue | everyone |
| 4 | **Alle post** / All post | `/w/$id/post` | — | everyone |
| 5 | **Labels** | `/w/$id/labels` | — | everyone |
| 6 | **Mensen** / People (PA 9); until then **Personen** and **Leden** | `/w/$id/mensen` | — | everyone |
| 7 | **Afdelingen** / **Groepen** (branch 5, only when on) | `/w/$id/afdelingen` | — | everyone |
| 8 | **Automatisering** | `/w/$id/automatisering` | — | not viewers |
| 9 | **Prullenbak** / Trash | `/w/$id/prullenbak` | — | everyone |
| — | divider, pushed to the bottom of the pane | | | |
| 10 | **Instellingen** / Settings | `/w/$id/instellingen` | — | everyone (see 4.2) |

"Mijn post" is the menu name of the personal inbox (B1, short, fits a tab bar, pairs with
"Mijn taken" and the existing "Wie kan mijn post zien?"; see Besluiten B1). Counts keep the
existing rules: a number only when above zero, the words in `aria-label`, the warning
colour never alone.

### 4.2 Instellingen at the bottom

Instellingen becomes one place for everyone, with sections by role, so the menu stays short:

- Everyone: **Jouw account** (language, password, sessions; today in the account menu, which
  keeps them as shortcuts), **Apparaten** (today `/koppel-app`, a top-level item), **Wie kan
  mijn post zien?** (branch 5 puts it in the account menu; it moves here and stays there as
  a shortcut).
- Admins in addition: the current workspace settings, **AI** (today a top-level item),
  **Wie ziet wat** (branch 5), the **Logboek**, export.

The routes stay (`/w/$id/ai`, `/koppel-app`, …); only the menu changes. Old links keep
working. Server admin: "Serverlogboek" stays in the account menu (it is not a workspace
thing).

### 4.3 The workspace switcher

At the top of the side menu, above Inbox, a button with the workspace's initial in a
coloured square, its name and kind ("Thuis", "Organisatie"):

- One workspace: the same block without chevron and without a button role (a label, never a
  dead button: this is the bug of 2.1).
- Several: a React Aria `MenuTrigger`; the menu lists the workspaces (initial, name, kind,
  role) with the current one checked; at the bottom "Alle werkruimtes" (`/werkruimtes`).
  **No counts of other workspaces** in the switcher: each workspace is its own space.
- Choosing another workspace goes to **that workspace's Mijn post** (`/w/<B>/mijn-post`),
  not to the same page: a letter id or a rule of A means nothing in B. Choosing the current
  one closes the menu and does nothing else.
- The last workspace used is remembered in this browser (`localStorage`, per-viewer
  convenience, wrapped in try/catch); `/` sends you there if you are still a member, else to
  the first workspace in the list, else to `/werkruimtes` (no workspace: the overview and
  its empty state).
- On a switch: the workspace subtree **remounts** (the workspace route's outlet is keyed by
  `workspaceId`, as iOS keys its post screen with `.id(InboxIdentity)`), the search and undo
  are already forgotten (`searchInWorkspace`, `undoInWorkspace`), and notices of A do not
  show in B (8).
- The page title carries the workspace when there are several: "Mijn post · Bedrijf -
  Brievie", so two tabs are told apart.
- The app bar's brand link goes to the current workspace's Mijn post (no detour via `/`).

### 4.4 Start screen

`/` and sign-in land on `/w/<last or first>/mijn-post`. The old home becomes
`/werkruimtes` (table of workspaces, kind, role), reached from the switcher.

### 4.5 iPhone and iPad

A left side menu does not suit an iPhone. Equivalent with the same order (see 10 for the
style):

- **iPhone**: a `TabView` with four tabs in the web's order: **Inbox**, **Mijn post**,
  **Taken**, **Meer**. The app opens on **Mijn post** (the selected tab at launch and after a
  workspace switch), although Inbox is the first tab: the order follows the menu, the start
  follows decision 1. "Meer" is a list in the web's order: Alle post, Labels, Prullenbak,
  Instellingen (Mensen is not in the app: administering people stays web-only, PA 11).
  Scan is a round action
  button on Inbox and Mijn post (it is the app's main action), not a tab.
- **Workspace pill** at the top left of every tab root: initial, name, chevron. It opens a
  sheet with the workspaces (Revolut-style account picker, section 10). This replaces the
  workspace list inside Settings (which keeps the server and unpair parts).
- **iPad**: `NavigationSplitView` with a sidebar in exactly the web's order (switcher on
  top, Instellingen at the bottom); it collapses to the tab layout in compact width.
- The title menu of scopes (`DocumentsModel+Scope`) disappears as navigation: Inbox and
  Alle post become their own tabs or rows; a person's post is reached from Alle post's
  filter ("Voor wie").

## 5. "Ongelezen": what is new to me

### 5.1 Name

**Ongelezen** / **Unread** (one letter: "ongelezen"; action "Markeer als gelezen" /
"Markeer als ongelezen"). It is deliberately not "Nieuw": in the letter status design
"Nieuw" means *not yet checked* (LS 4.1), a fact of the letter shared by everyone.
"Ongelezen" is a fact of **one member and one letter**. GLOSSARY gets both lines.

### 5.2 When a letter is unread

A letter is unread for member M when all hold:

1. M sees it (`visible_documents(M)`; the trash is out);
2. it is not `processing` (a letter still being read is nobody's news yet);
3. it arrived after M's starting point `Membership.unread_from` (set to the moment of the
   migration for existing members and to the moment of joining for new ones, so nobody
   starts with 10,000 unread letters);
4. M has no **view mark** for it.

### 5.3 When a letter counts as read

- When M **opens the letter** in the web (`/w/$id/post/$documentId` rendered with
  its first page or its text) or in the app (the reader or the confirmed-letter screen
  appears). The client then calls `markDocumentRead` once per opening. Not on a plain `GET`:
  prefetching, background refetches and scripts must not mark anything.
- When M **uploads or scans** it (M made it, there is nothing new to M): the upload writes
  M's mark as M.
- When M chooses "Markeer als gelezen" (one row) or "Alles gelezen" (the rows on screen, at
  most 200).
- Reviewing in the Inbox without opening (inline name choice) does **not** mark it read.
- "Markeer als ongelezen" removes M's own mark.
- Emergency access (RS 4.9): reading through it marks it read for that admin only and never
  counts as "opened by an addressee" (5.5).

### 5.4 Where it shows

- **Mijn post** and **Alle post** (and a person's post): unread rows bold with a dot in the
  brand colour and "Ongelezen" in the row's accessible name; filter "Alleen ongelezen".
  Status badges of LS (Nieuw, Openstaand, …) stay next to it; they say different things.
- The menu count of **Mijn post** is its unread count (not its total).
- The **Inbox** shows no unread marks: everything there is to-do for whoever sorts.
- The letter page: "Markeer als ongelezen" in the command bar.
- No other place, no count of other members' unread letters, never in history or the audit
  log.

### 5.5 "Opened by an addressee"

For the organisation signal (6), the server also keeps, per letter, **whether anybody it is
addressed to has opened it**: `Document.addressee_opened_at` (nullable, set once, by the
system, at the first `markDocumentRead` of a member whose own post ids
(`brievie_my_post_ids`, persons and departments) meet the letter's `addressee_ids`). It
says nothing about who; it is never returned to clients as a time, only used to filter.
When the addressees change, it is cleared if none of the new addressees' members has a
view mark (recomputed in the same `organise` path that syncs `addressee_ids`, as the system).

## 6. "Blijft liggen" (organisations only)

### 6.1 What lingers

In a workspace of kind `business`, with the switch on (default on for business, off and
hidden for home):

- **In de Inbox te lang**: a letter in the Inbox state `review` or `unassigned` longer than
  `lingering_inbox_days` (default 2) since it arrived.
- **Nog niet geopend**: a confirmed letter (`done`) with at least one addressee, not opened
  by any of its addressees (`addressee_opened_at` null) `lingering_unopened_days` (default
  5) after it was confirmed.

Days are calendar days in Europe/Amsterdam (as MT S 11); working days are a later option
(Besluiten B3).

### 6.2 Who sees it, and how

- **Sorters** (`Membership.sorts_post`, or admins when nobody is flagged, the existing
  `brievie_actor_sorts_post`) and **admins**.
- Only letters **the viewer can see** (`visible_documents(viewer)`): a private letter the
  admin may not read is not counted, not even as a number (RS "a count of hidden matches is
  never returned"). Its addressee sees it as unread in Mijn post instead.
- Menu: on **Inbox**, a second, warning-styled number "3 blijven liggen" next to the normal
  count (words in `aria-label`).
- Inbox page: a section **Blijft liggen** above the list (collapsed to a line when empty),
  two groups with the existing row layout plus the age ("ligt er 4 dagen", "5 dagen niet
  geopend") and the addressees' names (names are a directory). No sentence says *who* did
  not open it beyond "de geadresseerde".
- At home, nothing of this appears, and the endpoint answers an empty result.

### 6.3 Privacy

The mark is personal data about reading behaviour. It is used for exactly two things: the
member's own unread state, and the absence of any addressee's opening for the
organisation's sorters. PRIVACY.md gets that sentence. Marks are deleted with the letter
(cascade at purge), and for one member when they leave the workspace (`forget_member`).
They are not in the member's own export (they describe the app, not the post; Besluiten
none: the export stays as is).

## 7. Tasks: picking up and handing on

### 7.1 The rule (server, `apps.tasks.assignment.assign`)

Unchanged: the assignee must be a regular reader of the letter and not a viewer
(`assignee_cannot_read`); a task without a letter is not assigned (`task_without_letter`).

New, in the same lock:

| Task now | Caller wants | Allowed for |
|---|---|---|
| unassigned | give it to self ("Ik doe dit") | any member who sees the task (sight = "rights to that post") |
| unassigned | give it to another reader ("Geef aan…") | any member who sees the task |
| assigned to me | release ("Niet meer aan mij") or give to another reader | me |
| assigned to someone else | anything (take, give, release) | **admins only** |

Otherwise 409 `task_taken` / "Iemand anders doet deze taak al. Vraag die persoon of een
beheerder om hem door te geven." The same assignee again stays a no-op (200). System
releases (`release_unseeing_assignees`, `release_member`) are unchanged.

"Rights to the other person" is sight: at home, a parent who reads a child's post may pick
up the child's unassigned task; a child who does not read the parent's post never sees that
task. In an organisation, the department's tasks are seen by its members (RS 4.9.4) and so
can be picked up by them.

### 7.2 Telling the client

`TaskOut` and the "Mijn taken" item get three booleans (default `false`): `can_take`,
`can_hand_over`, `can_release`, computed by the same rule, so web and app never derive it.
Web: "Ik doe dit" only with `can_take`; "Niet meer aan mij" with `can_release` (for an admin
on another's task: "Vrijgeven"); "Geef aan…" with `can_hand_over`. A task of someone else
shows "Doet: Anne" without buttons for a member. iOS: the iOS "Mijn taken" plan's
`canTakeOn` / `canRelease` / `canHandOver` (its Task 5) read these fields when present and
fall back to its own rule against an older server, with the "assigned to someone else"
case excluded.

## 8. Workspaces are separate spaces

- Every web route of a workspace is under `/w/$workspaceId/` (exists). The subtree remounts
  on a switch (4.3). The workspace list's query key becomes `["workspace-list"]`, so no list
  refresh touches per-workspace data.
- **Notices**: `AccountNotice` gets a nullable `workspace` (FK, set for the codes that are
  about one workspace); `NoticeOut.workspace_id: UUID | None = None`. The web shows a
  notice with a workspace only inside that workspace (and on `/werkruimtes`), account
  notices everywhere. Old notices without the field stay account-wide.
- **Counts**: the new `getMenuCounts` (9) is per workspace; the switcher shows no counts.
- **iOS**: the switcher sheet replaces Settings' workspace list; switching rebuilds the post
  screens (exists) and resets the tab to Mijn post; reminders and calendars stay per
  workspace (exists).
- **One user, two workspaces** is asserted by a test (plan Task 11): the same user, a letter
  in each, every count, list, search, my-tasks and notice answer of A without B's data.

## 9. API (additive, `/api/v1`)

| Operation | Method, path | Auth | Notes |
|---|---|---|---|
| `getMenuCounts` | GET `/workspaces/{id}/menu/counts` | session or device | `inbox` (review + unassigned), `my_post_unread`, `my_tasks` (mine), `my_tasks_overdue`, `lingering` (0 unless sorter/admin in business), `shows_lingering: bool`; all ints default 0 |
| `listDocuments` (exists) | GET `/workspaces/{id}/documents` | | new query params `addressed_to_me: bool = false`, `unread_only: bool = false` |
| `DocumentSummaryOut` (exists) | | | new `unread: bool = False` |
| `DocumentOut` (exists) | | | new `unread: bool = False` |
| `markDocumentRead` | POST `/workspaces/{id}/documents/{doc}/read` | session or device | 204; 404 as `getDocument` |
| `markDocumentUnread` | DELETE `/workspaces/{id}/documents/{doc}/read` | session or device | 204; 404 as `getDocument` |
| `markDocumentsRead` | POST `/workspaces/{id}/documents/read` | session or device | body `document_ids` (max 200); ids the member does not see are skipped silently, count of marked returned |
| `listLingeringLetters` | GET `/workspaces/{id}/inbox/lingering` | session or device | `{too_long_in_inbox: [...], not_opened: [...], inbox_days, unopened_days}`; summaries plus `days: int`; empty unless sorter/admin in business |
| `assignTask` (exists) | | | new 409 `task_taken` |
| `TaskOut`, my-task item (exist) | | | new `can_take`, `can_hand_over`, `can_release: bool = False` |
| `WorkspaceSettingsOut/In` (exist) | | admin, session | `lingering_enabled: bool`, `lingering_inbox_days: int` (1–30), `lingering_unopened_days: int` (1–60); defaults by kind |
| `NoticeOut` (exists) | | | `workspace_id: UUID \| None = None` |

`addressed_to_me` filters on the member's **own person** ids (`brievie_my_post_ids` minus
departments; Besluiten B2 for departments) with the GIN index on `addressee_ids`. A member
without a person gets an empty list and `has_person: false` on `getMenuCounts`
(`has_person: bool = True`), so the page can say why.

## 10. Visual style of the app ("Visuele stijl app")

Owner: keep the app "Revolut-like, with a touch of the web interface". That is a
*direction*, not a brand: no Revolut colours, glyphs, names or layouts copied.

- **Calm background, rounded cards.** Screens on `systemGroupedBackground`; content in
  cards (`secondarySystemGroupedBackground`, corner radius 16, no borders, a soft shadow only
  in light mode). Lists of letters stay real `List` rows (VoiceOver, swipe actions) inside an
  inset-grouped style; the cards are for summaries.
- **Large, bold titles and numbers.** Every tab root has a large navigation title. The Mijn
  post root opens with a row of count cards: "4 ongelezen", "2 taken · 1 te laat", and for
  sorters "3 blijven liggen", each a large rounded number (`.largeTitle`, `.bold`,
  monospaced digits) with a small caption, tappable to its list. Amounts and deadlines in
  task rows use the same number style at `.title3`.
- **Workspace pill** (Revolut's account picker as the pattern): top left, a rounded capsule
  with the workspace initial in a brand-coloured circle, the name and a chevron; the sheet
  lists the workspaces as large rows with initial, name, kind and role, the current one
  checked. One workspace: the pill without chevron, not a button.
- **Quick actions as round buttons.** Under the count cards: round 56-pt buttons with a
  label underneath: **Scannen**, **Zoeken**, **Alles gelezen** (Mijn post) or **Scannen**,
  **Naam kiezen** (Inbox). Scan also stays reachable as the main action.
- **The web as the accent.** The brand navy `#16293F` (dark mode `#9DB8D6`) for selection,
  the unread dot, the pill's initial and primary buttons; the warning colour of the web
  for "te laat" and "blijft liggen", always with words. System font (SF Pro) with the web's
  weights: semibold for titles of rows, bold for numbers.
- **Constraints stay hard**: every card and round button works with VoiceOver (one element
  with a full label, e.g. "4 ongelezen brieven in Mijn post"), the largest Dynamic Type sizes
  wrap the cards into one column and the round buttons into a list, contrast 4.5:1.

What changes in `BrievieUI` (no dependency): `Brand.swift` gains `BrievieColor.surface`,
`card`, `warning`, `unread`; a `BrievieRadius` (card 16, pill capsule); a `NumberText` style
(bold, monospaced digits, scales with Dynamic Type); components `CountCard`,
`RoundActionButton`, `WorkspacePill`. The rest of the app keeps its look until a screen is
touched.

## 11. Rulings (made on the owner's behalf)

| # | Ruling | Why |
|---|---|---|
| N1 | Read = the letter opened by the member (explicit call), or uploaded/scanned by them | a `GET` is also prefetch and refresh; explicit is honest |
| N2 | `Membership.unread_from` as the starting point | no flood of unread letters at rollout or join |
| N3 | View marks are their own table, never history or audit | history is shown to others |
| N4 | Inbox rows carry no unread mark | the Inbox is a to-do list for sorters, not news |
| N5 | A switch lands on the other workspace's Mijn post | ids of one workspace mean nothing in another |
| N6 | The switcher shows no counts of other workspaces | owner: a workspace is a separate space |
| N7 | Admins may reassign a task someone else has; sorters may not (unless admin) | owner: "only that person or an admin" |
| N8 | Lingering counts only letters the viewer sees | no count of hidden matches |
| N9 | iPhone opens on Mijn post although Inbox is the first tab | order follows the menu, start follows decision 1 |
| N10 | AI, Apparaten and "Wie kan mijn post zien?" move into Instellingen; their routes stay | short menu, Instellingen at the bottom |

## 12. Not in this design

Push or e-mail for unread or lingering post; working-day thresholds; "read by" lists;
paging; a desktop-style multi-select for marking read beyond the 200 rows on screen;
Mensen itself (PA); the status badges themselves (LS).

## 13. Besluiten voor Stijn

Alleen vragen waarvan het antwoord verandert wat er gebouwd wordt.

- **B1. Naam in het menu: "Mijn post" of "Persoonlijke inbox"?** "Persoonlijke inbox" is
  lang (past niet in een tabbalk op de iPhone) en lijkt op "Inbox", wat juist iets anders is
  (te controleren post). *Aanbeveling: "Mijn post"* (EN "My post"), naast "Mijn taken".
- **B2. Telt post voor je afdeling mee in Mijn post?** Alleen post op jouw naam is helder;
  met afdeling erbij wordt Mijn post in een kantoor snel een tweede Inbox. *Aanbeveling:
  alleen post op jouw naam; in een organisatie een filterknop "Ook mijn afdeling"* (thuis:
  "Ook mijn groep").
- **B3. Drempels en publiek van "Blijft liggen".** *Aanbeveling: aan voor organisaties, 2
  dagen in de Inbox en 5 dagen niet geopend (kalenderdagen), instelbaar door de beheerder,
  zichtbaar voor postverdelers en beheerders.* Werkdagen kan later.
- **B4. Wanneer is een brief gelezen?** *Aanbeveling: als je de brief opent (web en app) of
  hem zelf hebt gescand; niet bij alleen controleren in de Inbox.* Alternatief "pas als de
  eerste pagina echt in beeld is geweest" is preciezer, maar lastiger te bouwen en te testen.
- **B5. iPhone: tabbalk of menu-sheet?** *Aanbeveling: tabbalk met Inbox, Mijn post, Taken,
  Meer*, de app opent op Mijn post. Een menu-sheet (zoals het web) kost op de iPhone altijd
  een extra tik.
- **B6. AI, Apparaten en "Wie kan mijn post zien?" onder Instellingen?** *Aanbeveling: ja*;
  het menu blijft kort en Instellingen staat onderaan, zoals je vroeg. De oude adressen
  blijven werken.
- **B7. Meldingen per werkruimte.** Meldingen over één werkruimte (noodtoegang, wie ziet
  wat) staan nu in elke werkruimte. *Aanbeveling: alleen tonen in de werkruimte waar ze over
  gaan*; dat vraagt één veld erbij op de server.
