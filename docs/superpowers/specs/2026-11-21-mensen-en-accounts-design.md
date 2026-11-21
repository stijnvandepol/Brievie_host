# Mensen: one list of people, each with their own account

Date: 2026-11-21. Covers `Brievie_host` (backend, `web/`) and, as a short list,
`Brievie_ios`. Design only; nothing here is built. Plan:
`docs/superpowers/plans/2026-11-21-host-mensen-en-accounts.md`.

**Neighbours.** The rights design `2026-11-19-zichtbaarheid-postkamer-inrichting-design.md`
(cited "S 4.1") and its plan `2026-11-20-host-wie-ziet-wat.md` (cited "WZW T38"), built on
`main` up to merge point M4 (`ba477f0`); its web slice (WZW T42–47) is being built on
`feat/wie-ziet-wat-5` and is **not** on `main`. DECISIONS "Wie ziet wat, slice A2b/A2c"
and the security reviews of PR #38/#39 bind this design. Planned designs that touch the
same code: `docs/letter-status-design` (status under a person: `routes/persons.tsx`,
`routes/inbox.tsx`), `docs/external-access-design` (`accounts/auth.py`, devices; no
overlap in substance), `docs/intake-and-2fa-design` (c45b285: a scan subfolder per person
with an account; see section 10).

## 1. Intent

Stijn (21-11-2026): "I don't understand the difference between members and persons.
Users must always log in with their own credentials and their own rights. Everyone who
can receive post also gets their own account. Such an account can be active or not
active, and when letters come in for someone new, a person must be added. You must be
able to see whether a user has ever logged in, so this becomes one section."

Additions (same day): "Mensen" is **a section of its own** in the web interface, not
under Instellingen: one place listing everyone with their account state and last login.
For someone who never logged in it offers **"Uitnodigen"** on their row and in their
detail view; for a pending invitation **"Opnieuw versturen"** and **"Intrekken"**.

## 2. What exists (verified on `main`, `ba477f0`)

- `people.Person` (workspace table, RLS forced): `display_name`, `name_variants`,
  `user` (nullable FK, unique per workspace; `save()` demands a membership), `archived_at`,
  `kind` `person` | `department`. Every addressee link (`DocumentAddressee.person`), the
  derived `Document.addressee_ids` (GIN), `DocumentSuggestion.person`, `Label.department`,
  name matching and the AI's closed list P1..Pn key on Person ids.
- **Every member already has a person.** `people/0002_persons_for_members` gave each
  existing member one; `ensure_person_for_member` gives each new one one (setup,
  `create_workspace`, an invitation without `person_id`). So today a "Person" with
  `user_id` set *is* a member's own post slot, and one without is a name that receives
  post but cannot sign in.
- `workspaces.Membership` (not under RLS): user, workspace, `role`, `sorts_post`. Every
  visibility helper (`visibility_sql`), `lock_memberships`, `sorter_ids` and
  `viewer_pairs_sql` iterate this table.
- `workspaces.Invitation` (server table): may carry `person` (one without an account);
  `_invitable_person` refuses a linked or archived person; `person_requires_email` demands
  an address when the person has private post; accept links the person
  (`_link_person`), recomputes readers (`on_member_added`) and notifies (`notify_join`).
  Admin invitations need the confirmed preview where they widen sight (PR #39 finding 1).
  `createInvitation`, `listInvitations`, `revokeInvitation`, `renewInvitation` are admin
  and `session_auth`. Brievie sends no e-mail: the link is shown once to the admin.
- Removing a member (`members.remove_member`) unlinks their person
  (`_unlink_and_unassign`), forgets their reader rows, shares and department
  memberships, removes their own export, runs `sight_change` (P6/P7: the person's
  non-private post goes to the sorters, who are told) and, on the last membership,
  switches the account off (`account_state.deactivate_account`: sessions, devices,
  pairing codes, reset links). Afterwards the person is indistinguishable from one that
  never had an account.
- `User.last_login` is real: `django.contrib.auth` is installed, its `update_last_login`
  receiver fires on the `django_login` inside `start_session` (password, setup,
  invitation, reset). `Device.last_seen_at` records app use.
- Web: separate nav items and pages "Personen" (`routes/persons.tsx`) and "Leden"
  (`routes/members.tsx`, invitations, roles, devices, reset links).
- iOS uses `listPersons` and `createPerson` only (addressee picker in review).

**Gap in that invariant.** `link_member` with `user_id=None` lets a member unlink
themselves without getting a new person: they keep a `Membership` and have no `Person`,
and would be missing from a list of people. Ruling M8: the invariant "every member has
exactly one person" is *maintained*: an unlink of a member gives that member a fresh
person at once (`ensure_person_for_member`, same transaction; a new person has no letters,
so nobody's sight changes); the old person becomes "niet actief".

**Finding.** The data model already has the shape Stijn asks for; the product shows it as
two concepts. The work is mostly naming, one list, one missing state, and the edges.

## 3. The model

### 3.1 One concept: "Mensen"

A **person** (Mensen, EN "People") is someone in a workspace who can receive post. Each
person has an **account in that workspace**, in one of four states:

| State (NL / EN) | Data | Can sign in | Shown as |
|---|---|---|---|
| **Niet actief** / Not active | `kind=person`, `user_id IS NULL`, no open invitation, `deactivated_at IS NULL` | no | "Nooit ingelogd" |
| **Uitgenodigd** / Invited | `user_id IS NULL`, an `Invitation` with `person_id` = this one, not accepted, not revoked (expired included, marked "verlopen") | no | "Uitgenodigd op …, verloopt …" |
| **Actief** / Active | `user_id` set (so a `Membership` exists) | yes | "Laatst ingelogd op …" or "Nooit ingelogd" |
| **Uitgeschakeld** / Switched off | `user_id IS NULL`, `deactivated_at` set | no | "Uitgeschakeld op …, laatst ingelogd op …" |

Precedence when two hold: an open invitation makes a switched-off person "Uitgenodigd".

**Ruling M1: the account slot is the Person row; the login identity (`User`) exists only
from activation.** An inactive account has no `User`, no `Membership`, no e-mail and no
password. Reasons, each decisive on its own:

- `brievie_covered_post_ids()` counts a person as covered when `user_id IS NOT NULL`, and
  `viewer_pairs_sql` treats every `Membership` row as a viewer. A `User` + `Membership`
  per inactive person would move every inactive person's post away from the sorters and
  add phantom members to every lock, sorter fallback and preview: exactly the silent
  change of sight the rights design forbids.
- `User.email` is unique and required for sign-in; inactive people have none.
- The security requirements then hold by construction: with no `User` there is nothing
  that can sign in, receive a reset link, pair a device, appear in a sign-in or account
  list, or be found by e-mail.

**Per workspace or global?** The slot (Person, its state) is **per workspace**; the
credentials (`User`) are **global**, one per human and e-mail address. A human in two
workspaces has two persons and one account; "Laatst ingelogd" is the account's, the same
in both. An inactive person needs no unique name beyond today's rule (display name unique
per workspace, ignoring case).

### 3.2 Departments stay a group, not an account

**Ruling M2: keep `kind = department` in the Person table; show departments outside
Mensen.** Splitting into a `Department` table would re-key `addressee_ids`, every
`DocumentAddressee`, `DocumentSuggestion.person`, `Label.department`, the
`department`-reason reader rows and both SQL helpers: a policy migration and a new
measurement for no change in behaviour. In the product a department is not a person:
it has members, never an account or a state, is not listed under Mensen and keeps its own
page "Afdelingen" (WZW T43, branch 5). `listPeople` returns persons of kind `person` only.
A person's detail shows the departments they are in.

### 3.3 Transitions and who may make them

| From → to | How | Who | Endpoint |
|---|---|---|---|
| (none) → niet actief | "Persoon toevoegen" on Mensen, or "Nieuwe persoon" in the addressee picker (Inbox, letter, app) | any member (not viewer), as today | `createPerson` (unchanged, device token allowed) |
| niet actief / uitgeschakeld → uitgenodigd | "Uitnodigen" / "Opnieuw uitnodigen": role, validity, optional address (required with private post) | admin, web session | `createInvitation` with `person_id` (unchanged) |
| uitgenodigd → uitgenodigd | "Opnieuw versturen": a new link, the old one stops | admin, web session | `renewInvitation` (unchanged) |
| uitgenodigd → niet actief / uitgeschakeld | "Intrekken" | admin, web session | `revokeInvitation` (unchanged) |
| uitgenodigd → actief | the invitee accepts (new or existing account) | the invitee | `acceptInvitation` (unchanged; now also clears `deactivated_at`) |
| actief → uitgeschakeld | "Uitschakelen" (an admin), "Werkruimte verlaten" (oneself) | admin / the member | `removeMember`, `leaveWorkspace` (unchanged; now also set `deactivated_at`, `former_user`) |
| niet actief / uitgeschakeld → gearchiveerd | "Archiveren" | any member (not viewer), as today for a person without an account | `updatePerson` (now also revokes an open invitation for the person) |

**Activation goes only through the invitation flow** (Ruling M3). There is no "activate"
endpoint and no way to set a password for someone. Linking an existing member to a
person (`linkPersonToMember`) stays for old clients and is not offered on Mensen.

"Opnieuw versturen" keeps Stijn's word, but Brievie sends nothing: the dialog shows the
new link (and QR) once and says "De oude link werkt niet meer." (plan).

### 3.4 Last sign-in

`last_sign_in_at` = `User.last_login` (web sign-in of any kind). `last_app_use_at` =
the newest `Device.last_seen_at` of an unrevoked device. "Nooit ingelogd" = no account,
or an account with `last_login IS NULL` and no device seen. A switched-off person keeps
showing the former account's last sign-in through `former_user` (Ruling M4: `former_user`
is display-only and read by no visibility, lock or authority code; a test asserts the SQL
helpers do not mention it). Both are global per account (3.1); a member who is
also in another workspace shows the same time in both. They are shown to admins only
(Ruling M5).

## 4. Who sees the post of a person without an active account

The rule of sight is not changed by slices A and B. The table maps today's S 4.1 onto the
new states; "niet actief", "uitgenodigd" and "uitgeschakeld" behave alike, because none
has a linked member.

| Letter on the person | Thuis (E on) | Organisatie (E off) |
|---|---|---|
| Not private | every member | the sorters (flagged, else the admins), R34 |
| Private, made private by a member | that member (they keep a share) and other shares | the same |
| Private by Zorg or a rule, no reader | stays visible (`private_skipped`), as above | the same |
| Private, readers were the person's former account | **nobody** (shares only) until activated again, emergency access, or "Beheerd door" (slice C) | the same; emergency access is on by default |

Examples. A child, a parent whose post you handle, someone deceased: at home everyone
sees their post (E on); in an organisation the sorters do. A former employee: their
non-private post goes to the sorters at once, with a notice (P7, unchanged).

**The gap (row 4).** Switching off an account whose person has private post leaves those
letters without a reader. That is today's behaviour of `removeMember`. At home emergency
access is off by default, so nobody can open them. Two answers:

1. **In slice A (always):** the "Uitschakelen" dialog says it before the admin confirms,
   from a per-person read `getPersonHasPrivatePost` that the dialog calls when it opens
   (admin, web session, a boolean, no count). It is deliberately not a column of the
   list: a list column would show an Organisatie admin at a glance which colleagues have
   private (Zorg) letters, the passive disclosure PR #39 findings 4 and 5 removed. One
   person at a time it reveals no more than the `email_required` answer to an invitation: "Op naam van Anne staan privébrieven. Na uitschakelen kan niemand ze
   lezen, tot je Anne opnieuw uitnodigt of iemand de post laat beheren." (plan).
   Nothing is silent; nothing widens.
2. **Slice C, "Beheerd door" (see Besluit B2):** an admin names one member who manages a
   person without an active account. The manager sees that person's post as their own
   (a new term in `brievie_my_post_ids`, reader reason `managed` for private letters),
   and the person counts as covered, so the sorters stop getting it. A widening: preview
   and confirmation (S 4.4), never self-granted when the person has private post (409
   `cannot_grant_self`, as linking), the manager is told (`sight_widened`), the sorters
   too (`sight_narrowed`). Activating the person ends the management (narrowing, the
   manager is told) — ruling M6, since an adult with their own account decides who reads
   their post; sharing a letter remains. Removing the manager as member ends it too (P7).
   This is the narrow, inactive-only form of the "meekijken" that S 9 removed; delegation
   for active accounts stays out.

## 5. Letters for someone new

- **Inbox, letter and app picker:** typing a name nobody has offers "Nieuwe persoon:
  ‘<naam>’" (plan) at the bottom of the list; choosing it calls `createPerson` and puts
  the new person on the letter (one action for the user, two calls; the second goes
  through `organise` as always). The picker shows a likely existing match first when the
  typed text normalises to an existing name or variant ("Bedoel je J. de Vries?"), so
  "Anne" and "A. de Vries" do not become two people by accident.
- **Name matching** proposes existing persons only; it does not invent names.
- **The AI** today picks from a closed list (P1..Pn; `validation` drops anything else;
  `DocumentSuggestion` must name a person). Proposing an *unknown* name needs a new
  output field and a new suggestion kind: Besluit B3.
- **Ruling M7: nothing automatic creates a person or an account.** Not the autopilot, not
  a rule, not the pipeline, not the scan folder (an unknown subfolder is never read, per
  the intake design). A person is created only by a human's request.

## 6. Security

- Inactive, invited and switched-off people have no `User` (M1): they cannot sign in, get
  a reset link (`PasswordReset.user` is a `User`), pair a device, or appear in
  `listMembers`, sessions or the server's account lists. Tests assert each path by
  absence (plan Task 5).
- Activation only through `createInvitation` → `acceptInvitation`, both unchanged: admin,
  `session_auth`, `email_required` for a person with private post, the admin-role
  preview, `notify_join`.
- `listPeople` is `session_auth` (a device token gets 401: it lists account facts). Every
  member may call it (the names are already in `listPersons`); the account columns
  (e-mail, last sign-in, app use, invitation, `can_manage_account`) are filled for admins
  only and empty/null for others, and for others `state` is collapsed to `active` or
  `inactive` (M5).
- `getPersonHasPrivatePost` (`GET /workspaces/{id}/persons/{person_id}/private-post`,
  admin, `session_auth`): `{has_private_post: bool}`, read as the system, used only by the
  "Uitschakelen" and "Uitnodigen" dialogs.
- Audit (all through `apps.audit.services.record`, closed list):
  - create: **new** `person_created` (target person, no values). Today creating a person
    writes nothing; under "everyone is an account" it should.
  - invite / resend / withdraw: `invitation_created`; `renewInvitation` records
    `invitation_revoked` (reason `renewed`) plus `invitation_created`;
    `invitation_revoked` (reason `admin`). All existing, unchanged.
  - activate: `invitation_accepted` + `person_linked` (existing).
  - deactivate: `member_removed` / `member_left` + `person_unlinked` (existing).
  - slice C: **new** `person_manager_set`, `person_manager_cleared` (values `{"member"}`, a
    name: the manager is a person).
- No new log line holds a name or e-mail.

## 7. Migration

`people/0004_person_account_state`: `Person.deactivated_at` (datetime, null) and
`Person.former_user` (FK `User`, null, `SET_NULL`, `related_name="+"`). No data moves:
every member already has a person (0002), every unlinked person becomes "niet actief" by
the derivation. Persons unlinked by an earlier removal also show as "niet actief" (no
reliable source: an unlink can also have been the member's own; the audit log is not read
back into data). Departments are untouched. Reversible.

**Proof.** `viewers()` for every letter of a seeded workspace (both kinds, E on and off,
departments on and off, private letters with share and addressee readers, an unlinked and
a removed member) is equal before and after the migration and after slice C's function
migration with no manager set (plan Tasks 1 and 11).

## 8. API (additive, `/api/v1`)

New:

- `GET /workspaces/{workspace_id}/people` `listPeople` (`session_auth`, any member) →
  `list[PersonAccountOut]`, persons of kind `person`, active ones by name, archived last;
  query `state: str = ""` (`inactive|invited|active|deactivated`, empty = all).
- `PersonAccountOut`: `id`, `display_name`, `name_variants: list[str]`, `archived: bool`,
  `document_count: int`, `state: str`, `user_id: UUID | None = None`, `role: str = ""`,
  `sorts_post: bool = False`, `department_ids: list[UUID] = []`, `email: str = ""`,
  `last_sign_in_at: datetime | None = None`, `last_app_use_at: datetime | None = None`,
  `deactivated_at: datetime | None = None`, `invitation_id: UUID | None = None`,
  `invitation_expires_at: datetime | None = None`, `invitation_expired: bool = False`,
  `can_manage_account: bool = False`. Slice C adds `managed_by_user_id: UUID | None =
  None`. Nullable fields primitives only; no `anyOf`.
- `GET /workspaces/{workspace_id}/persons/{person_id}/private-post`
  `getPersonHasPrivatePost` (`session_auth`, admin) → `PersonPrivatePostOut`
  `{has_private_post: bool = False}`; 404 `person_not_found`.
- Slice C: `PUT /workspaces/{workspace_id}/persons/{person_id}/manager`
  `setPersonManager` (`session_auth`, admin): body `user_id: UUID | None = None`,
  `confirmed: bool = False`, `preview_token: str = ""`; `previewVisibilityChange` gains
  `member_change` kind `manage`.

Changed behaviour of existing endpoints (no schema change): `acceptInvitation` clears
`deactivated_at`/`former_user`; `removeMember`/`leaveWorkspace` set them; `createPerson`
records `person_created`; archiving through `updatePerson` revokes the person's open
invitation (`invitation_revoked`, reason `person_archived`). `listPersons`,
`listMembers`, `linkPersonToMember` stay as they are for old clients; an unlink through
`linkPersonToMember` now also gives the member a fresh person (M8).

Contract: the new operations appear in `openapi.json`; `test_openapi_contract` keeps
comparing with `openapi_1b.json` and `openapi_1c.json` (nothing removed or made required).

## 9. Web

- **Nav:** one item **"Mensen"** (icon `people`) replaces "Personen" and "Leden", in the
  main nav (not under Instellingen). "Afdelingen" (branch 5) stays its own item while
  departments are on. `/w/$id/personen` and `/w/$id/leden` redirect to `/w/$id/mensen`;
  `/w/$id/leden/$userId/apparaten` stays and is linked from the detail.
- **List `/w/$id/mensen`:** status filter (Alle · Actief · Uitgenodigd · Niet actief ·
  Uitgeschakeld · Gearchiveerd), search by name. Columns: Naam, Status, Rol, Verdeelt
  post, Afdelingen, Laatst ingelogd, Brieven. Members see Naam, Status (active or not),
  Afdelingen, Brieven and "Persoon toevoegen".
- **Row and detail actions (admins):**
  - niet actief: **Uitnodigen**; uitgeschakeld: **Opnieuw uitnodigen** (address
    prefilled from `email`); both open the invite dialog with the person fixed.
  - uitgenodigd: **Opnieuw versturen**, **Intrekken**; "verlopen" shown when expired.
  - actief: Rol wijzigen, Verdeelt post, Afdelingen, Apparaten, Herstellink (where
    `can_manage_account`), **Uitschakelen** (with the private-post sentence of 4).
  - "Opnieuw versturen" works on an expired invitation too (`renewInvitation` takes any
    invitation not accepted or revoked).
  - every person: Naam en schrijfwijzen, Archiveren; slice C: Beheerd door.
- **Detail `/w/$id/mensen/$personId`:** the same facts and actions, the person's letters
  (link to Alle post filtered on the person), "Wie kan de post van <naam> zien?" (the
  reasons of `getMyPostAccess` are per member; for an inactive person the sentence is
  the table of 4 for the workspace's switches; plan).
- The invite dialog (`components/invite-dialog.tsx`) takes a fixed `personId` and hides
  its person picker then; a general "Iemand uitnodigen" stays on Mensen (it creates the
  person at acceptance, as today).
- All strings Dutch B1 with English (`pnpm extract`), terms in GLOSSARY.

**Build after branch 5.** `routes/members.tsx`, `routes/persons.tsx`,
`routes/departments.tsx`, `components/page.tsx`, `components/person-picker.tsx`,
`components/invite-dialog.tsx`, `components/role-dialog.tsx`, `routes/inbox.tsx`,
`router.tsx` and both message catalogues are edited on `feat/wie-ziet-wat-5`. The web
tasks start after it merges. The letter-status plan edits `routes/persons.tsx` and
`routes/inbox.tsx` too: whichever lands second moves its person-status block into the
Mensen detail.

## 10. Effects elsewhere

- **Scan folders (intake design, c45b285).** Its rule "a subfolder for every person with
  an account and for every department while on; other persons only when an admin adds
  one (`added_by_admin`)" reads under this model: see Besluit B5.
- **"Wie kan mijn post zien?" / `viewer_reasons`:** unchanged in A/B; slice C adds
  "beheert de post van <naam>" and the matching policy sentence.
- **Mijn taken:** a task on an inactive person's letter goes to whoever sees the letter
  (unchanged); slice C's manager becomes a possible assignee through `regular_viewers`.
- **iOS:** no required change (section 11).

## 11. iOS (`Brievie_ios`)

- Nothing breaks: `listPersons` and `createPerson` are unchanged; "Nieuwe persoon" in the
  review picker already exists and now creates an inactive account (same call).
- Optional: show "geen account" next to a person in the picker (would need
  `listPersons.user_id`, already present). Pairing is per account and unchanged: only an
  active account can pair.
- Mensen is not built in the app (administering people is web-only, `session_auth`).

## 12. Rulings (made on the owner's behalf)

| # | Ruling | Why |
|---|---|---|
| M1 | The account slot is the Person row; a `User` exists only from activation | no change of sight; security by construction (3.1) |
| M2 | Departments keep `kind=department`, shown outside Mensen | no re-keying, no policy migration (3.2) |
| M3 | Activation only through the invitation flow | one audited, confirmed path |
| M4 | `former_user` is display-only | it may never grant anything |
| M5 | Account columns for admins only | e-mail and sign-in times are account facts |
| M6 | Activation ends "Beheerd door" (slice C) | an adult with an account decides |
| M7 | Nothing automatic creates a person; the autopilot never does | Stijn: a human adds the person |
| M8 | Every member always has exactly one person (kept, not only migrated) | Mensen is one complete list |

## 13. Not in this design

- Merging two persons ("A. de Vries" into "Anne"): moves addressee links through
  `organise` with a preview; later, hooks into the Mensen detail.
- E-mailing invitations (needs SMTP; DECISIONS has none).
- Delegation for active accounts ("meekijken" in full, S 9).
- A `Department` table (M2).

## 14. Besluiten voor Stijn

Alleen vragen waarvan het antwoord verandert wat er gebouwd wordt.

- **B1. Een niet-actief account is een persoon zonder inlog (geen echte gebruiker).**
  Alternatief: voor iedereen meteen een echte gebruiker zonder wachtwoord. Dat verandert
  wie welke post ziet (de post van niet-actieve mensen gaat dan niet meer naar wie post
  verdeelt) en raakt elke rechtencontrole. *Advies:* persoon zonder inlog (M1). In het
  scherm is het verschil onzichtbaar: iedereen staat onder Mensen met een status.
- **B2. "Beheerd door" nu bouwen (slice C) of later?** Zonder dit kan na uitschakelen
  niemand de privébrieven van die persoon lezen, en thuis staat noodtoegang uit. Kost:
  een wijziging in de databaseregels voor zicht, een nieuwe meting op 100.000 brieven en
  een bevestigingsstap. *Advies:* nu, als laatste deel C, na A en B (die los kunnen
  uitkomen); alleen voor mensen zonder actief account.
- **B3. Mag de AI een nieuwe naam voorstellen** ("Deze brief is voor M. Jansen; die ken
  ik nog niet")? Vraagt een nieuw veld in de AI-uitvoer en een nieuw soort voorstel.
  *Advies:* later; eerst "Nieuwe persoon" in de naamkiezer (deel B). De autopilot maakt
  hoe dan ook nooit iemand aan (M7).
- **B4. Zien gewone leden onder Mensen ook status en laatst ingelogd?** *Advies:* leden
  zien naam, of iemand actief is en afdelingen; e-mail, laatst ingelogd en uitnodigingen
  alleen beheerders (M5).
- **B5. Scanmap per persoon (inname-ontwerp):** krijgt straks *iedere* persoon
  automatisch een submap, ook niet-actieve, of alleen actieve en uitgenodigde?
  *Advies:* iedere niet-gearchiveerde persoon, ongeacht status: wie post kan ontvangen
  heeft een account, dus ook een map; post in de map van een niet-actief iemand gaat
  naar wie post verdeelt (sectie 4). `added_by_admin` blijft alleen voor het terugzetten
  van een gestopte map. Let op: leden kunnen personen aanmaken, dus elke nieuwe persoon
  geeft een map op de NAS.
