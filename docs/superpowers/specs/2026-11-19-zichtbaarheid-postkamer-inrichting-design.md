# Who sees what, the mailroom, the first start and folders per person: design

Date: 2026-11-19, revised the same day. Covers `Brievie_host` (backend, `web/`,
`deploy/`) and `Brievie_ios`. Design only; nothing here is built.

**This revision.** The owner found the first version (commit `9d71093`) too much and
asked for: one switch "iedereen ziet alles" for home use, departments for organisations,
no per-user rights to learn, a single Thuis-or-Organisatie choice at installation instead
of a seven-step wizard, and a look at directories such as Microsoft 365 and Keycloak. He
delegated the choices. Research behind this revision:
`.superpowers/onderzoek/research_notes/Rechten en afdelingen/rechten_en_afdelingen.md`
(cited "RA", with its lesson numbers), on top of the ticket-system notes (cited "T") and
the competitor report. Section 4.20 lists what was removed, kept and moved to later;
section 11 lists the withdrawn rulings. A plain-Dutch summary for the owner is
`2026-11-19-rechten-in-gewone-taal.md`.

**Inputs.** The owner's notes of 2026-11-19 (`ontwerp-zichtbaarheid-en-rechten`,
`ontwerp-postkamer-gesloten-envelop`, `ontwerp-inrichting-en-mappen`,
`veiligheidsregels-wiscode`), his wishes of the same evening (simplify; departments;
directories; workspaces; an easy home/organisation switch; one choice at installation),
the competitor report (`.superpowers/onderzoek/reports/Concurrenten van Brievie.md`).

**Neighbours.** `2026-11-17-fase-1d-leden-rollen-sso-design.md` (cited "D"): part A is
built and merged; part B (private post, departments) is planned in
`docs/superpowers/plans/2026-11-17-host-1d-b-prive-afdelingen.md` (cited "PB") but not
built; part C (SSO, second factor) is extended by section 6 here, which says exactly
what it adds. `2026-11-17-beheer-backup-export-updates-design.md` (cited "Beheer"): its
safety rules (4.10) bind every part here that deletes; its export (5) and archive folder
(6.3) are extended by part D, not duplicated. `2026-11-19-herstel-met-wachtwoord-design.md`
on branch `docs/recovery-password-design` (cited "Herstel"): its recovery mode is a
preset value here (4.2), and its "wizard step 6" moves to the back-up card (4.2).

**What this document does to D part B and PB.** Part A below *is* D part B, widened in
one direction (who sees non-private post) and otherwise left as PB plans it:

| D / PB section | Change |
|---|---|
| D 8.1 "a non-private letter is visible to everyone" | visible by the workspace's switches (4.1): everyone, or own post, departments and sorting |
| D 8.1 readers | unchanged for private letters; a share (`DocumentShare`) also works on a non-private letter (R44) |
| D 8.2 departments | unchanged model (a department is a `Person` of kind `department`); adds leads, labels, a switch to turn them off (4.5, 4.6) |
| D 8.3 document policy | gains the arms of 4.9; "an empty actor sees public letters" becomes "sees no letter" (R6) |
| D 8.3 "a manual rule run is the system" | runs as the member who started it (R7) |
| D 8.5 emergency access | also opens non-private post of the person that the admin cannot see (4.7) |
| D 6.2 invitations | a second form: one QR code that creates the account *and* pairs the phone (4.19) |
| D 9 SSO | section 6 adds groups-to-departments, a test sign-in and a shorter device re-confirm |
| D 13 row B | replaced by part A of this document |
| PB tasks 2, 3, 4, 5, 7, 11, 12, 13, 14, 15, 18, 20, 24 | amended as listed in 4.18 |
| Beheer 5 "server-wide export as the system" | only on the server's command line (4.10, R8) |
| Beheer 6.3 "no setting to write as another user" | replaced by part D's archiver under `BRIEVIE_UID`/`BRIEVIE_GID` |

## 1. Intent

Brievie makes it clear, for every letter, **who may see it**, with as few concepts as
possible. At home it works without a thought: everyone sees all post except private
post. An organisation turns on departments: you see post on your own name and post of
your departments, not your colleagues' post; post without a name goes to the people who
sort it, not to everyone. Both are **presets of the same handful of switches**, chosen
once at installation and changeable later, safely, in both directions. An organisation
that already has Microsoft 365 or Keycloak can let its directory groups decide who is in
which department. An opt-in mailroom registers closed envelopes.

## 2. Goals, non-goals, anti-goals

**Goals**

- G1. One choice at installation, Thuis or Organisatie, that only sets switches (4.2).
- G2. A page "Wie ziet wat" with four switches and one list of people, each explained in
  one sentence about real people; every change previewed, confirmed when it widens
  access, logged, and told to whoever it affects (4.3, 4.4).
- G3. Departments: a letter belongs to persons and/or departments; members of a
  department see its post; unassigned post goes to the sorters (4.5).
- G4. Labels that belong to a department, next to labels for everyone (4.6).
- G5. A private mark per letter (D) that holds under every setting.
- G6. "Wie kan mijn post zien?" for every member, in plain sentences.
- G7. All of it enforced by PostgreSQL row-level security, so every path obeys one rule.
- G8. Directory groups (Entra ID, Keycloak, Authentik) mapped to departments, tested
  without owning a directory (section 6).
- G9. Invite *and* pair a phone for a person with one QR code (4.19).
- G10. The opt-in mailroom (part B) and folders per person (part D), as designed before.

**Non-goals**

- Rights per member ("scopes"), delegation levels, folder-based rights, rights per label
  or per sender: removed or later (4.20).
- Nested departments; roles per department.
- LDAP, SCIM, back-channel logout (section 6.9: later).
- Several servers in the iOS app (section 5.3: out of scope, sized).
- Mail sending (SMTP). Legal advice on what an employer may open.

**Anti-goals** (failure even if it works)

- A letter that a member may not see reaching them through any path in 4.10.
- A state in which a letter without a name is seen by nobody.
- A switch that deletes something, or that cannot be switched back.
- A widening of access that the people it concerns cannot see.
- A home user who must understand departments, sorting or directories to add a partner.
- Envelope data (sender, markers) in a notification, a push payload, the audit log, or on
  a lock screen; a "Persoonlijk" envelope that any setting lets the mailroom open.
- A delete in someone's home folder of a file Brievie did not write.
- A restore that silently brings back rights that were taken away.

## 3. Facts from the code that shape this

- `workspace_context(workspace_id)` sets one transaction-local setting,
  `brievie.workspace_id`; every workspace table has one permissive policy
  `workspace_isolation` and forced RLS (`apps/workspaces/rls.py`). The actor setting of
  D 8.3 does not exist yet (PB tasks 1–2).
- `Workspace` (not under RLS) has `name`, `kind` (`home` "Thuis" | `business`
  "Zakelijk"), `modules` (read by nothing), `allow_member_export`. `kind` controls today:
  `allow_member_export` at creation (`workspaces/services.py`), the label start set
  (`labels/presets.py`, 10 home labels, 9 business labels), the preset rules
  (`rules/presets.py`), the hidden "Rol" column at home (`web/src/routes/members.tsx`),
  and when the web offers "Startset toevoegen" (`labels.tsx`). Nothing else.
- `Membership` (not under RLS): `role` `admin` | `member` | `viewer`; no other field.
  `Person` has `display_name` (unique per workspace), `name_variants`, `user` (one person
  per account per workspace), `archived_at`; no `kind` yet; no `Department` model.
  `Label`: `name` (unique per workspace), `color`, `system_key`; no owner.
- Rules: conditions on `text`, `sender`, `document_type`, `label` (`has`/`has_not`),
  `addressee` (`is`/`is_empty`), `source`; actions `add_label`, `add_addressee`; modes
  `propose` and `auto`. So "label X → department Y" is already expressible.
- `organise.py` is the only writer of addressees (max 10 per letter) and labels.
  `inbox_state()` derives `new`, `review`, `unassigned` (no addressee), `""`. The web
  calls the unassigned tab "Op niemands naam"; the iOS branch `feat/clear-names` (not
  merged) introduces "Naam kiezen".
- `setupServer` asks `setup_token`, `email`, `password`, `name`, `workspace_name`,
  `workspace_kind` (required) and `extra_labels`; the web's `/setup` shows the cards
  "Thuis" and "Zakelijk" and, for Zakelijk, a field "Afdelingen" that becomes labels.
  There is no page after it.
- `Document.scanned_by` is a nullable foreign key; `status` is `processing`,
  `needs_review`, `done`, `failed`.
- The iOS app pairs with **one server**: `ConnectionStore.save` "replaces any existing
  pairing", the Keychain holds two fixed keys (`deviceToken`, `serverConnection`). It
  already switches between **workspaces on that server** (`CurrentWorkspaceStore`, the
  list in `SettingsView`); every queued upload carries `serverID` and `workspaceID`;
  calendar settings are per workspace.
- Audit codes for visibility, departments and emergency access exist (`AuditCode`);
  notice codes for emergency access exist (`AccountNotice.Code`).
- The pairing QR code is `{"v":1,…}`; the iOS app refuses another `v`.

---

## 4. Part A: who sees what

**How to build it.** Take PB as the plan and amend it as 4.18 lists; everything PB says
that this section does not change holds (its PostgreSQL facts, lock order, single
writer, grant rules, emergency mechanics, contract fields). Build in the order of
section 13.

### 4.1 The model

Four workspace settings and one flag per member decide who sees a non-private letter.
Nothing is set per member except whether they sort post.

| Setting (field) | Dutch on screen | Meaning |
|---|---|---|
| `Workspace.everyone_sees_all` | Iedereen ziet alle post | every member sees every letter that is not private |
| `Workspace.departments_enabled` | Afdelingen gebruiken (Thuis: Groepen gebruiken) | letters can be on a department's name; its members see them |
| `Workspace.admins_read_all` | Beheerders lezen alle post | admins see every letter that is not private |
| `Membership.sorts_post` | Verdeelt post | this member sees letters that nobody else reads, and gives them a name |
| `Workspace.kind` | Thuis / Organisatie | the preset; changes no right by itself (4.3) |

A member **sees** a letter that is not private when at least one of these holds:

1. "Iedereen ziet alle post" is on; or the member is an admin and "Beheerders lezen alle
   post" is on;
2. the letter is on the name of a person linked to the member (their own post);
3. departments are on and the letter is on the name of a department the member is in;
4. the member **sorts post** and nobody reads the letter through 2 or 3 (it has no
   person with an account and no department with members, counting departments only
   while they are on). When no member sorts post, the admins sort it;
5. the member scanned it and it is not yet done (4.8);
6. it was shared with the member, or the member opened it with emergency access (4.7).

A **private** letter (`Document.visibility = restricted`, D 8.1) is seen only by its
readers (addressees with an account, members of its departments, shares, emergency),
whatever the switches say. "Iedereen" and "Beheerders lezen alle post" never include
private post (D decision 1, R3).

**Positions.** There are two, and no third (R32):

- **Iedereen ziet alles** (Thuis default): switch 1 on. Departments may be on as well,
  for sorting and filters; they then hide nothing.
- **Eigen post** (Organisatie default): switch 1 off. A member sees their own letters,
  those of their departments, and, if they sort post, the unassigned ones; not other
  users' or other departments' letters. With departments off this is simply "ieder ziet
  zijn eigen post".

The earlier third scope, "own and shared", is covered without a new concept: a household
that turns "Iedereen ziet alle post" off (its own choice; the Thuis preset leaves it on)
keeps the parents as admins with "Beheerders lezen alle post" on, so a letter for "Fam.
de Vries" (no person) reaches them through sorting and admin sight, and not the children.

**Every combination is a valid resting state.** Rule 4 guarantees that a letter nobody
reads through a name always reaches the sorters, and the sorters are never empty because
a workspace always has an admin (D 6.1 `last_admin`).

### 4.2 Installation: one choice, a preset

**What is asked.** The `/setup` form keeps the installation code, the name, email and
password of the first account and the name of the workspace (labelled "Naam, bijvoorbeeld
'Familie de Vries' of 'Bakkerij Jansen'"). Above them, the one choice, as two cards with
one sentence each:

- **Thuis** — "Voor jezelf en je gezin. Iedereen ziet alle post, behalve wat privé is."
- **Organisatie** — "Voor een bedrijf, praktijk of vereniging. Ieder ziet zijn eigen
  post en die van zijn afdeling."

Nothing else is asked. The field "Afdelingen" disappears from the form (departments are
made on their own page; the API keeps accepting `extra_labels` for old clients, R40). The
code value stays `business`; every screen says "Organisatie" instead of "Zakelijk".

**The presets** (applied by `create_workspace` through one service,
`apps.workspaces.presets.apply_preset_settings(workspace)`):

| Value | Thuis | Organisatie | Changed later at |
|---|---|---|---|
| Iedereen ziet alle post | on | off | Instellingen → Wie ziet wat |
| Afdelingen gebruiken | off | on | Instellingen → Wie ziet wat |
| Beheerders lezen alle post | on | off | Instellingen → Wie ziet wat |
| Who sorts post without a name | the admins (nobody flagged) | the admins until someone is flagged | Wie ziet wat → Post zonder naam, or the "Verdeelt post" column on Leden (both only while "Iedereen" is off) |
| Zorg is privé (`care_is_private`, PB 15) | on | off | Instellingen → Privé |
| Noodtoegang (`emergency_access`, D 8.5) | off | on | the server admin only, through PB's `setEmergencyAccess` (its page in PB task 24); not in a workspace admin's preset dialog, which shows "De serverbeheerder zet noodtoegang aan of uit." |
| Label start set and preset rules | 10 home labels and their rules | 9 organisation labels and their rules | one action, `applyLabelPreset` ("Startset toevoegen" on Labels and Automatisering); never removes anything |
| Words that follow the kind | "Groepen", no Rol column on Leden | "Afdelingen", Rol column shown | Wie ziet wat → Soort gebruik |
| Leden mogen eigen post exporteren | on | off | Instellingen |
| AI | off | off | AI (with its consent flow) |
| Postkamer (part B) | off | off | Instellingen → Postkamer, when built |
| Herstelmodus (Herstel) | suggested: own password | suggested: recovery sheet | the back-up set-up; Instellingen → Back-up |

The recovery mode is not set by the preset: Herstel lets the server admin choose it at
back-up set-up (`./brievie backup init`, or `BRIEVIE_RECOVERY_MODE`); the preset only
decides which option is offered first. Herstel's section 8 places the web choice in
"wizard step 6"; with no wizard, that choice lives on the back-up card under Instellingen
and the summary below links to it.

**After setup: "Zo is Brievie nu ingesteld".** `/setup` navigates to
`/w/<id>/ingesteld`, a page of sentences from `policy_text.describe()` with links, not
steps. Thuis example:

> Iedereen in Brievie ziet alle post, behalve post die privé is. Zorgpost is privé.
> Je bent beheerder. Brieven worden niet door AI gelezen. Er is nog geen back-up.
> → Iemand uitnodigen · Personen toevoegen · Back-up instellen · Wie ziet wat wijzigen

Organisatie example:

> Ieder ziet zijn eigen post en die van zijn afdelingen. Er zijn nog geen afdelingen.
> Post zonder naam zie jij, tot je iemand aanwijst die post verdeelt. Beheerders lezen
> geen post die niet voor hen is. Noodtoegang staat aan. Brieven worden niet door AI
> gelezen. Er is nog geen back-up.
> → Afdelingen maken · Mensen uitnodigen · Iemand post laten verdelen · Back-up instellen

The same sentences head the "Wie ziet wat" page.

**The installer** (`./brievie`, `deploy/init-env.sh`) asks only technique (address,
ports, data folder, `BRIEVIE_UID`/`GID`, the archive folder of part D). The preset is
**not** an environment variable (R40): an unattended installation does not create the
first account either, and the choice belongs with that account in the web. A second
workspace, or an unattended one for a provider, is made on the server with
`manage.py create_workspace --name … --kind home|business --admin-email …` (5.2), which
applies the same preset.

### 4.3 The page "Wie ziet wat"

Under Instellingen, admins only (members see "Wie kan mijn post zien?", 4.15). From
top to bottom:

1. **Soort gebruik: Thuis / Organisatie.** "Dit bepaalt welke standaard Brievie
   voorstelt. Het verandert zelf niemands rechten." Switching it sets `kind` (the Rol
   column, the offered label start set and rule set follow, as today) and opens the
   preview of 4.4 with every value of the other preset that differs, each with a
   checkbox, all checked (Noodtoegang excepted: a line says the server admin sets it);
   widening ones need the confirmation of 4.4. Labels and rules are
   never removed; "Ook de startset labels toevoegen" is one more checkbox (idempotent,
   existing `applyLabelPreset`).
2. **Iedereen ziet alle post** (switch). "Aan: iedereen in Brievie ziet elke brief,
   behalve brieven die privé zijn. Uit: je ziet alleen post op je eigen naam en van je
   afdelingen."
3. **Afdelingen gebruiken** (switch; Thuis: "Groepen gebruiken"). "Zet post op naam van
   een afdeling, zoals Finance. Wie in die afdeling zit, ziet die post." With "Iedereen
   ziet alle post" on, the sentence adds: "Nu zien nog steeds alle mensen alle post;
   afdelingen helpen dan alleen bij het sorteren."
4. **Beheerders lezen alle post** (switch; hidden while switch 2 is on). "Aan: Stijn en
   Anne zien alle post die niet privé is. Uit: zij regelen wie wat ziet, maar lezen
   alleen post die voor hen is."
5. **Post zonder naam** (a list of members with "Verdeelt post", and "Iemand toevoegen";
   hidden while switch 2 is on). "Wie hier staat, ziet brieven waar nog geen naam of
   afdeling op staat, en zet er een naam op. Staat hier niemand, dan doen de beheerders
   dat: Stijn en Anne."

Links below: "Privé" (Zorg is privé, Noodtoegang), "Afdelingen", "Wie kan mijn post
zien?". No other right is set anywhere.

### 4.4 Changing a switch safely

The switches, the kind and the sorting flag change through one service,
`apps.workspaces.visibility_settings.change(workspace, proposal, *, actor, confirmed,
preview_token)`, in one transaction that first takes `lock_memberships(workspace)` (D
part A). Roles stay with `apps.workspaces.members`, department memberships with
`people.services` (PB task 11), person links with `link_member` (PB task 12): each of
those writers, after its own `lock_memberships`, calls the same three helpers
(`visibility_rules.diff`, `require_confirmation`, `notify_changes`), so every change that
moves sight follows these steps:

1. **Preview.** A pure function `visibility_rules.diff(before, after, members,
   departments) -> list[Change]` computes, per member, what they gain and lose in
   categories ("post van anderen", "post van Finance", "post zonder naam", "alle post als
   beheerder"), plus one count: letters whose set of viewers changes (one SQL count as
   the system). The dialog says: "Dit verandert voor 12 mensen wat ze zien." with two
   lists, "Gaan meer zien" and "Gaan minder zien", one line per person.
2. **Confirmation when anyone gains.** A change in which anybody gains sight needs
   `confirmed = true` (otherwise 409 `confirmation_required`); the dialog's button says
   what happens ("Ja, iedereen mag alle post zien"). A narrowing needs only the preview.
   The preview answers a `preview_token` (a hash of the settings, roles, flags, links and
   department memberships it was computed from); a confirmed request sends it back, and
   a token that no longer matches answers 409 `preview_outdated` ("Er is intussen iets
   veranderd. Bekijk de wijziging opnieuw."), so a confirmation never covers another
   admin's change made in between. A directory sign-in (section 6.3) needs no
   confirmation (the mapping was confirmed when it was made) but sends the notices.
3. **Audit.** One `workspace_setting_changed` row per changed setting (values `setting`,
   `value`), `workspace_preset_applied` (value `preset`) for a preset switch,
   `post_sorter_changed`, `department_member_added`/`_removed`, `member_role_changed` as
   they exist.
4. **Notices** (content-free, 4.14): to every member who gains ("Je kunt nu alle post
   zien die niet privé is. Dat heeft Erik op 3 december ingesteld.") and every member who
   loses ("Je ziet de post van Finance niet meer."). When admins gain sight of all post
   ("Beheerders lezen alle post" on, or a new admin while it is on), **every member** is
   told, because it is about everybody's post (R37).
5. **Nothing is deleted.** Departments, their members, leads, labels, mappings and rules
   stay stored whatever is switched; switching back restores exactly the earlier sight.

**While departments are off** (R35):

- department pages, the department filter and department labels' ownership are hidden;
  department labels behave as labels for everyone;
- a letter on a department's name keeps that link; for sight, the department counts as
  nobody, so a letter **only** on departments goes to the sorters (rule 4), and a letter
  also on a person goes to that person;
- a rule that adds a department keeps working and keeps writing the link, so nothing is
  lost when departments come back; the rule shows "Afdelingen staan uit: deze post gaat
  nu naar wie post verdeelt.";
- private letters are untouched: their readers (including department readers) stay as
  they are. Private stays private under every setting, and no switch makes a private
  letter readable by anyone new.

**No lock-out.** Admins manage whatever the switches say; a switch cannot hide the page
from them. The last admin cannot be removed or demoted (D). The sorters are never empty
(rule 4). No combination hides a letter from everybody (tested, 4.17).

**Transitions** (E = everyone sees all, D = departments, A = admins read all, S = at
least one member flagged as sorter). "Gains"/"loses" is about non-private post.

| Change | Gains | Loses | Confirm | Notice to |
|---|---|---|---|---|
| E off → on | everyone who did not see all | nobody | yes | every member |
| E on → off | nobody | every member except admins when A is on: others' post | no | those who lose |
| D off → on, E off | department members: their departments' post | sorters: letters only on staffed departments | yes | those who gain or lose |
| D on → off, E off | sorters: letters only on departments | department members: departments' post (unless also on their own name) | yes | those who gain or lose |
| D either way, E on | nobody | nobody | no | nobody (audit only) |
| A off → on, E off | admins: all post | nobody | yes | every member |
| A on → off, E off | nobody | admins: others' post | no | the admins |
| member made admin, A on, E off | that member | nobody | yes | every member |
| admin made member, A on, E off | nobody | that member | no | that member |
| sorter added, E off | that member: unassigned post (admins lose it when they were the fallback) | admins, if they were the fallback and do not see it otherwise | yes | that member; the admins |
| last sorter removed, E off | admins (fallback) | that member | yes when an admin gains | that member; the admins |
| member added to a department, D on, E off | that member | nobody | yes (the add dialog) | that member; the department's members when the admin added themselves |
| member removed from a department | nobody | that member | no | that member |
| person linked to a member (PB task 12, also by accepting an invitation for a person), E off | that member: the person's post | sorters: that person's post, if nobody read it before | yes (the link dialog; an accepted invitation was confirmed when it was made) | that member; the sorters; the other admins when an admin linked themselves |
| person unlinked, E off | sorters: the person's post, if nobody else reads it | that member | no | that member; the sorters |
| member made admin or admin made member, A off, nobody flagged | the new admin: unassigned post (fallback) / nobody | nobody / the former admin | yes / no | that member |
| sorter made viewer | admins, if they become the fallback | that member (`sorts_post` is cleared in the same transaction) | yes when an admin gains | that member; the admins |
| one of several sorters removed | nobody | that member | no | that member |
| member removed from the workspace (D 6.3) | as "person unlinked" and "removed from a department", for each | that member | no | the sorters who gain |
| directory sign-in changes departments or role (6.3) | as the rows above | as the rows above | never (6.2) | the user; as the rows above |
| kind switched without taking values | nobody | nobody | no | nobody (audit only) |
| preset applied | the union of its rows | the union | if any row needs it | as the rows say |

The table is the specification of a table-driven test (4.17).

### 4.5 Departments

A department is a `Person` with `kind = department` (D 8.2, PB task 11): a name, name
variants ("Afd. Finance", "Crediteuren", "t.a.v. de administratie"), members
(`DepartmentMember`), and **optional leads** (`DepartmentMember.is_lead`). Leads manage
the department's labels and receive the mailroom's escalations (part B); they see nothing
extra. Members and leads are set by admins (or by the directory, section 6).

**A letter belongs to persons and/or departments**, through the existing addressee links
(at most 10). A letter may be on **several departments** (R36): "t.a.v. Finance en HR"
exists, and the model already allows several addressees; forcing one would be new code
and a new constraint. Reports count a letter in each of its departments.

**How a letter gets its department:**

- by hand: "Toewijzen" in Te doen and on the letter (the person picker shows departments
  first while departments are on);
- by name matching: the 1c matcher already proposes persons and, through PB task 11,
  departments by name and variants;
- by rule: `add_addressee` with a department; conditions on sender, words, document type
  or **label** ("als label Facturen, dan afdeling Finance" — no engine change);
- by suggestion (`DocumentSuggestion`, as today; a dismissed one is not repeated).

**Unassigned post** (no addressee anybody reads through) goes to the sorters, never to
everyone (RA lesson 2). Te doen shows it to them as a queue "Zonder naam".

**Wording in Te doen** (the row's status and the tab): with departments off, the status
of an unassigned letter is **"Naam kiezen"** (the word of `feat/clear-names`, also for the
web, which now says "Op niemands naam"); with departments on, **"Toewijzen"**, because the
choice includes departments. Te doen gains filter chips for a department member: "Voor
mij", one chip per department, and for sorters "Zonder naam".

**Moving a letter** to another department hides it from the previous department (the
Freshdesk behaviour, RA 2). The picker says it before the tap: "Na toewijzen ziet HR deze
brief niet meer. Hij gaat naar Finance."

**Deleting and archiving.** A department with letters cannot be deleted (409
`person_in_use`, as for persons); it can be archived (`archived_at`): no new letters are
assigned to it and its matcher stops proposing it; its members keep seeing its letters.
Deleting an empty department removes its memberships and mappings; its labels become
labels for everyone (`on_delete=SET_NULL`).

**Thuis.** The same object is called "Groep" in a Thuis workspace (R51), for example
"Ouders"; the switch is off by default and nothing about it shows until it is on.

### 4.6 Labels under a department

`Label.department` (nullable foreign key to a `Person` of kind department, `SET_NULL`)
is the whole change (R42):

- **Where it shows.** A department label is offered in pickers, filters and the label
  list to members of that department and to admins; everyone else does not get it
  offered. On a letter they can see, they see all its labels. Label names are not secret
  (a directory, like person names, D 8.3); the label page says "Noem een label niet naar
  een persoon of een gevoelig onderwerp."
- **Who manages it.** Admins, and leads of that department. Members create labels for
  everyone, as today.
- **Labels never decide who sees a letter.** A label is applied to a letter one can see;
  it grants nothing (RA lesson 5). To route by label, a rule adds the department.
- **Names stay unique per workspace** (the existing constraint): a member of two
  departments never sees two labels with one name. The page suggests a prefix
  ("Finance: Facturen") when a name exists.
- **A rule may** add any label (rules are admin-made and workspace-wide); its effect is
  limited to letters the rule reaches (system for automatic rules, the starter for a
  manual run, R7).
- **Reporting.** Counts per label are SQL counts under the policies, so each member
  counts only what they see.
- **Migration.** One nullable column; no data change.

### 4.7 Admins, roles and break-glass

| Role (Dutch) | Code | Notes |
|---|---|---|
| Serverbeheerder | `User.is_server_admin` | as D 6.1; inside a workspace only its membership's rights |
| Beheerder | `admin` | manages members, departments, labels, rules, switches; reads post by 4.1 |
| Lid | `member` | scans, reviews, organises what it sees |
| Alleen lezen | `viewer` | reads and notes what it sees (R29) |
| Verdeelt post | `Membership.sorts_post` | a flag, any role except viewer |
| Postkamer | `Membership.mail_desk` | part B; envelopes only |

- **At home** an admin reads everything that is not private ("Beheerders lezen alle
  post" on, and "Iedereen" on anyway).
- **In an organisation** the default stays "manages but does not read", in the sentence a
  user sees: "De beheerder regelt wie wat ziet, maar leest alleen post die voor hem is."
  An admin is also the fallback sorter while nobody else sorts (said on the page).
- **Self-grants are visible.** An admin who adds themselves to a department, links
  themselves to a person, or flags themselves as sorter may do so (small offices), but
  the department's members, or the other admins, get a notice; adding or linking oneself
  where there is private post stays refused (409 `cannot_grant_self`, PB tasks 11–12).
- **Break-glass** (`emergency_access`, D 8.5 with the earlier extension): a server-admin
  switch, a reason of 10–200 characters, one hour, read-only, an audit row, a history
  line on each letter, a notice to the person or the department's members. It opens
  **all letters on the person's or department's name that the admin cannot see**,
  private or not, because in an organisation the admin does not read non-private post
  either. Organisatie on, Thuis off.

### 4.8 Private letters, shares and the scanner

- **Private** (D 8.1, PB tasks 7, 17–20): unchanged. Who may make private or public, "a
  private letter always has a reader", "nobody gives themselves access", "automatic
  actions do not widen the readers", "Zorg is privé", the rule action "privé maken".
- **Shares** ("Delen met", `DocumentShare`) work on **every** letter, not only private
  ones (R44): in an organisation "laat Piet deze brief zien" is the everyday exception
  that replaces delegation. `recompute` writes `share` reader rows for shares of any
  letter; addressee and department reader rows stay for private letters only (PB).
- **The scanner** (R38): whoever scanned a letter sees it while its status is not `done`
  (rule 5), so they can review it; there is no setting and no share row. After confirm
  the normal rules apply. The review says it before the tap when the scanner will lose
  it: "Na bevestigen kun je deze brief niet meer zien. Hij gaat naar Jan."
  `DocumentOut.can_read` (default `true`) answers it afterwards (4.13). An upload for a mail item
  (part B) sets no `scanned_by`.

### 4.9 Enforcement in the database

#### 4.9.1 Settings and helper functions

Two transaction-local settings, as PB tasks 1–2: `brievie.workspace_id` and
`brievie.actor` (a user id, `system`, or empty). No third setting: the switches, the
role, the sorting flag, the person links and department memberships are read by SQL
functions from their tables, so a change applies at the next statement and no code can
set a wrong value (R31). All are `LANGUAGE sql STABLE PARALLEL SAFE SET search_path = ''`,
`SECURITY INVOKER`, owned by `brievie_owner`, `EXECUTE` for `brievie_app`, and called as
`(SELECT public.…())` so they run once per statement. A non-member gets `false` or an
empty array. The bodies below are written for the actor; the built functions take the
user as a parameter (`brievie_sees_all_post(uid uuid)`, `brievie_my_post_ids(uid)`,
`brievie_actor_sorts_post(uid)`; `brievie_covered_post_ids()` needs none) and the policy
passes `(SELECT public.brievie_actor_id())`. That gives one answer to "can user U see
letter X?" for everything that asks it about **another** user (`assignee_cannot_read`,
`getDocumentAccess`, the preview's count, notices, the label offer):
`documents.visibility.viewers(document_ids)` runs the same expression with each member's
id, as the system. A test asserts that it equals the policy's answer for every actor in
the transition test; no second Python copy of the rule exists.

```sql
-- PB task 1: brievie_actor_id() and brievie_is_system(), unchanged.

-- Arm 1: everyone sees all, or the actor is an admin and admins read all.
CREATE FUNCTION public.brievie_sees_all_post() RETURNS boolean AS $$
  SELECT COALESCE((
    SELECT w.everyone_sees_all OR (w.admins_read_all AND m.role = 'admin')
    FROM public.workspaces_workspace w
    JOIN public.workspaces_membership m ON m.workspace_id = w.id
    WHERE w.id = NULLIF(pg_catalog.current_setting('brievie.workspace_id', true), '')::uuid
      AND m.user_id = public.brievie_actor_id()), false)
$$;

-- Arms 2 and 3: the persons linked to the actor, and the actor's departments while on.
CREATE FUNCTION public.brievie_my_post_ids() RETURNS uuid[] AS $$
  SELECT COALESCE(pg_catalog.array_agg(id), '{}') FROM (
    SELECT p.id FROM public.people_person p
     WHERE p.user_id = public.brievie_actor_id()
    UNION ALL
    SELECT dm.department_id FROM public.people_departmentmember dm
      JOIN public.workspaces_workspace w ON w.id = dm.workspace_id
     WHERE dm.user_id = public.brievie_actor_id() AND w.departments_enabled) s
$$;

-- Arm 4: addressees somebody reads through: persons with an account, and departments
-- with members while departments are on.
CREATE FUNCTION public.brievie_covered_post_ids() RETURNS uuid[] AS $$
  SELECT COALESCE(pg_catalog.array_agg(id), '{}') FROM (
    SELECT p.id FROM public.people_person p WHERE p.user_id IS NOT NULL
    UNION
    SELECT dm.department_id FROM public.people_departmentmember dm
      JOIN public.workspaces_workspace w ON w.id = dm.workspace_id
     WHERE w.departments_enabled) s
$$;

-- Arm 4: the actor sorts post: flagged, or an admin while nobody is flagged.
CREATE FUNCTION public.brievie_actor_sorts_post() RETURNS boolean AS $$
  SELECT COALESCE((
    SELECT m.sorts_post
        OR (m.role = 'admin' AND NOT EXISTS (
              SELECT 1 FROM public.workspaces_membership o
               WHERE o.workspace_id = m.workspace_id AND o.sorts_post))
    FROM public.workspaces_membership m
    WHERE m.workspace_id = NULLIF(pg_catalog.current_setting('brievie.workspace_id', true), '')::uuid
      AND m.user_id = public.brievie_actor_id()), false)
$$;
```

`people_person` and `people_departmentmember` carry only the workspace policy, and the
workspace and membership tables none, so there is no circle. The arrays are small (one
entry per person with an account and per staffed department).

#### 4.9.2 The derived column

`Document.addressee_ids` (`uuid[]`, default `'{}'`, GIN index) holds the ids of the
persons and departments the letter is on. It is written only by
`documents.visibility.sync_addressee_ids(document)`, **as the system**, called **once** by
the outermost service that changed addressees (organise's functions, confirm, undo, the
pipeline, a rule run), at its end, in the same transaction; `organise` itself stays the
only writer of the addressee rows. The nightly check (PB task 5) compares it with
`DocumentAddressee` and logs the number of differences. It replaces the earlier
"reader rows for every addressed letter" and `Document.audience` (R33): linking a person
or changing a department's members changes no document row, because the functions read
those tables live.

#### 4.9.3 Policies

The document's three restrictive policies (`FOR SELECT`, `FOR UPDATE`, `FOR DELETE`, no
`WITH CHECK`, PB task 3) get this condition, replacing PB's:

```sql
(SELECT public.brievie_is_system())
OR EXISTS (
  SELECT 1 FROM "documents_documentreader" r
  WHERE r.document_id = "documents_document".id
    AND r.user_id = (SELECT public.brievie_actor_id())
    AND (r.expires_at IS NULL OR r.expires_at > pg_catalog.now())
)
OR (
  visibility = 'workspace'
  AND (
    (SELECT public.brievie_sees_all_post())
    OR addressee_ids OPERATOR(pg_catalog.&&) (SELECT public.brievie_my_post_ids())
    OR (scanned_by_id = (SELECT public.brievie_actor_id()) AND status <> 'done')
    OR ((SELECT public.brievie_actor_sorts_post())
        AND NOT addressee_ids OPERATOR(pg_catalog.&&) (SELECT public.brievie_covered_post_ids()))
  )
)
```

- An empty actor, and a user who is not a member, see no document (R6).
- **Write order.** While `addressee_ids` and `status` are unchanged the letter stays
  visible to whoever is working on it, so every user-level write happens before they
  change. In `confirm_document` (PB task 18 amended) and every service that changes
  addressees:
  1. as the user, after the existing authorisation: the fields except `status`, labels,
     addressee rows (through `organise`), suggestion updates (`mark_accepted`, the
     dismissals in `_set_organisation`), tasks, history, and the rules at confirm (D 8.3:
     inside a request they run as the user);
  2. as the system: `sync_addressee_ids`, `recompute` (readers), and in confirm
     `status = done` and `confirmed_by`/`confirmed_at`;
  3. nothing else to that letter as the user; `can_read` is computed last, as the user.

  Without this order a member who confirms a letter for Jan, and does not read Jan's
  post, would hit 42501 on the update policy (the new row is checked with the same
  condition) or silently update 0 child rows. A repeated confirm (`_is_repeat`) is
  looked up as the system after the membership lock, so a user who handed the letter
  over gets the idempotent answer with `can_read: false`, not a 404.
  `apps/documents/visibility.py`, `review.py` and `apps/tasks/services.py` (below) join
  the closed list of `system_actor()` callers.
- **PB's `keep_own_access`** (409 `would_lose_access`, PB task 7) applies to private
  letters only; on a non-private letter losing sight is the hand-over and the answer is
  `can_read: false`.
- **Child tables**: PB's correlated `EXISTS (document)` policies, unchanged; the
  coverage test keeps every table with a document foreign key under them.
- **Tasks without a letter**: the task policy becomes

```sql
CASE WHEN document_id IS NULL THEN
  (SELECT public.brievie_is_system())
  OR (SELECT public.brievie_sees_all_post())
  OR assigned_to_id = (SELECT public.brievie_actor_id())
  OR created_by_id = (SELECT public.brievie_actor_id())
ELSE EXISTS (SELECT 1 FROM "documents_document" d WHERE d.id = "tasks_task".document_id)
END
```

  with the new nullable `Task.created_by` (R39). Existing letterless tasks have no maker:
  with "Iedereen" on (every existing workspace after migration) nothing changes.
  Reassigning a letterless task is allowed to its maker, its assignee and whoever sees
  all post; the service checks that, then writes the new assignee as the system (the
  update policy would otherwise refuse the new row with 42501 when the actor is neither
  maker nor new assignee).
- `DocumentReader`: PB's `reader_scope` policy. `Person`, `Label`, `Rule`,
  `Membership`, `DepartmentMember`: no change; names are a directory; counts on them are
  computed under the document policy.
- **Who may write** is checked in services: `documents.visibility.can_edit(document,
  user)` = the role is `member` or `admin`, the letter is visible to the user, and not only
  through an `emergency` row (read-only).

#### 4.9.4 Readers: reduced, not dropped

`DocumentReader` goes back to PB's scope (private letters: `addressee`, `department`,
`share`, `emergency`) plus `share` and `emergency` rows on non-private letters. The rows
cannot be dropped altogether: a private letter needs per-user readers that the people
tables cannot express (shares, the scanner, emergency with an expiry), and a policy that
joins through addressee links recurses (D 5.1 B). For non-private post the cheaper check
is the array overlap above. Expected size: private letters × readers plus shares, as PB
measured, not "every addressed letter × readers".

#### 4.9.5 Performance

PB task 4's measurement, extended: 100,000 letters (10 % private), 50 persons of whom 25
with an account, 5 departments of 5, each list of PB task 4 measured as a plain member,
a department member, a sorter and an admin, in the states E on, and E off with D on and
off. Pass: ≤ 250 ms median each, as PB. The policy is one OR over several arms, and
PostgreSQL uses a BitmapOr only when every arm can use an index, which the sorter arm
(`NOT … &&`) and the sees-all arm cannot: **a scan of the workspace's letters with the
condition as a filter is expected**. Each arm is cheap (an array overlap against a small
initplan array). The planned optimisation, part of A1 rather than a fallback: the list
services add an explicit filter for the member's arm (`addressee_ids && …`, using the GIN
index) when "Iedereen" is off and the member sorts nothing; the policy still decides.
PB's EXPLAIN check is adapted to that. PB's per-child-table fallback stays the last
resort, with the owner's yes.

### 4.10 Every path a letter can leak through

| Path | How it is closed |
|---|---|
| Post list, Te doen, "Alle post", counts, filters on person, department or label | document policy; counts are SQL counts under it |
| One letter by id | policy; a hidden letter answers like an unknown id (404 `document_not_found`, PB task 16) |
| Page images and files | `DocumentPage` child policy; the file endpoint loads the page row as the user first; no storage path in an answer |
| Search (not built) | must query `Document` or a table with a document foreign key (then the coverage test forces the child policies); no count of hidden matches; highlighting reads returned rows only |
| Tasks, "Vandaag", the widget | task policy (4.9.3); a task is assigned only to somebody who can read its letter (422 `assignee_cannot_read`), never to a viewer |
| Phone calendar | fed from `listTasks` as the user; the app removes an event whose task no longer comes back |
| ICS feed (not built) | runs as the feed token's user; tasks of private letters as "Taak" without description |
| Suggestions, history | child policies |
| Labels of a department | offered by service filter; label names are not secret (4.6) |
| Rule test on existing post, manual rule run | run as the user (R7); the report counts only what the user sees |
| Automatic rules, pipeline | system; never add an addressee to a private letter (D 8.1) |
| The preview of 4.4 | admins only; categories and names, one count, no letter ids |
| Notices | content-free codes; a test asserts every code's value keys come from a fixed list without sender, summary, label or text |
| Audit log | a letter by id only (D 11); mail items by number and state |
| Member export | runs as the member: own post and private letters they read (PB decision 26) |
| Workspace export (Beheer 5) | runs as the requester, so it follows the switches |
| Full copy with others' private post | only `manage.py export_workspace --all` on the server, with a server audit row and a notice to every member (R8) |
| Archive folder (part D) | system; a letter goes to the folders of its persons and departments only; a letter without a name gets no copy |
| Back-up | contains everything (D 8.3); said in `PRIVACY.md` |
| Background jobs | name an actor (PB task 2); a job for a user runs as that user and re-checks the membership first (4.11) |
| AI call | sees the text of every letter it processes, private or not, under the workspace's recorded consent (`PRIVACY.md`); envelope data never (part B) |
| Push payloads (part B) | one kind code, no names |
| Answers that reveal hidden post exists (`person_in_use`, `cannot_grant_self`, `email_required`, `server_admin_only`) | admin-only operations (D 8.3) |
| "Who can see this letter" | only for a letter the user can read |
| Directory sync (section 6) | writes memberships only; reads no letter |
| Browser | no letter data in `localStorage`, `sessionStorage` or a service-worker cache; the query cache is cleared at sign-out |

### 4.11 Jobs and the actor

`workspace_context(workspace_id, *, actor)` without a default (PB task 2). A job that
works **for a user** takes `user_id`, starts with `workspace_context(ws, actor=user_id)`,
and first checks that the membership still exists; a job that **must see everything**
(pipeline, automatic rules, recompute, nightly check, reminders of part B, archive of
part D) runs as `SYSTEM` and is on the closed list; a job whose output reaches a person
computes the recipients as the system but sends only content-free codes. The manual rule
run (`rules/jobs.py`) takes the starter's `user_id` (R7).

### 4.12 Data model

| Table / field | Kind | Notes |
|---|---|---|
| `Workspace.everyone_sees_all` | bool, default `true` | not under RLS; preset |
| `Workspace.departments_enabled` | bool, default `false` | preset |
| `Workspace.admins_read_all` | bool, default `true` | preset |
| `Workspace.care_is_private`, `Workspace.emergency_access` | PB task 15 | preset |
| `Membership.sorts_post` | bool, default `false` | not for viewers (422 `viewer_cannot_sort`); cleared in the same transaction when the member is made viewer |
| `Document.addressee_ids` | `ArrayField(UUIDField)`, default `list`, GIN index, no `db_default` | derived (4.9.2) |
| `Document.visibility`, `DocumentShare`, `DocumentReader`, `DocumentEvent.note` | PB task 3 | `DocumentReader.reason` as PB |
| `Person.kind`, `DepartmentMember` | PB task 11 | `DepartmentMember.is_lead` bool, default `false` |
| `Label.department` | FK `Person`, null, `SET_NULL` | must be kind department |
| `Task.created_by` | FK user, null | |
| `Invitation.with_device`, `User.email` nullable | 4.19 | |
| directory tables | section 6 | |

Removed from the previous revision: `Membership.post_scope`, `Document.audience`,
`Person.shared_with_everyone`, `Delegation`, `Workspace.scanner_keeps_access`,
`Workspace.default_post_scope`, `Workspace.readers_verified_at`, `Invitation.post_scope`,
`DocumentShare.origin`, `Workspace.setup_step`, `Workspace.setup_completed_at`.

### 4.13 API (additive, `/api/v1`)

| Operation id | Method, path | Notes and errors |
|---|---|---|
| `getVisibility` | `GET /workspaces/{workspace_id}/visibility` | the switches, the sorters, `sentences`; any member (members get the sentences only) |
| `previewVisibilityChange` | `POST /workspaces/{workspace_id}/visibility/preview` | admin; body `VisibilityIn`, or a planned role, department or link change (`member_change`); answers `changes` (per member: name, gains, loses), `letter_count`, `needs_confirmation`, `sentences`, `preview_token` |
| `updateVisibility` | `PUT /workspaces/{workspace_id}/visibility` | admin; body `VisibilityIn`; 409 `confirmation_required`, 409 `preview_outdated` |
| `updateMemberSortsPost` | `PUT /workspaces/{workspace_id}/members/{user_id}/sorts-post` | admin; `{sorts_post, confirmed}`; 409 `confirmation_required`, 422 `viewer_cannot_sort` |
| `updateDepartmentMember` | `PUT /workspaces/{workspace_id}/persons/{person_id}/members/{user_id}` | admin; `{is_lead}` |
| PB `addDepartmentMember`, `removeDepartmentMember` | | + `confirmed: bool = False`; 409 `department_managed_by_directory` (section 6) |
| `getMyPostAccess` | `GET /workspaces/{workspace_id}/post-access` | the page of 4.15; `sentences` rendered by the server |
| `getDocumentAccess` | `GET …/documents/{document_id}/access` | names and why, for a letter the user can read |
| `acceptInvitationWithDevice`, `setMyWebSignIn`, `createUserDeviceCode` | 4.19 | as the previous revision |
| PB's `setDocumentVisibility`, emergency, department and settings operations | as PB | `setDocumentVisibility` shares also on a non-private letter |

`VisibilityIn`: `kind: str | None = None`, `everyone_sees_all: bool | None = None`,
`departments_enabled: bool | None = None`, `admins_read_all: bool | None = None`,
`care_is_private: bool | None = None`, `allow_member_export: bool | None = None` (None
leaves a value), `apply_preset: bool = False` (the request comes from the preset dialog:
one `workspace_preset_applied` row besides the setting rows), `add_start_set: bool =
False` (also runs `applyLabelPreset`), and on `updateVisibility` `confirmed: bool = False`
and `preview_token: str = ""`. Errors: 409 `confirmation_required`, 409
`preview_outdated`. `emergency_access` is not in it (server admin only, PB).

Added fields, each with a default: `WorkspaceSettingsOut` gains the three switches
(`true`, `false`, `true`); `MemberOut.sorts_post` (`false`); `PersonOut` gains
`lead_user_ids` (empty); `LabelIn.department_id` and `LabelOut.department_id` (`null`, a
UUID string); **`DocumentOut.can_read`** (`true`; `confirmDocument` and
`setDocumentAddressees` answer `DocumentOut` today, there is no `ConfirmOut`): when
`false`, the answer carries the id and status only, and empty lists for pages, labels,
addressees, suggestions and tasks, built as the system, so nothing of the handed-over
letter is returned; `InvitationIn.with_device` (`false`). Role changes, department member
changes and person links gain `confirmed: bool = False` and `preview_token: str = ""`
with the same two 409s when 4.4 says so.

### 4.14 Services, audit and notices

- `apps.workspaces.presets.apply_preset_settings(workspace)` (creation) and the
  preset switch inside `visibility_settings.change`.
- `apps.workspaces.visibility_rules` (pure: `diff`, `expected_sight` for tests, the
  categories) and `apps.workspaces.visibility_settings.change` (4.4).
- `apps.workspaces.policy_text.describe(policy) -> list[str]` (pure): the sentences of
  4.2, 4.3 and 4.15.
- `apps.documents.visibility`: PB's module plus share readers on every letter and
  `can_edit`; `organise` writes `addressee_ids`.
- Audit codes added: `workspace_preset_applied` (`preset`), `post_sorter_changed`
  (`member`, `value`), `department_lead_changed` (`member`, `value`),
  `invitation_device_used`, `workspace_full_export` (server), and section 6's codes.
  Reused: `workspace_setting_changed`, `department_member_added`/`_removed`,
  `visibility_changed`, `member_role_changed`, `person_linked`, `emergency_access_*`.
- Notice codes added: `sight_widened` and `sight_narrowed` (values: a category key and an
  optional department name), `admins_read_all_on` (to every member), `workspace_full_export`
  (to every member), and section 6's `directory_departments_changed`.

### 4.15 Screens

**Web.**

- Instellingen → **Wie ziet wat** (4.3) with the preview dialog of 4.4.
- **Afdelingen** (Thuis: Groepen), a page next to Personen while departments are on: a
  list with members, leads and letter counts; "Afdeling maken" (name, name variants,
  members); per department: members (add, remove, lead), labels, "Beheerd via Microsoft"
  when section 6 manages it; archive; delete when empty.
- **Te doen**: "Naam kiezen" / "Toewijzen" (4.5), the picker with departments first and
  the hand-over sentence; filter chips.
- **Labels**: a column "Van" (Iedereen / a department) and the department choice when an
  admin or lead creates one.
- **Leden**: a column "Verdeelt post", and "Afdelingen" per member.
- On a letter: the lock of D, "Delen met", and "Wie kan deze brief zien?" with reasons
  ("op naam van", "afdeling Finance", "verdeelt post", "heeft de brief gescand", "is
  gedeeld", "ziet alle post").
- **"Wie kan mijn post zien?"** (account menu), sentences for example:
  - "Iedereen in Brievie ziet alle post, behalve privé-post." or "Je post zien alleen jij
    en, als hij op naam van een afdeling staat, de mensen van die afdeling."
  - "De beheerders Erik en Sanne lezen alle post die niet privé is." / "De beheerders
    lezen je post niet."
  - "Post zonder naam zien: Sandra (verdeelt post)."
  - "Privé-post zie alleen jij, en wie je die brief geeft."
  - "Noodtoegang staat aan. De beheerders kunnen je post openen. Ze moeten een reden
    geven, en jij krijgt dan een melding." / "Noodtoegang staat uit."
  - (section 6) "In welke afdeling je zit, komt uit Microsoft 365."
  - (part B) "De postkamer (Sandra) ziet van elke envelop voor jou de naam van de
    afzender." (part D) "Een leesbare kopie van je post staat in de map Brievie in je
    thuismap."
  - "Wie de server zelf beheert, kan technisch bij alle gegevens. Dat is Erik."
  - "Tekst van brieven gaat naar <dienst>." (the AI line)
- `/w/<id>/ingesteld` (4.2).
- `/setup`: the two cards with the sentences of 4.2, "Organisatie" for "Zakelijk", no
  "Afdelingen" field.

**iOS.** The lock and "Privé" (D 13); departments in the person picker; "Naam kiezen" /
"Toewijzen" and the hand-over sentence in the review; "Wie kan mijn post zien?" under
Werkruimte (read-only, the server's sentences). Settings stay in the web: the app opens
"Wie ziet wat" in the browser for admins. "Organisatie" for "Zakelijk" in the workspace
list.

### 4.16 Migration

One migration per app, reversible where it can be:

- every existing workspace gets `everyone_sees_all = true`, `departments_enabled = false`,
  `admins_read_all = true`, **whatever its kind**: today everyone sees everything, and
  on the day of the update nothing a member sees changes (an existing Zakelijk workspace
  sees "Iedereen ziet alle post: aan" and can switch);
- `addressee_ids` is filled from `DocumentAddressee`, set-based per workspace inside
  `workspace_context(ws, actor=SYSTEM)` (migrations run as the owner, who sees nothing
  under forced RLS; the pattern of the audit purge);
- labels, persons, rules: no data change (`Label.department` null, `Person.kind`
  `person`).

No guard like the earlier `readers_verified_at` is needed: the migration creates no
reader rows, and a wrong array can only be missing an id (it is built from the links),
which the nightly check reports; the leak suite runs on a migrated copy.

### 4.17 Tests that must exist

The dangerous ones are marked ⚠.

- ⚠ **The transition test**, table-driven from 4.4: every start state (E, D, A, S: 16)
  × every change in the table × the actors admin, sorter, department member, plain
  member, viewer × the letters own, own department, other department, another member's,
  unassigned, only on a department, on a person without an account, private, scanned and
  in review. For each: the database's answer (as the actor, through the policy) equals
  `visibility_rules.expected_sight`; `needs_confirmation` is true exactly when someone
  gains; the notices go to exactly the changed members; the audit rows exist; no
  `DepartmentMember`, `Label`, `Rule` or mapping row was deleted; switching back gives
  the start sight; private letters' sight is identical in every state.
- ⚠ **No orphan**: in every state, every non-private letter is visible to at least one
  member (property test over random states and letters).
- ⚠ The leak suite of PB task 16, extended to every row of 4.10 for a plain member and a
  colleague's letter in state E off.
- ⚠ An update or delete without a WHERE as a plain member leaves hidden letters and
  child rows alone.
- ⚠ A job with an empty actor sees no document.
- ⚠ A member removed between enqueue and run of a job for them: the job writes nothing.
- ⚠ Confirming a letter for Jan as a member who does not read Jan's post: 200,
  `can_read: false`, no 42501, the suggestion dismissals applied (not 0 rows), and the
  answer holds no page, label or addressee; a repeated confirm answers the same.
- ⚠ A confirmed change with a stale `preview_token`: 409 `preview_outdated`.
- ⚠ `viewers()` equals the policy for every actor and letter of the transition test.
- ⚠ A workspace admin who does not read all post makes a reset link for anybody: 403.
- ⚠ Making a member admin while "Beheerders lezen alle post" is on without
  `confirmed`: 409; with it: every member has the notice.
- ⚠ Assigning a task to a member who cannot read the letter, or to a viewer: 422.
- ⚠ A v2 QR code used twice at once, after 30 minutes, after revocation (4.19).
- ⚠ A department label is not offered to a non-member and grants nothing.
- The nightly check finds a hand-damaged `addressee_ids`.
- The migration on a copy of a 1d-A database: every member sees exactly what they saw.
- `describe()` produces the sentences of 4.2 for both presets and of 4.15 for each state.

### 4.18 What changes in PB (the 1d-B plan)

- Task 2: the manual rule run uses the starter's id; the job takes `user_id`;
  `apps/documents/visibility.py`, `review.py` and `apps/tasks/services.py` join the
  system callers.
- Task 3: the document condition of 4.9.3; `addressee_ids`; the task policy with
  `Task.created_by` (the CASE policy replaces `restrict_to_visible_documents("tasks_task",
  nullable=True)`, and the coverage test expects it); the four helper functions with
  their user parameter; the workspace switches. Its tests change:
  `test_empty_actor_sees_public_letters_but_no_restricted_one` becomes "an empty actor
  sees no letter" (R6); `test_task_without_document_stays_visible` becomes the R39 cases.
- Task 4: the data and states of 4.9.5, the explicit member filter, the adapted EXPLAIN
  check.
- Task 5: `share` readers on every letter; `sync_addressee_ids`; the nightly check
  compares it; `viewers(document_ids)` and its equality test.
- Task 7: `keep_own_access` only for private letters (4.9.3).
- Task 11: `DepartmentMember.is_lead`, `Label.department`, archive rules, the
  `departments_enabled` switch, `confirmed` and `preview_token`, the 4.4 helpers.
- Task 12: a link changes non-private sight: the 4.4 helpers, the self-link notice.
- Task 13: the own export stays PB decision 26 (own post and the private letters the
  member reads), read as the member; the switches do not widen it.
- Task 14: condition 3 of 4.19.
- Task 15: the three switches with their preset values, `sorts_post`.
- Task 18: the confirm order of 4.9.3; rules at confirm stay in step 1 as the user;
  `DocumentOut.can_read`; the repeated confirm as the system.
- Task 20: emergency access opens all hidden post of the person (4.7), and
  `require_regular_reader` becomes `can_edit` (4.9.3), so an admin with emergency access to
  a non-private letter cannot add their own person or a share either.
- Task 24: the pages of 4.15.
- New tasks after task 15: the switches with preview and confirmation (4.4) and the
  transition test; sorters; the "Wie ziet wat" and "Afdelingen" pages; Te doen wording;
  `describe()` and "Wie kan mijn post zien?"; the setup form and `/ingesteld`; the v2
  invitation (4.19).

### 4.19 Inviting and pairing for a person in one QR code, and account actions

Unchanged from the previous revision except where marked; part A3 in section 13.

Today an invitation is accepted in a browser, then the phone is paired with a code (D
6.2). For pap, a child or a colleague without a computer, the admin wants: "Koppel een
telefoon voor Pap", hand over the phone, scan, done.

- **Invitation with device.** `createInvitation` gains `with_device` (default `false`).
  With it, the invitation's expiry is 30 minutes (it is meant to be scanned now) and
  the web shows a QR code with payload version 2:
  `{"v":2,"urls":[…],"ca_sha256":…,"invite":"<token>"}` (no pairing code). A 1c/1d app
  says "Werk de app bij" (it refuses `v` 2 already); a new app accepts both versions.
- **In the app.** Scan → check the CA fingerprint and health as for a pairing QR →
  `getInvitation` (token in the body) shows the workspace, who invited, the person ("Je
  wordt: Pap"), the role and the AI line (D 6.2) → the user confirms the name and taps
  "Klaar" → `acceptInvitationWithDevice` (auth none; token, name, optional email and
  password, device name, app version) creates, in one transaction: the account, the
  membership with the invitation's role, the person link (a grant, D 8.1), the
  device and its token, the invitation marked used, audit rows `invitation_accepted` and
  `device_paired`. It answers with the device token, like `pairDevice`.
- **One account per person, no shared accounts.** The existing unique constraints (one
  person per account per workspace, one account per person) stay; a person that already
  has an account cannot be the subject of a new invitation (409 `person_has_account`);
  for that, the admin uses "Telefoon koppelen voor <naam>", an account action (below).
- **App-only accounts** (R11). An account made this way may have no email address and no
  password: `User.email` becomes nullable (unique when present), the password unusable.
  It can only be used through its paired phones. In the app, "Ook op de computer
  inloggen" sets an address and a password (`setMyWebSignIn`, device-token auth, the
  password rules of D). Recovery of a lost phone is a new device QR made by an admin
  under the rules below. When the invitation names a person with post the admin cannot
  see, D's rule holds: the invitation needs an email address and the app asks for it.
- **Threat.** The QR code is a bearer secret on a screen for 30 minutes, single use,
  rate limited as pairing. Whoever photographs it before the phone does gets pap's
  account; the grant then shows in the history of pap's private letters (D 8.5 point 4).

#### Account actions

The owner decided (2026-11-19) that for a member with private post only the server admin
makes a reset link. Widened by the same reasoning (an account action lets the actor act as
the member): a workspace admin may make a reset link, end sessions, list or revoke
devices, or make a device QR for a member only when

1. the member's single membership is that workspace and the member is no server admin
   (D 6.3), **and**
2. the member reads no private letter (no non-emergency reader row on a restricted
   letter), **and**
3. the admin reads all non-private post in that workspace ("Iedereen ziet alle post" on,
   or "Beheerders lezen alle post" on). *Changed: was "the admin's own scope is `all`".*

Otherwise 403 `server_admin_only`. `account_action_requires_server_admin(target)` gets
conditions 2 and 3 (3 needs the actor; the seam gains it as an argument). In an
organisation, where admins do not read all post, every account action is the server
admin's (R10).
The member always gets the notice (D 6.3).

### 4.20 Compared with the previous revision

One line each. Fewer concepts: the previous revision asked a builder and an admin to
learn scopes, audience, a household person, delegations with four levels, the scanner
setting and a seven-step wizard; this one asks for four switches, departments and a
sorting flag.

| Item (previous revision) | Now | Why |
|---|---|---|
| Per-member post scope (*Alles*, *Eigen en gezamenlijk*, *Alleen eigen*) | **removed**; replaced by the workspace switches of 4.1 | one rule per workspace is what helpdesks' small customers use (RA 1); per-member levels were the hardest part to explain |
| `Document.audience` and the household person `shared_with_everyone` | **removed** | "post for everyone in the house" is post without a name, which goes to the parents as sorters and admins (4.1) |
| Delegation (*meekijken*) with four levels and `includes_private` | **later** (section 9) | absence is covered by departments and by "Delen met" on a letter; at home the admins read all |
| Per-letter private mark | **stays** (D, PB) | the one exception every setting respects |
| Break-glass (emergency access) | **stays**, as extended before (4.7) | an organisation needs a traceable way in when someone is ill |
| `scanner_keeps_access` setting and the scanner share | **simplified** to a fixed rule: the scanner sees the letter until it is done (4.8) | no setting to explain |
| Reader rows for every addressed letter | **reduced** to private letters, shares and emergency (4.9.4) | the array check is cheaper and needs no recompute when a person or department changes |
| Departments | **stay**, plus leads, labels and an off switch | the owner's organisation model |
| Account actions by a workspace admin | **stay**, condition 3 reworded (4.19) | |
| Invitation with device (v2 QR), app-only accounts | **stay** unchanged (4.19, part A3) | |
| Seven-step setup wizard (part C) | **removed** (R27 withdrawn, R40) | the owner's decision: one choice at installation |
| The mailroom (part B) | **stays** as designed, opt-in, built later; edits listed at its top | |
| Folders per person (part D) | **stay**; the "Gezamenlijk" folder goes with `audience` | |
| SSO groups to departments (part E) | **moved in** as section 6 | the owner asked for it |


---

## 5. Workspaces versus departments

### 5.1 What a workspace is now

The workspace stays in the data model as the **hard wall**: its own data, its own key,
its own members, its own row-level-security boundary. It is used where a hard wall is
right: a home and one's own company on one NAS, two legal entities that must not see each
other's post, a provider hosting several customers. It is **no longer a concept people
must learn**:

- one workspace per server is normal and invisible: with one workspace the web goes
  straight in (`/` redirects to it; the workspace list appears only from two), and the iOS
  app already selects the only one;
- an organisation divides its post with **departments**, never with extra workspaces;
  the Afdelingen page and the documentation say so ("Wil je post scheiden binnen je
  organisatie? Maak afdelingen.");
- a person in two workspaces has two memberships and sees each separately, as today.

### 5.2 A second workspace is a server admin's deliberate act

`manage.py create_workspace --name "…" --kind home|business --admin-email …` (server
admin on the server; audit `workspace_created`, server log): it creates the workspace,
applies the preset (4.2) and makes the named account its admin (an existing account, or
an invitation link printed once). There is no button for it in the web: a button would
invite using workspaces where departments belong (RA lesson 6). D 6.1 said only setup
creates a workspace; this is the one addition.

### 5.3 "Work and home" means two servers: the iOS app

The owner's "for work and also at home" is, in practice, the office's server and the
home server. The app pairs with **one server** today (section 3). Supporting several is
**out of scope** for this design and sized here so it is not lost:

- a list of pairings, each with its own pinned certificate authority, device token,
  addresses and server id (Keychain keys per server id: `deviceToken.<serverID>`,
  `serverConnection.<serverID>`, with a one-time move of today's two keys);
- one switcher listing "server · workspace" rows, named by the user ("Thuis", "Werk"),
  where the workspace list is today (`SettingsView`), and on the title menu of Te doen
  (Bitwarden's model, RA 4: each account its own server, one dropdown);
- uploads already carry `serverID` and `workspaceID`; the queue sends each item to its
  own server; calendar settings are already per workspace and gain the server id in
  their keys; local notices per server;
- revocation per server: a 401 from one server unpairs only that one and says which;
- the background refresh checks each server in turn.

Size: **M–L for iOS** (about 15–20 tasks: storage migration, connection store, API
client per server, switcher, queue and calendar keys, tests), **nothing for the host**.
Until then the honest answer is "één telefoon, één server", and a person with home and
work servers uses the web for one of them.

### 5.4 The word

Keep **"werkruimte"** in the interface where it still appears (the workspace list, the
iOS settings). Renaming to "omgeving" costs every web and iOS string, the glossary, the
site and the screenshots, for a word most users will no longer see (5.1). The Dutch
summary and the Afdelingen page avoid it (R50).

## 6. Identity providers: directory groups to departments

**What this adds to D part C.** D 9 (OpenID Connect sign-in, invite first, the "Test"
button that runs discovery, emergency password sign-in for server admins, 12-hour SSO
web sessions, the device re-confirm period) and D 10 (second factor) hold unchanged.
This section adds, as part **C2** after D's part C (here **C1**):

1. a mapping "directory group → department, optionally a role" per workspace (6.2);
2. synchronisation of department memberships and roles at each SSO sign-in (6.3);
3. "Test inloggen", which runs the real flow and shows the received claims without
   signing in (6.6);
4. a shorter default device re-confirm period while a mapping exists (6.4);
5. audit and notice codes, provider guides and the test set-up (6.7, 6.8).

Nothing of this is visible to a home user: it lives on the server admin's "Aanmelden"
page, which shows only after "SSO gebruiken" is switched on.

### 6.1 Which claims

- **Microsoft Entra ID**: the `groups` claim with **security groups**, configured in
  the app registration's token configuration; values are **group object ids** (GUIDs),
  names only for groups synced from on-premises AD (RA 7). Organisations with Entra ID P1
  (in Microsoft 365 Business Premium) are advised to choose "Groups assigned to the
  application": it limits the claim to the groups that matter and avoids overage, but
  group assignment needs P1 (RA 7). App roles (`roles` claim) are not used in C2: for
  departments they would mean one app role per department in Entra; they are a later
  option for the role only.
- **Keycloak**: a Group Membership mapper, claim `groups`, "Full group path" off or on
  (the value is stored as received).
- **Authentik, Authelia, Zitadel and other OIDC providers**: a `groups` claim of strings
  (Authelia sends it by default with the `groups` scope, RA 7). The claim name is a
  setting (default `groups`).
- **Google Workspace**: Google's ID token is not known to carry groups (not verified);
  the guide says SSO sign-in works, department mapping does not.

### 6.2 The mapping

`DirectoryMapping` (server table, server admins only; one provider per server, D 9.1):
`issuer`, `group_value` (the exact claim value, 1–256 characters), `label` (what the admin
types, e.g. "Finance (Microsoft)", because Brievie cannot look a GUID up without Graph),
`workspace`, `department` (a `Person` of kind department in that workspace), `role`
(null, `member` or `viewer`). Unique on (`issuer`, `group_value`, `department`).

- **Map by id, not by name**, and only recognised values: a group without a mapping is
  ignored and never creates a department (RA lesson 7).
- **A department is managed either by the directory or here, never both** (R46): a
  department with at least one mapping is "Beheerd via <provider>"; adding or removing its
  members by hand answers 409 `department_managed_by_directory` ("Deze afdeling wordt
  bijgehouden in Microsoft 365. Pas het daar aan."); deleting its last mapping makes it
  manual again and keeps its members.
- **Never admin, never sorter, never mailroom** from a claim (R47): admin rights, "Verdeelt
  post" and `mail_desk` stay set here. A mapped `role` may set `member` or `viewer`; the
  highest mapped role wins; an admin's role is never changed by a sign-in.
- **A mapping change** is a server audit row (`directory_mapping_changed`, values
  `department`, `role`, `action`) and is previewed: removals take effect at once for users
  whose last received groups (6.3) no longer qualify, additions only at their next
  sign-in (gaining sight must rest on a fresh claim). The preview lists both.

### 6.3 Synchronisation at sign-in

At each SSO sign-in, after D 9.2's token validation (signature, issuer, audience,
expiry, nonce) and D 9.3's account choice:

1. read the groups claim from the **ID token** of the configured issuer only (a token
   from any other issuer never reaches this step);
2. claim present → for every workspace with mappings: the directory-managed departments
   the user should be in = mappings whose `group_value` is in the claim; add missing
   memberships, remove memberships of directory-managed departments not in the claim;
   set the role when a mapping gives one; each change is an ordinary
   `department_member_added`/`_removed` or `member_role_changed` row with actor "SSO",
   PB's `recompute_where` for private letters, and one notice to the user
   (`directory_departments_changed`: "Je zit nu in Finance en niet meer in HR. Dat komt
   uit Microsoft 365.");
3. claim **absent**, or Entra's overage marker (`_claim_names` containing `groups`, or
   `hasgroups`) → **nothing changes** (R47); the sign-in succeeds; a server audit row
   `directory_claim_missing` (value `reason`: `missing` | `overage`) and at most one
   notice per day to the server admins ("Microsoft stuurde geen groepen mee voor Jan.
   Brievie heeft zijn afdelingen niet veranderd."). Reading "no claim" as "no groups"
   would empty every department after one configuration mistake (RA, not to copy). The
   Graph lookup that resolves overage is later;
4. store the received values on `ExternalIdentity` (`last_groups`, at most 300 values,
   `groups_seen_at`), for the preview of 6.2 and the "Leden" page ("Afdelingen uit
   Microsoft 365, bijgewerkt 3 december 09:12").

New members: invite first (D decision 3). With "automatisch aanmaken" on (D 9.3 point
4), an account is made in the configured workspace with role `viewer`, or with the role a
mapping gives (R52), and lands in its mapped departments.

### 6.4 Leaving, moving, being disabled

- **Moved** to another group: the user's departments follow at their next sign-in. The
  web session started through SSO lasts at most 12 hours (D 9.4), so within a working day
  for web users.
- **Phones** do not sign in through SSO; a device paired from an SSO session must be
  re-confirmed by an SSO sign-in within the re-confirm period (D 9.4). While a mapping
  exists, that period's **default is 14 days** instead of 90 (minimum 7, R49), so a phone
  follows the directory within two weeks at most. The admin page says this in one
  sentence.
- **Disabled** in the directory: the next sign-in fails at the provider; the web session
  ends within 12 hours; phones stop at the re-confirm deadline. **Removing the member in
  Brievie stops everything at once** (D 6.3: devices revoked, sessions ended), and the
  guides say: "Haal iemand die vertrekt ook in Brievie weg." Letters and tasks stay with
  the person and the departments (D decision 2). Audit rows as D.
- What closes this gap fully (back-channel logout, SCIM) is later (6.9), because both
  need the provider to reach the server.

### 6.5 Never locked out

At least one server admin keeps a local password (D 9.4); "alleen via SSO" never applies
to a server admin, and it can only be switched on after a successful "Test inloggen" by
the same admin in the last hour. A provider outage stops new SSO sign-ins only; sessions
and phones keep working (D 9.4); memberships stay as last synchronised.

### 6.6 The admin's screens

"Aanmelden" (server admin), in this order:

1. **Provider**: issuer URL (with the Entra example
   `https://login.microsoftonline.com/<tenant-id>/v2.0`), client id, client secret
   (write-only, sealed), and the redirect URL to copy
   (`https://<server>/api/v1/auth/oidc/callback`) with a "Kopieer" button, shown before
   anything is filled in. "Controleren" runs D's discovery test (issuer match, `jwks_uri`,
   `S256` among the PKCE methods) and answers in a sentence.
2. **Test inloggen**: opens the provider's sign-in in a new tab (`prompt=login`); the
   callback, marked as a test by its `state`, creates no session and changes nothing, and
   shows: issuer, subject, email, the claim names that arrived, the groups received (as
   values), overage or missing, and what Brievie would do ("Je zou in Finance komen. Groep
   9f2c…e1 kent Brievie niet en wordt overgeslagen."). Each received value has "Koppel aan
   afdeling".
3. **Groepen en afdelingen**: the mapping list per workspace (label, value, department,
   role), with the preview of 6.2 on every change; per department how many members came
   from the directory and when they were last updated.
4. **Instellingen**: claim name, "automatisch aanmaken" (D), "alleen via SSO" (D), the
   re-confirm period.

A short guide per provider is linked from the page (6.7).

### 6.7 Provider guides

`docs/sso/entra.md`, `keycloak.md`, `authentik.md`, `authelia.md`: where to register the
app, the redirect URL, the client secret, the groups claim (and, for Entra, the overage
advice and "Groups assigned to the application" with P1), and the Test inloggen step.
Until the manual check of 6.8 is done, the Entra guide and the site say: "Microsoft Entra
ID werkt volgens de documentatie van Microsoft; wij hebben het nog niet zelf getest."

### 6.8 Testing without owning a directory

- **In CI** (a separate job, not in `make test`): Keycloak in a throwaway container
  (`quay.io/keycloak/keycloak` at a pinned exact tag, `start-dev --import-realm`, health
  on `/health/ready`), with a realm file in the repository
  (`backend/tests/sso/brievie-realm.json`): a client `brievie` with PKCE, a Group
  Membership mapper, users in groups Finance and HR, a user in none, a disabled user, a
  second client without the mapper. Tests: sign-in; mapping applied; a user moved between
  groups between two sign-ins; the disabled user refused; the client without mapper
  (claim missing) changes nothing. Whether `--import-realm` works with `start-dev` and the
  start time are measured in the first task (RA 11).
- **Claim edge cases** as ordinary unit tests with ID tokens signed by a test key and a
  local JWKS stub (no extra container): GUID values, 250 groups, Entra's overage marker,
  a token from another issuer, an empty claim, a claim with a value that maps to two
  departments.
- **Once, by hand, against a real Entra tenant** before calling Entra supported: a free
  Azure account gives a tenant; it needs from the owner a Microsoft account, a phone
  number and a non-prepaid bank card (Microsoft may place a temporary hold), and about an
  hour. Checklist `docs/sso/entra-check.md`: app registration, redirect URL, secret, two
  security groups, groups claim on the ID token, Test inloggen shows two GUIDs, mapping,
  a sign-in lands in the department, removal from a group takes effect at the next
  sign-in. Whether the groups claim works on Entra ID Free is verified there (RA 7 gap).
- Keycloak is a CI service, not a runtime dependency; it still gets a `DECISIONS.md` line.

### 6.9 Not built, and why

- **LDAP directly**: not built (R48). Domain controllers increasingly refuse simple binds
  without TLS and signing; it would need a bind secret, LDAPS certificates and AD-specific
  quirks. An AD-only organisation federates through Entra Connect or Keycloak's LDAP
  federation and uses OIDC (RA 8).
- **SCIM** (C3, last): pre-provisioning and disable-on-leave within Microsoft's cycle, but
  Entra must reach the server over HTTPS, or the customer needs the Windows provisioning
  agent with P1 (RA 9). Built when a real organisation asks; a SCIM `active=false` then
  maps to removing the member, never to deleting post.
- **Back-channel logout** (C3): small, but also needs the provider to reach the server.
- **Graph lookup for overage**: needs an extra Graph permission and Entra-specific code;
  "Groups assigned to the application" is the advice instead.

### 6.10 Data model, API, codes

- `DirectoryMapping` (6.2); `ExternalIdentity.last_groups` (JSON list),
  `groups_seen_at`; `SsoSettings.groups_claim` (default `groups`),
  `SsoSettings.device_reconfirm_days` default 14 while a mapping exists.
- Operations (server admin): `listDirectoryMappings`, `createDirectoryMapping`,
  `deleteDirectoryMapping`, `previewDirectoryMappingChange`, `startTestSignIn` (answers the
  provider URL), `getTestSignInResult`. Errors: `department_managed_by_directory` (409),
  `mapping_exists` (409), `test_sign_in_required` (409, for "alleen via SSO").
- Server audit codes: `directory_mapping_changed`, `directory_claim_missing`,
  `test_sign_in_used`. Workspace codes reused with actor "SSO". Notice:
  `directory_departments_changed`.
- A separate app, `apps/directory`, so that question Q1's answer (open core or paid
  tier) can move it without touching part A.

### 6.11 Done means

On the Keycloak CI realm, Anna signs in and sees Finance's post; moved to HR in Keycloak,
she sees HR's and not Finance's after her next sign-in; the disabled user cannot sign in;
a broken mapper changes nothing and the admin sees why. After the manual Entra check the
same holds against Microsoft, and the guide drops its "nog niet zelf getest".

---

## 7. Part B: the mailroom (closed envelope), opt-in

Kept as designed in the previous revision, opt-in and built later (after question Q2).
Changed by this revision: no wizard proposes a mode; delegates are gone from the
recipients (absence is a department colleague or a forward); a content upload sets no
`scanned_by`; department leads come from part A (4.5). Visibility of the resulting
letter follows part A.

### 7.1 Settings

All under one workspace setting, off by default; each change an audit row
(`workspace_setting_changed`) and visible in the summary sentences.

| Setting | Values | Thuis default | Organisatie default |
|---|---|---|---|
| `mailroom_mode` | `off`, `light` (any member registers an envelope for someone), `desk` (only members with `mail_desk`) | `off` | `off` |
| `envelope_sender` | `off`, `category` (only a known sender's category: "Overheid: Belastingdienst", "Deurwaarder"), `name` (category or a typed name) | `name` | `category` |
| `mail_content_private` | content scanned for a mail item is private for its addressee | on | on |
| `mail_content_ai_when_marked` | content of a Persoonlijk/Vertrouwelijk item goes to the AI without the addressee asking | off | off |
| `mail_reminder_days` | 1–14 | 2 | 2 |
| `mail_escalation_days` | off, 2–30 | off | 5 |
| `envelope_retention_days` | 0 (delete at opening), 7–90 | 0 | 30 |

`Membership.mail_desk` ("Postkamer") is set by an admin, never for oneself (409
`cannot_grant_self`), audited, and named on everyone's "Wie kan mijn post zien?".

### 7.2 The mail item

A **poststuk** has two layers: the envelope (registered by the mailroom or a housemate)
and the content (scanned by the addressee, or by the mailroom when allowed). The content
is an ordinary `Document`, linked to the item.

`MailItem(WorkspaceModel)`, forced RLS:

| Field | Notes |
|---|---|
| `number` | per-workspace counter, shown as at least four digits ("0412"), unique, never reused; written on the envelope with a pen |
| `received_on` | date |
| `registered_by` | user |
| `person` | addressee (person or department), null when unknown |
| `addressee_text` | what the envelope said when no person matched (BSN-masked, 150) |
| `department` | a department read from "t.a.v." when the addressee is a person |
| `sender_category` | key from `rules/nl/senders` or empty; only when `envelope_sender` ≠ `off` |
| `sender_name` | BSN-masked, 200; only when `envelope_sender = name` |
| `personal`, `confidential`, `registered` | the markers Persoonlijk, Vertrouwelijk, Aangetekend |
| `urgent` | derived: `registered`, or a sender category on the urgent list (deurwaarder, rechtbank, CJIB, Belastingdienst aanmaning; data in `rules/nl`, versioned) |
| `state` | 7.3 |
| `document` | FK `Document`, null until content is scanned |
| `envelope_path`, `envelope_expires_at` | the sealed envelope image (same sealing as pages); null after removal |
| `last_reminded_at`, `escalated_at` | |

`MailItemRecipient(mail_item, user, reason)` (derived like readers, written only by
`apps.mail.recipients.recompute` as the system): users linked to the person, members of
the department (while departments are on; otherwise the item is `unassigned`); they
are also the ones *notified* (reminders, push). `MailItemEvent` is the item's history (child of the item).

The mail desk is, in Zammad's terms, *overview plus create-only* (R30): it sees every
envelope's data and registers new ones, and loses the content the moment it uploads it
for somebody — said on screen before the upload, because Zammad's silent loss of access
after saving is a documented trap.

**Policy on `MailItem`** (restrictive select/update/delete): system, or
`brievie_actor_is_mail_desk()`, or a recipient row for the actor, or
`registered_by_id = actor` (the housemate in `light` mode). A department lead, an admin
or a read-only member without one of these sees nothing of the item. `MailItemEvent`
and `MailItemRecipient` follow their item.

**What is read from the envelope**, in the app, on the phone: Vision OCR, then the text
goes to `matchEnvelope`, which runs the existing name matching (`people.matching`, no AI)
for persons and departments and the known-sender list for the category, and answers
candidates. The envelope text is **not stored** (R16): only the outcome. The BSN mask
applies to every stored field (window envelopes).

### 7.3 Life cycle

Each state answers one question, "wie is aan zet?" (after Zendesk's states, R30); the
UI shows that answer, not the state's name.

| State | Who acts next | Reached by | Audit code |
|---|---|---|---|
| `waiting` | the addressee: collect, scan, or ask to open | registration with an addressee; forwarding | `mail_item_registered` (value `number` only) |
| `unassigned` | the desk: find the addressee | registration without a match; "Niet voor mij"; addressee left | `mail_item_registered` / `mail_item_not_for_me` |
| `open_requested` | the desk: open and scan | the addressee taps "Open hem voor mij" | `mail_item_open_requested` |
| `collected` | the addressee: scan, or close as kept | the addressee or the desk taps "Opgehaald" | `mail_item_collected` |
| `opened` | nobody here; the letter goes on as a document ("Te doen" if it needs review) | content uploaded with `mail_item_id` (by the addressee, or by the desk when requested or allowed by policy) | `mail_item_opened` (value `by`: `addressee` \| `mail_desk`) |
| `closed` | nobody | "Bewaard zonder scannen", "Retour afzender", "Doorgestuurd buiten de organisatie" | `mail_item_closed` (value `reason`) |

`collected` can still become `opened` (scanned later at the desk of the addressee).
Six states is the limit; reasons are values, not states.
Every move is a `MailItemEvent` the recipients and the desk see; audit rows carry the
number, the person's id and name, and the code — never sender, category or markers.

### 7.4 Opening policy

`MailOpenPolicy(workspace, person (department or person, null = every addressee),
sender_category (null = any), created_by)`: "de postkamer mag deze post openen". Rules:

- Persoonlijk and Vertrouwelijk are **never** openable by the desk without
  `open_requested`; this is in code, not in a setting (R23).
- A policy is admin-made, audited (`mail_policy_changed`), and appears on the "Wie kan
  mijn post zien?" of everyone it covers.
- With a policy, the desk sees "Openen en scannen" at registration; the content upload
  goes straight to the addressee as a private letter.

**The desk never keeps the content.** An upload with `mail_item_id` sets no
`scanned_by` (the item's history records who uploaded), so the scanner rule of 4.8
never applies; it sets visibility `restricted` when
`mail_content_private`, and the readers are the item's addressee readers. The upload
endpoint checks: actor is a recipient, or actor is mail desk and the item is
`open_requested` or covered by a policy; otherwise 403 `mail_opening_not_allowed`. The app
says it: "Na het uploaden kun jij deze brief niet meer zien."

### 7.5 Notifications, reminders, escalation

- **In the product**: "Er ligt post voor je" is the list of the user's `waiting` and
  `open_requested` items, at the top of "Te doen" in web and app. No separate notice
  table (R19).
- **Reminders, not service clocks** (after Linear, not Zendesk's SLA timers; R30): an
  item `waiting` for `mail_reminder_days` (one day when urgent) reminds its notified
  recipients once per interval (job `remind_mail_items`, hourly, system). Nothing pauses
  or resumes a clock.
- **Later** (snooze): the addressee can hide an item from "Te doen" until a date
  ("Herinner me maandag"); any new event on the item (forwarded, corrected, the desk
  writes a note) ends the snooze at once; reminders and escalation skip a snoozed item
  until its date. `MailItem.snoozed_until` (per item; an item has one addressee user in
  practice, and a department's members share it).
  After `mail_escalation_days`, the department's leads (`DepartmentMember.is_lead`) and
  the desk get "Er ligt sinds 5 dagen post voor Jan (nummer 0412)." — no sender, no
  marker (R21).
- **Absence**: post on a department reaches its other members; for post on a person's
  own name the desk forwards the item (`forwardMailItem`) to a colleague, who then sees
  the envelope data, never content scanned before.
- **Departed staff**: when the person's member is removed or the person archived,
  `waiting` items become `unassigned`; new envelopes for them match nothing offered and
  land in "Onbekend" with the desk.
- **Unknown names**: "Onbekend" queue for the desk: assign a person, "Retour afzender",
  or "Bewaren".
- **"Niet voor mij"**: back to "Onbekend" with the addressee's short reason (BSN-masked).

### 7.6 Push: what it needs and what is honest

Apple's push service (APNs) accepts notifications for an app only from a provider
holding an authentication key of the app's developer team; that key can send to every
app of the team. The App Store's Brievie app belongs to the publisher's team. So **a
self-hosted server cannot push to the App Store app on its own**; handing the key to
every server would let any server push to any Brievie user. Options:

| Option | What the self-hoster sets up | Who sees what | Verdict |
|---|---|---|---|
| A. No push: local notifications when the app syncs (exists), plus iOS background refresh | nothing | nobody | always there; not timely |
| B. A push relay run by the publisher (spec 11 "Connect push-relay"), opt-in per server | switch it on; the server registers and gets a relay token | the relay and Apple see: a device token, a server id, the time, one kind code | **recommended** for the iOS app |
| C. Web Push from the server itself (VAPID keys made by the server, payload encrypted for the browser) | a publicly trusted certificate (D part D), and the web added to the home screen on iOS | the browser vendor's push service sees time and size; not the content | as well, for servers with a trusted certificate; impractical with the private authority at home, because service workers need a trusted certificate |
| D. Each self-hoster builds the app with their own key | an Apple developer account | — | documented for developers only |

**Payload, both channels** (the ntfy model: a fixed text plus an id; R30):
`{"aps":{"alert":{"loc-key":"MAIL_WAITING"}},"n":"<random notification id>"}` (or the Web
Push equivalent). The text lives in the app ("Er ligt post voor je."). The id is a
random value made per push (`PushNotice` row: id, user, kind, target, 7 days), not an
item or document id; the app asks its own server, over the pinned connection, what it
points to and opens that screen. No workspace name, no number, no urgency (an
interruption level would tell Apple which post is urgent). Nextcloud's alternative,
sending the subject encrypted to a device key, is not taken: there is no subject worth
the extra key management.

**What the server holds**: per device that opted in a `PushEndpoint` (server table, like
`Device`): kind (`apns_relay` | `web_push`), the token or subscription (sealed with the
master key), last success, disabled. For B, after Bitwarden's self-host model: one env
block, `BRIEVIE_PUSH_INSTALLATION_ID` and `BRIEVIE_PUSH_INSTALLATION_KEY`, obtained once
from the publisher, plus `BRIEVIE_PUSH_RELAY_URL` with the publisher's default; never
shared between installations. For C: the VAPID key pair (sealed). The server calls out
only after a server admin switches a channel on (Beheer anti-goal "no call without being
asked"). A job `send_push(user_id, kind, target)` sends; failures disable the endpoint
after the provider says the token is gone. The app shows, per server, whether push
works ("Meldingen: aan" / "Meldingen: alleen als de app open is"), as Home Assistant
does for local push; without a relay it falls back to checking at app start and
background refresh (ntfy's no-upstream mode: late, never wrong). Whether the relay is
free or part of a paid Connect is question Q3.

### 7.7 The mailroom mode in the app

- "Postkamer" (only with `mail_desk`, or `light` mode for everyone): the camera stays
  open. Per envelope: photo → on-phone OCR → `matchEnvelope` → "Voor: Jan de Vries
  (Finance)" with the markers as switches and, per the setting, the sender category →
  "Registreer" → a full-screen number "Schrijf 0412 op de envelop" → next envelope.
  Ten envelopes a minute is the goal. Offline: registrations queue on disk as scans do,
  and the number arrives at upload (the screen then says "Nummer volgt").
- "Post voor jou" for the addressee: the item, the envelope image while it is kept, and
  "Opgehaald", "Scan de inhoud", "Open hem voor mij", "Niet voor mij". "Scan de inhoud"
  opens the normal scanner with `mail_item_id`; the review pre-fills the sender from the
  item when `envelope_sender = name`.
- **Light home form**: in "Te doen", "Envelop voor iemand anders" → photo → "Voor Mila"
  → Mila sees "Bert heeft een envelop voor je gescand." No number unless switched on.

Web: a "Postkamer" page (queue by state, "Onbekend", search by number, registration
without a photo for a desk with a flatbed scanner), and "Post voor jou" in "Te doen".

### 7.8 Data model, API, jobs

New app `apps/mail` (`models`, `services`, `recipients`, `matching` glue, `jobs`, `api`).
Tables `MailItem`, `MailItemRecipient`, `MailItemEvent`, `MailOpenPolicy`,
`MailCounter` (one row per workspace, locked for the next number), all
`WorkspaceModel` with forced RLS; `PushEndpoint` and `PushNotice` (server tables; a
`PushNotice` is resolved only for its own user, and its target is re-read under that
user's policies, so a stale notice for a letter they lost answers "Niet meer
beschikbaar").
`Membership.mail_desk`, `DepartmentMember.is_lead`, the workspace settings of 7.1.

Operations: `matchEnvelope`, `registerMailItem` (multipart: envelope image optional),
`listMailItems` (desk queue and "for me" by filter), `getMailItem`,
`getMailEnvelopeImage`, `correctMailItem` (desk: person, markers, sender), `forwardMailItem`,
`markMailCollected`, `requestMailOpening`, `markMailNotForMe`, `closeMailItem`,
`snoozeMailItem` (date or null), `listMailPolicies`, `createMailPolicy`,
`deleteMailPolicy`, `updateMemberMailDesk`, `registerPushEndpoint`,
`deletePushEndpoint`, `getPushStatus`, `resolvePushNotice`, `updateMailroomSettings`. The upload gains
`mail_item_id` (default null). Errors: `mail_item_not_found` (404, also for items the
user may not see), `mailroom_off` (409), `mail_item_closed` (409),
`mail_opening_not_allowed` (403), `envelope_too_large` (422), `push_not_configured`
(409).

Jobs: `remind_mail_items` (hourly), `remove_expired_envelopes` (daily; deletes sealed
envelope files only under the documents root through the Beheer 4.10 guards, with a
recorder in tests), `send_push`.

### 7.9 Privacy notes

Envelope data is personal data (the report's legal paragraph): the summary sentences
and `PRIVACY.md` say what the desk sees. The sender is often the most sensitive field
(GGZ, incasso, advocaat): with `category` only a small fixed list is stored, with `name`
the desk and the addressee see it, nobody else. Envelope images are deleted after
`envelope_retention_days`; back-ups keep them until their own retention removes them.

### 7.10 Tests that must exist

- ⚠ A desk member fetches the document of an item they opened: 404.
- ⚠ The desk uploads content for a Persoonlijk item without a request, with a policy
  covering everything: 403.
- ⚠ No push payload, notice, escalation text or audit row contains a sender, category,
  marker or urgency (a test over every code and every template).
- ⚠ Envelope text and image never reach the AI provider (the network test of spec 16).
- ⚠ A department lead, an admin and a read-only member see nothing of an item.
- ⚠ A `PushNotice` resolved by another user, or for a letter its user can no longer
  read, answers like an unknown id.
- A snoozed item is skipped by reminders and escalation; a forward ends the snooze.
- Two desk phones registering at once get distinct numbers.
- An item for a removed member becomes `unassigned`; a forwarded item reaches the
  colleague with its reminder.
- Envelope removal refuses a path outside the documents root (pure guard test).

### 7.11 Left to the builder, and done means

Left: the urgent-sender list's first content (data, with sources), the app's capture
ergonomics, page layout. **Done means**: an office with a desk registers ten envelopes
in a minute; Jan gets "Er ligt post voor je" in the app (with push where configured)
without sender; he scans the content, which nobody else can read; the desk opens a
Finance letter under a policy and cannot read it afterwards; at home Bert scans an
envelope for Mila in two taps.

**Sub-parts**: B1 (everything above except push) and B2 (`PushEndpoint`, Web Push, the
relay client; the relay itself lives in `Brievie_connect`).


---

## 8. Part D: folders per person and the NAS

Kept as designed; changed by this revision only where `audience` and delegation were
used (8.1, 8.4, 8.7).

This **extends Beheer 6.3** (the readable archive folder, part E there) and changes
only what is listed; everything else of Beheer 5 and 6.3 holds (one way, `ArchiveCopy`
with a hash, a copy removed only when it is still the file Brievie wrote, safe file
names, private letters only in the addressees' folders, a worker of its own).

### 8.1 Layout

`<werkruimte>/<persoon>/<jaar>/<datum> <afzender> - <soort>.pdf`. A letter without a
name gets no copy until it has one (no "Gezamenlijk" folder: post without a name is for
the sorters, 4.1). `<soort>` is the document type's Dutch name (there is no title field;
R25). Departments get a folder like persons. A letter on two names is in both. A share creates no copy: the
person it was shared with reads in Brievie.

### 8.2 Who writes, as whom

Beheer said the files are written as uid 10001 and there is no setting to write as
another user. That cannot serve a NAS whose home folders accept only the NAS user's
own uid. Replacement (R24):

- A service `archiver` (Beheer's "worker of its own"), same image, serving only queue
  `files`, runs as `user: "${BRIEVIE_UID:-10001}:${BRIEVIE_GID:-10001}"` with
  `group_add: ["10001"]`, the documents volume **read-only**, the archive folder and the
  linked folders read-write, and the master key. It exists only when the archive is
  switched on (a compose profile).
- Page files become readable for group 10001: `FileSystemStorage.write` sets mode 0640
  on the temporary file before the rename; `./brievie start` once runs, inside the
  container as uid 10001, `chmod g+r` over the documents tree (not on Synology ACL
  folders given as `BRIEVIE_DOCUMENTS_DIR`: there the text says to grant the `brievie`
  group read in File Station instead). The files are sealed, so this widens who can read
  ciphertext only.
- In the archive folders the archiver **never chmods or chowns**: it creates with
  `os.open(…, O_CREAT | O_EXCL | O_WRONLY, 0o660)` and renames, so the folder's inherited
  permissions decide. Synology's own help says that a chmod turns an ACL into Unix
  permissions; Linux's `acl(5)` says a new file takes the directory's default ACL as its
  access ACL, trimmed by the mode given at creation.

### 8.3 Linking a person's folder: `./brievie archive link "<Persoon>" <pad>`

1. The wrapper resolves `<pad>` on the host: an existing directory, not a link, not `/`,
   not a system directory, not the Brievie checkout or a Docker volume; refuses
   otherwise (the guards of Beheer 4.10, as a pure function first).
2. It runs `manage.py archive_link --person "<Persoon>" --slug <slug>` in the `admin`
   container, which finds exactly one person by name in the given workspace
   (`--workspace` when there are several), records `ArchiveLink(workspace, person, slug,
   created_by="command line")` and an audit row `archive_link_created`.
3. It writes `deploy/compose.archive.yml` **whole**, from all recorded links: one bind
   mount `<pad>:/data/archive-links/<slug>` per link on the `archiver`. The user never
   edits compose; `./brievie` passes the override file with `-f` from then on.
4. It proves writing: a one-off archiver container writes and then removes
   `.brievie-schrijftest` (fixed name, the only file the command removes), and reports
   "Brievie kan schrijven in <pad>" or the exact sentence of what failed (uid, gid,
   permissions).
5. `./brievie archive unlink "<Persoon>"` removes the mount and the record; copies stay.

The person concerned sees the link on "Wie kan mijn post zien?" and gets a notice; a
person with an account can switch off copies of their own post ("Geen leesbare kopie
van mijn post"), which wins over the admin's setting (R26). Removing the copies Brievie
wrote is a separate, explicit action ("Verwijder de kopieën die Brievie schreef", typed
`VERWIJDER`), only files whose hash matches `ArchiveCopy`, through descriptors, inside the
fixed list.

The fixed allow-list in code (Beheer 4.10 rule 2) gains `/data/archive` and
`/data/archive-links/<slug>` (one level, slug `[a-z0-9-]{1,40}`), in the commit that adds
the code that deletes there, with its test.

### 8.4 When a letter is withdrawn

A copy is removed (if it is still Brievie's file) when: the letter becomes private and
the folder is not an addressee's; an addressee is removed; the person switches off copies (only
on the explicit removal action). Removing a member, switching departments off or unlinking a
folder removes nothing: the text says so. A copy the user changed is left, and the
admins get a notice that it is still there.

### 8.5 The warning (web, when switching on)

"Brievie zet dan een leesbare kopie van elke brief in de map van die persoon. Die kopie
is **niet versleuteld**. Wie de map kan openen, kan de brief lezen, ook het BSN op de
brief. Hoe goed de map is afgeschermd, regelt je NAS, niet Brievie. Brievie schrijft
alleen; wat je in de map verandert, komt niet terug in Brievie."

### 8.6 Linux and NAS guidance, and what is proven

- **Linux** (in `SELFHOST.md`): per person folder owned by the user with group
  `brievie` and setgid, plus default ACLs:
  `chown anne:brievie <map> && chmod 2770 <map> && setfacl -m u:<BRIEVIE_UID>:rwx -m
  d:u:<BRIEVIE_UID>:rwx -m d:u:anne:rwx -m d:g:brievie:rwx <map>`. Default-ACL
  inheritance and the trimming by the creation mode are documented in `acl(5)`; the
  exact commands are **to prove** in a container with a throw-away volume.
- **Synology**: Synology's File Station help documents applying a permission to "this
  folder, child folders, child files"; it does not document whether a file created by a
  container under a given uid inherits it. **To prove on a real NAS**: Beheer 6.3a's
  list, plus: the uid and gid of a DSM user `brievie` (`id brievie` over SSH) as
  `BRIEVIE_UID/GID`; a file created by the archiver in `homes/anne/Brievie` is readable
  by anne over SMB and not by another user; the ACL is still an ACL afterwards.
- **NFSv4 ACLs** (TrueNAS, some QNAP): inheritance flags on the directory entry
  (file-inherit, directory-inherit). **To prove.**

Nothing about NAS behaviour goes on the site until the owner's Synology run, which also
settles whether Brievie runs on the Synology itself or beside it (section 12, note).

### 8.7 Tests and done

Recorder tests for every destructive primitive (Beheer 4.10 rule 6); the link guard as
a pure function over paths (`/`, `~`, a link, `/var/lib/docker/...`, the checkout); the
override file generated from records is byte-identical on a second run; a private letter
never lands in a non-addressee's folder; removing an addressee removes that copy. **Done means**: on the owner's Synology, `./brievie archive link "Anne"
/volume1/homes/anne/Brievie` makes Anne's letters appear in her home folder, readable by
her and not by others, and a letter made private disappears from a non-addressee's folder.


---

## 9. Part E: out of scope, named so they are not lost

**Scan-in folder.** A network scanner's "scan to folder", per person a folder
(`BRIEVIE_SCAN_IN_DIR/<persoon>`), read by the worker; `Document.Source.WATCH_FOLDER`
already exists. Files are taken over and then removed from the scan folder, which makes
it a destructive path under Beheer 4.10 (its own allow-list entry). The scanner's
folder decides the addressee; nobody is its scanner, and the letter is visible by the
rules of 4.1.

**Relation dossier (business).** Match a letter's sender to a customer or supplier on
hard numbers (KvK, VAT number, IBAN with its mod-97 check), the name as fallback, always
confirmed by a user (a `DocumentSuggestion` kind); one dossier per relation (quotes,
contracts, letters, reminders, paper invoices); a retention period per document with an
overview of what may go (needs deleting documents, phase 2); terms from quotes and
contracts. No bookkeeping, approvals, payments or Peppol (the report's advice). Visibility
of a dossier is the visibility of its letters.

**Digital mail in.** A PDF by e-mail to a mailbox Brievie reads (IMAP, spec 8.2) or
forwards from Connect, and the Berichtenbox notification mails turned into tasks. Each
arriving document needs an addressee rule (mailbox per person or matching), and until it
has one it is unassigned and goes to the sorters (4.1); in an organisation a mailbox
should map to a person or department.

**"Weergave": a thumbnail grid.** A per-user setting "Weergave: lijst / tegels" for "Alle
post" and "Te doen", with tiles like the PostNL app's. The list stays the default. It
needs a small thumbnail per first page made by the server at intake (a few kilobytes,
**sealed** like the pages, under the same child policy as `DocumentPage`, never cached
outside the app's memory); with the mailroom the envelope photo is the tile until the
content is scanned, and is shown only to those who may see the item. A thumbnail of a
page shows what the page shows (also a BSN), so it is a page, not an icon.

**Delegation ("meekijken").** Member A reads or handles the post on person B's own
name for a period, always visible to B, with Front's clean-up on ending (T). Removed from
this revision (4.20); departments, "Delen met" and, at home, the admins' sight cover
the cases seen so far. Comes back only when a customer's absence case is not covered.

**Several servers in the iOS app.** Section 5.3.

**SCIM, back-channel logout, LDAP.** Section 6.9.

---

## 10. Threats and abuse

| Threat | What stops it, or what stays |
|---|---|
| **Curious admin in an organisation** | "Beheerders lezen alle post" is off; switching it on, or making someone admin while it is on, needs confirmation and tells every member (4.4); adding oneself to a department tells its members; flagging oneself as sorter tells the other admins and shows on everyone's page; adding oneself to a department with private post is refused; emergency access needs a reason, lasts an hour, is read-only and tells the person; account actions on members are the server admin's (4.19); the audit log is insert-only for the app. **Stays**: an admin can turn a switch on and read; the point is that everyone sees that it happened. Two admins acting together are seen, not stopped. |
| **Server admin with a shell** | Reads everything (database, master key, back-ups). Said on every member's page. The full export with others' private post exists only on the command line and tells every member. |
| **Member reading a colleague's post** through lists, search, export, filters, tasks, counts, rule tests, labels | Every path of 4.10 goes through one policy; the leak suite and the transition test cover them. |
| **A switch flipped by mistake** | Preview before, confirmation when anyone gains, notices to those affected, nothing deleted, switching back restores the earlier sight (tested). |
| **A letter nobody sees** (unassigned in an organisation, a department without members, departments switched off) | Rule 4 sends it to the sorters, and the admins sort when nobody does (tested as a property). |
| **Directory misconfiguration or a hostile directory admin** | Only mapped group ids count; a claim never gives admin, sorting or mailroom; a missing claim changes nothing; additions need a fresh sign-in; every change is an audit row and a notice to the user. **Stays**: whoever controls the directory decides department membership; that is the point of connecting it, and the "Wie kan mijn post zien?" page says "komt uit Microsoft 365". |
| **Someone who left keeps a phone** | Removing the member in Brievie revokes at once; otherwise the 14-day re-confirm (6.4). **Stays**: up to 12 hours on the web and 14 days on a phone when the admin only disables the directory account; said in the guide. |
| **Token from another provider** | One provider per server; issuer validated before any claim is read (D 9.2); mappings keyed by issuer. |
| **Mailroom employee** | As before (part B): envelope data only, never content scanned through the item, Persoonlijk/Vertrouwelijk never without a request. A reception phone is a dedicated account with `mail_desk`, role read-only, no sorting flag: it reads no letters. **Stays**: Brievie cannot stop someone opening paper. |
| **Stolen phone** | App lock, revocable device token, no document cache, push without content. **Stays**: calendar events with task text until the app syncs. |
| **Prompt-injected letter** | The AI only proposes from closed lists; it never decides visibility, departments, sorting or private; automatic rules never add an addressee to a private letter. **Stays**: a wrong addressee proposal for a non-private letter, accepted by a human. |
| **A restore that brings back old rights** | After every restore: a server audit row, an admin banner "Controleer wie wat kan zien" (switches, admins, sorters, departments, mappings, emergency, `mail_desk`, archive links) until a server admin confirms it, and a notice to every member (R28). |
| **Hostile QR code (v2)** | As before (4.19). |

## 11. Rulings

### 11.1 Kept from the previous revision

| # | Ruling | Note |
|---|---|---|
| R3 | "The admin reads everything at home" never includes private post | D decision 1 |
| R4 | The mailroom is a flag on the membership, shown as a role | part B |
| R6 | An empty actor sees no letter | fail closed |
| R7 | A manual rule run runs as its starter | |
| R8 | The full copy with others' private post only on the command line, with a notice to every member | |
| R10 | Account actions by a workspace admin need: single membership, no private reader, and the admin reads all post | condition 3 reworded (4.19) |
| R11, R12 | App-only accounts; QR payload version 2 | 4.19 |
| R16–R26 | Mailroom, push and archive rulings | parts B and D; R21 now uses part A's leads |
| R28 | After a restore: audit row, admin checklist, member notice | the checklist lists this revision's settings |
| R29 | The role `viewer` is shown as "Alleen lezen" | "meekijken" is no longer used at all |
| R30 | Lessons from ticket systems | minus the delegation rows (delegation is later); plus RA's lessons |

### 11.2 Withdrawn

R1 (a per-member scope read by a function; replaced by R31), R2 ("gezamenlijk" and the
organisation scope `own`; replaced by R32, R34), R5 (reader rows for every addressed
letter; replaced by R33), R9 (widening a scope notifies everyone; replaced by R37), R13
(the scanner share and `scanner_keeps_access`; replaced by R38), R14 (delegation start
dates; delegation is later), R15 (letterless tasks per scope; replaced by R39), R27 (the
wizard; replaced by R40).

### 11.3 New

| # | Ruling | Why | Cost if wrong |
|---|---|---|---|
| R31 | Sight of non-private post is set by four workspace settings and one member flag, read by SQL functions from their tables | one rule per workspace is what small teams use (RA 1); no code can set a wrong value | an organisation that wants one member to see one other person's post uses a share per letter, or a department of two |
| R32 | Two positions, no third ("own and shared" is gone) | a household that turns "Iedereen" off keeps the parents as admins who see post without a name; children do not | a household where an adult non-admin should see shared post makes a group (department) for it |
| R33 | Non-private sight comes from `Document.addressee_ids` and live functions; reader rows only for private letters, shares and emergency | linking a person or changing a department touches no letter; far fewer rows | one derived column with a nightly check; measured before building (4.9.5) |
| R34 | Unassigned = no addressee anybody reads through; it goes to the sorters, or to the admins when nobody sorts | never to everyone (Paperless trap, RA 5), never to nobody | in an organisation that does not name sorters, the admins read unassigned post (said on the page) |
| R35 | Departments switched off are kept and inactive; letters only on departments go to the sorters; rules keep writing department links, marked on the rule; private letters untouched | nothing lost, switching back restores; private stays private under every setting | while off, the sorters see department post that has no person on it |
| R36 | A letter may be on several departments (and persons), at most 10 addressees as today | letters are addressed to several parties; the model already allows it | reports count a letter once per department |
| R37 | A change in which anyone gains sight needs confirmation and notifies those who gain; admins gaining all post notifies every member; narrowing previews and notifies those who lose | the owner asked for safe switching both ways; visibility to the people concerned | one more dialog and some notices |
| R38 | The scanner sees a letter until it is done; no setting; the hand-over sentence; the final confirm write as the system | the reviewer must see what they review; no setting to explain | a receptionist cannot look back at a letter she scanned for someone else |
| R39 | Letterless tasks: visible to those who see all post, the assignee and the maker | a manual task can be as sensitive as a letter | old letterless tasks without a maker are hidden from plain members once "Iedereen" is off |
| R40 | One choice at installation, the workspace kind; no environment variable; the "Afdelingen" field leaves the form; a second workspace by `manage.py create_workspace` | the owner's decision; the choice belongs with the first account | an unattended provider install uses the command, not an env var |
| R41 | Switching the preset later changes the kind and offers the other values with checkboxes; it never removes labels or rules | presets are a starting point, not a mode | an admin may keep old values by unticking |
| R42 | Department labels: an optional `Label.department`; names unique per workspace; labels never grant sight | Front and Paperless: tags follow item access (RA 3) | names need a prefix when two departments want "Facturen" |
| R43 | Delegation is later | departments, shares and the admins' sight cover the known cases; four levels were the second-hardest thing to explain | a carer for a colleague's own-name post uses shares per letter |
| R44 | A share works on every letter | the everyday exception in an organisation | share rows on non-private letters |
| R45 | Emergency access stays, covering all post of the person the admin cannot see; Organisatie on, Thuis off | a traceable way in when someone is ill | as D 8.5 |
| R46 | A department is managed by the directory or here, never both | a manual change would be undone at the next sign-in | an organisation that wants an exception adds a share, or a manual department |
| R47 | Directory: exact claim values per issuer; recognised groups only; never admin, sorter or mailroom; a missing or overage claim changes nothing and warns; removals from a mapping change apply at once, additions at the next sign-in | RA lessons 7 and 8; Paperless's admin lock-out warning | a user removed in the directory keeps sight until sign-in or re-confirm while the claim is missing |
| R48 | No LDAP; SCIM and back-channel logout later | RA 8, 9 | AD-only customers need Entra Connect or Keycloak in front |
| R49 | While a mapping exists, the device re-confirm default is 14 days (minimum 7) | phones do not sign in through SSO | phone users sign in on the web every two weeks |
| R50 | Keep the word "werkruimte" | renaming costs every string for a word few see | none known |
| R51 | A department is called "Groep" in a Thuis workspace | "afdeling" is odd for a family | two words for one object; the glossary records it |
| R52 | An auto-created SSO account may start with the role a mapping gives (`member`), instead of always `viewer` | the mapping is the admin's explicit choice; extends D decision 3 | an admin who maps a role by mistake gives members' rights to a directory group |

## 12. Questions only the owner can answer

The owner delegated the choices; these three are where a wrong guess is expensive. Each
has the option this design takes meanwhile.

- **Q1. Directory features: open core or paid tier?** Group mapping (section 6), and later
  SCIM, are what helpdesks sell as enterprise features (RA 10); the competitor report puts
  the earning model with organisations. *Meanwhile:* built in the open core, in its own app
  `apps/directory`, so it can move to a paid layer without touching part A.
- **Q2. Talk to five to ten offices before building the mailroom?** The report advises it,
  and part B1 is L. *Meanwhile:* part B waits for those conversations; parts A, A3 and C2
  do not depend on it.
- **Q3. The push relay for the iOS app: free for every server, part of a paid Connect, or
  not offered?** *Meanwhile:* B2 waits; the app uses local notifications and background
  refresh.

Note, not a question: part D's proof needs the owner's own Synology run, which also settles
whether Brievie runs on the Synology itself or beside it; the manual Entra check (6.8)
needs a Microsoft account, a phone number and a bank card, and about an hour.

## 13. Build order and sizing

Relative size: S ≈ 5 tasks, M ≈ 10–15, L ≈ 25–30 (one task as in the 1d plans).

| Order | Part | Size | Ships alone? |
|---|---|---|---|
| 1 | **A1** actor, helper functions, the three switches and the sorting flag (fields only), `addressee_ids`, policies, migration, measurement (PB B1 amended) | M | yes: every workspace migrates to "Iedereen", nothing visible changes |
| 2 | **A2** private post, shares, departments with leads and labels, emergency access (PB B2–B4 amended); "Wie ziet wat" with preview and the transition test; sorters; Te doen wording; "Wie kan mijn post zien?"; the setup form and `/ingesteld` | L | yes, with A1 |
| 3 | **A3** invitation with device (v2 QR, app-only accounts), host + iOS | M | yes, after A1 |
| 4 | **C1** OpenID Connect sign-in and second factor (D part C, unchanged) | as D | yes |
| 5 | **C2** directory groups to departments, Test inloggen, provider guides, the Keycloak CI job; then the manual Entra check | M | after A2 and C1 |
| 6 | **B1** mailroom without push | L | after A2 and Q2 |
| 7 | **B2** push endpoints, Web Push, relay client | M (+ relay S–M in `Brievie_connect`) | after B1; depends on Q3 |
| 8 | **D** archive extension, after Beheer part E | M on top of E | after E and A2 |
| later | C3 SCIM and back-channel logout; delegation; several servers in the iOS app (M–L, iOS only) | | |

**How much smaller part A is.** The previous revision's A1 (L) + A2 (L) + C, the wizard
(M), came to roughly 65–75 tasks; this revision's A1 (M) + A2 (L) is roughly 37–45, about
40 % less, and the riskiest piece (reader rows for every letter, kept in step with every
person, department and delegation change) is gone. A3 is unchanged. C2 is new work (M)
that the owner asked for.

## 14. Lines for `docs/GLOSSARY.md` and `docs/DECISIONS.md`

Glossary: iedereen ziet alle post → `Workspace.everyone_sees_all`; afdelingen gebruiken
(Thuis: groepen gebruiken) → `departments_enabled`; afdeling, groep → department
(`Person.kind = department`); leidinggevende van een afdeling → department lead
(`DepartmentMember.is_lead`); beheerders lezen alle post → `admins_read_all`; verdeelt
post → sorter (`Membership.sorts_post`); post zonder naam → unassigned post; naam kiezen,
toewijzen → assign; delen met → share; organisatie → workspace kind `business`; zo is
Brievie nu ingesteld → setup summary; directorygroep → directory group
(`DirectoryMapping`); test inloggen → test sign-in; alleen lezen (rol) → viewer;
postkamer → mail desk; poststuk → mail item; envelop → envelope; kenmerk → marker;
volgnummer → mail number; openbeleid → mail open policy; leesbare kopie, archiefmap →
archive copy, archive folder.

Decisions (one line each when built): R31, R33, R34, R35, R38, R40, R46, R47, R48; the
Keycloak CI image (a test service, not a runtime dependency); no new runtime dependency in
A or C2 (`ArrayField` is Django's PostgreSQL contrib); B2's Web Push library (to be chosen
then).
