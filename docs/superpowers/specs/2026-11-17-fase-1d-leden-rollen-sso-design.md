# Phase 1d "Leden, apparaten, rollen en SSO": design

Date: 2026-11-17. Covers `Brievie_host` (backend and `web/`) and `Brievie_ios`.
The owner delegated the remaining choices on 2026-11-17 ("Regel dit allemaal, zoek dit
van tevoren uit wat hier het beste voor is"): every choice below that is not in section 2
is the designer's, made after reading the sources named with it, and marked as such.
An independent reader checked the document against the code; section 20 lists what
changed because of it. Builds on phase 1c
(`docs/superpowers/specs/2026-11-16-fase-1c-ordenen-design.md`).

## 1. Intent

Until now a server has one account. Phase 1d makes Brievie something several people
use together, and something an organisation can run:

- adding a person or a phone must be obvious at home: a link or a QR code, no settings;
- the same core must carry a business: roles, post that only the right people see,
  departments, single sign-on, an audit log.

The owner's direction: one product. Every function has a simple default and an optional
advanced layer on the same core. Nothing of the advanced layer may be required at home
or stand in the way there.

## 2. Decisions taken with the owner (2026-11-17)

1. **Private means private.** Post that is private for somebody else cannot be read by
   the admin. Per workspace an "emergency access for the admin" can be switched on:
   opening is then possible, each time with a reason, in the audit log and visible to
   the person concerned. Default off for Thuis, on for Zakelijk.
2. **A removed member's post stays.** The person remains as a person without an
   account; post and tasks stay in the workspace, devices are revoked at once, assigned
   tasks become unassigned, private post stays locked. Before leaving, a member can
   export their own post. Inviting them again links everything back.
3. **SSO: invite first.** With SSO on, only somebody who was invited or is already a
   member gets in; the SSO identity is linked at the first login. As an option the
   admin can switch on automatic creation for an email domain, with a fixed first role
   (viewer).
4. **Second factor in 1d, optional.** Every user can add an authenticator app (TOTP
   with recovery codes); the server admin can require it. At home nobody has to do
   anything. With SSO the identity provider does the second factor. Passkeys later.

## 3. Goals, non-goals, anti-goals

**Goals**

- G1. Invite a member with a link or QR code; invitations expire and can be revoked;
  the invitee chooses a password (or signs in with SSO); remove a member.
- G2. Devices: pairing stays QR; an overview per user; revoke by the user and by an
  admin; a device token that stops working when it is no longer used.
- G3. Roles admin, member, viewer, and a server admin for what belongs to the server.
- G4. Post that is private per letter, "Zorg is private by default", departments as
  the business counterpart of persons, enforced by row-level security, not only by
  the API.
- G5. Single sign-on through OpenID Connect as an opt-in.
- G6. What an organisation asks for before it trusts a product: an audit log, session
  management, a second factor.
- G7. Additive contract: a 1c app works against a 1d server and a 1d app against a 1c
  server.

**Non-goals for 1d**

- SAML, SCIM provisioning, back-channel logout, several identity providers at once.
- Sending mail (invitations, password resets, notifications). Links are shown to the
  admin, who passes them on. SMTP can be added later behind the same flows.
- Passkeys (WebAuthn).
- Retention periods and deleting documents (section 12).
- Back-up and restore (section 12).
- Sign-in with SSO inside the iOS app (section 9.5).
- Remote wipe of a phone.
- A viewer limited to certain folders (spec 8, Zakelijk): folders do not exist yet.
- Self-registration without an invitation.

**Anti-goals** (would make 1d a failure even if it works)

- Private post that leaks through a list, a count, a search, a rule test, a background
  job's log line, the history of another document or a suggestion.
- An admin who can read private post without a trace.
- A home user who has to understand roles, departments, SSO or a second factor to add
  their partner.
- An SSO setup that can lock everybody out when the identity provider is down.
- An invitation or reset link that still works after use, after expiry or after it
  was revoked; or whose secret ends up in a log.
- A removed member whose phone keeps reading post.

## 4. Constraints

Everything in `CLAUDE.md` and `docs/DECISIONS.md` holds: forced row-level security on
workspace tables, additive API with primitives-only nullable fields and defaults on
added fields, error body `{"code", "message"}`, thin endpoints, no document content,
tokens or passwords in logs, secrets stored only as hashes or sealed, Dutch B1 text,
`mypy --strict`, tests first, no dependency without a decision line.

Facts of the current system that shape this design:

- **Accounts belong to the server**, not to a workspace (`accounts.User`); a user can
  have memberships in several workspaces with different roles. So sign-in settings
  (SSO, second factor, sessions) are server settings, and removing a member is not the
  same as removing an account.
- **The server cannot send mail.**
- **Row-level security isolates workspaces only.** Inside a workspace every member
  sees every row.
- **Device tokens never expire** and there is no second factor.
- **The home server usually lives on a `.local` name with its own certificate
  authority.** That limits what SSO can do there (section 9.6).

## 5. Approaches considered

### 5.1 Who may see a private letter: where it is enforced

**A. In the API only.** Every query adds a filter. Simple, but one forgotten filter in
a list, a count, a rule test or a job leaks. Rejected: the same reason workspaces use
row-level security.

**B. Row-level security with a policy that joins through addressees, persons,
departments and shares.** Correct by construction, nothing to keep in sync. Rejected
because it cannot work: those tables are themselves under policies that must check
whether their document is visible, and a query through policies that refer to each
other in a circle fails in PostgreSQL ("infinite recursion detected in policy"). A function that bypasses row-level security would need a role with
`BYPASSRLS`, which the application's roles deliberately do not have (the only
such role is the read-only one of the back-up design).

**C. Row-level security with a small table of readers (chosen).** `DocumentReader`
holds, per private document, the users who may read it. The document's policy checks
that table; the policies of everything that hangs on a document check the document.
There is no circle. The price is a derived table that must be recomputed when the
inputs change; one service owns that, in the same transaction as the change, and a
nightly job verifies it (section 8.3).

### 5.2 Departments

**A. A separate `Department` concept** next to persons, with its own links on
documents, its own rule action and its own name matching. Twice the surface.

**B. A department is a kind of person (chosen).** `Person` gains a `kind`
(`person` | `department`); a department has members. "Op naam van Finance" is then the
same addressee link, the same rule action, the same suggestion, the same filter that
1c built. The web calls them "Afdelingen" in a Zakelijk workspace and hides the kind in
Thuis.

### 5.3 SSO protocol and library

OpenID Connect first (Entra ID, Google Workspace, Keycloak, Authentik all speak it);
SAML is left out: it needs XML signature handling, a much larger attack surface, for
providers that all offer OIDC too. Client library, compared from their own documentation on 2026-11-17 (notes:
`onderzoek-oidc-tunnels.md`):

| Option | Finding | Verdict |
|---|---|---|
| Authlib 1.9.x (BSD-3) | discovery, PKCE (S256), ID-token validation; released almost weekly; the sign-in and callback views and the link to our user are ours to write | **chosen** |
| mozilla-django-oidc 5.0 (MPL-2.0) | supports Django up to 5.2, no discovery, no custom CA bundle | rejected: not on Django 6 |
| django-allauth 65 (MIT) | generic OIDC, and a second-factor module | rejected: wants to own the whole sign-in flow, which collides with the own user model and the Ninja endpoints |
| joserfc or PyJWT, hand-written flow | everything ours | rejected: token validation is the kind of code that is wrong in ways tests do not show |

Not confirmed from Authlib's documentation and therefore the first task of part C's
plan: nonce handling in its Django client, a custom CA bundle, Django 6. Cost if the
choice is wrong: the library sits behind one module (`accounts/oidc.py`); replacing it
touches that module and its tests.

The second factor (TOTP, RFC 6238) is small and fully specified with test vectors: it
is written with the standard library, no dependency. `django-otp` was considered and
left out (no Django 6 support stated; it brings its own device models). Replay
protection, rate limiting and sealing the secret are ours either way.

### 5.4 One phase or several

The work falls into three parts that each stand on their own and are delivered in
this order (section 13): members and devices; private post and departments; SSO and
the second factor. The audit log is in the first part because every later part writes
to it.

## 6. Members

### 6.1 Roles

| Role | Scope | May |
|---|---|---|
| Server admin | the server | server settings (SSO, second factor, sessions, addresses), create a workspace, see the server audit log, account actions (6.3). Inside a workspace a server admin has exactly the role of their membership there, nothing more |
| Admin (`admin`) | one workspace | invite, change roles, remove members, revoke members' devices, manage rules, see the workspace audit log, workspace settings |
| Member (`member`) | one workspace | scan, review, organise, manage persons and labels |
| Viewer (`viewer`) | one workspace | read; no changes (as in 1c) |

`User.is_server_admin` is new; the first user gets it at setup, and a data migration
gives it to the admin of the oldest workspace on existing servers. A server admin is
not automatically a member of every workspace: they do not see post of a workspace
they are not in. There is always at least one server admin and every workspace always
has at least one admin: the last one cannot be removed or demoted (409
`last_admin`).

At home this table never shows. The first person is admin; whoever they invite becomes
a member; "Rol" is a choice with three plain sentences that only appears on the
member's own row.

### 6.2 Inviting

`Invitation` (not a workspace table: it is looked up before anybody is signed in, like
`PairingCode`):

| Field | Notes |
|---|---|
| `workspace` | the workspace the invitee joins |
| `role` | `member` by default |
| `person` | optional: "this is Papa": the existing person the new account is linked to. Only a person without an account link |
| `email` | optional: when set, only an account with this address can accept. Required when `person` has private post, because accepting then gives access to it |
| `token_hash` | SHA-256 of `secrets.token_urlsafe(32)`; the token itself is shown once |
| `expires_at` | 7 days by default; the admin can choose 1 day or 30 days |
| `created_by`, `accepted_by`, `accepted_at`, `revoked_at` | |
| `note` | optional name for the admin's own overview ("Anne"), never shown to others |

- **The link** is `https://<server>/uitnodiging#<token>`. The token sits in the URL
  fragment, which a browser never sends to a server, so it cannot land in a proxy or
  server log; the page reads it and posts it in a request body. The QR code holds the
  same link.
- **Opening it** shows the workspace's name, who invited and the role. The invitee
  then either creates an account (name, email address, password with the existing
  rules), or signs in to an account they already have on this server (they then join
  a second workspace), or, with SSO on, continues with the identity provider.
  A deactivated account (a member who was removed earlier) comes back the same way:
  by signing in to it with its password, which reactivates it; nobody gets an old
  account by knowing its address.
- **Accepting** is one transaction: the membership, the link to the person (or a new
  person, as 1c's `ensure_person_for_member` does), the invitation marked used, an
  audit event. A used, expired or revoked link answers the same sentence ("Deze
  uitnodiging werkt niet meer. Vraag een nieuwe."), so a link reveals nothing.
- **Single use**, rate limited per client address like pairing.
- **Managing**: the page "Leden" lists members and open invitations; an invitation can
  be revoked or made again (a new link; the old one stops).
- **No mail.** The admin passes the link or shows the QR code. The page says when the
  link only works on the home network (every public URL of the server is a `.local`
  or private address).
- **Second factor.** Accepting an invitation or completing a reset creates a session
  through the same code as signing in, so the second step or the required set-up of
  section 10 applies there too.
- **Straight into the app.** After accepting on a phone the page offers "Open de
  Brievie-app": a `brievie://server#<addresses and CA fingerprint>` link and, on the
  page, the eight-character pairing code to type into the app. The link carries no
  secret: another app on the phone that claims the URL scheme learns only the server's
  address. The app shows the server's name and asks for the code. On a computer the
  page shows the pairing QR code instead.

### 6.3 Changing and removing

- **Role**: an admin changes a member's role; the member sees it at their next
  request; an audit event.
- **Removing a member** (admin), in one transaction: the membership is deleted; the
  person stays and loses its account link; tasks in this workspace assigned to the
  user become unassigned; the user's reader rows, shares and department memberships in this workspace go
  (section 8); when the user has no membership left on the server, the account is
  deactivated, its sessions end and its devices are revoked. The service does these
  writes as the system (8.3), because the admin cannot see the member's private rows
  and would otherwise skip them silently. A user who is still a member elsewhere
  keeps their account and devices; their phone simply no longer lists this workspace.
- **Leaving** (the member themselves): the same, started by the member.
- **Private post of a removed member stays locked**: nobody reads it, unless emergency
  access is on (section 8.5). Inviting the person again with "this is <person>" makes
  them the reader again.
- **Own export before leaving**: "Mijn post exporteren" builds, in a background job
  that runs as that user (so it contains only what they may read), a ZIP with the
  page images and a JSON file of every document on the member's name; the file is
  sealed on disk, downloadable by that member for 24 hours, then removed. It must be
  made and downloaded before leaving: the leave screen offers it, and after leaving
  there is no account to download with. In a Zakelijk workspace it is off unless the
  admin allows it (post on an employee's name is the company's). The export uses the
  same building block as the server-wide export of the back-up design.
- **Password**: a user changes their own password (all their other sessions end).
- **Account actions** (a reset link, clearing a second factor, ending sessions,
  seeing and revoking devices) work on the account, which belongs to the whole
  server. So: a server admin may do them for anyone; a workspace admin only for a
  member whose single membership is that workspace and who is not a server admin.
  A reset link has the mechanics of an invitation (one use, 24 hours, token in the
  fragment). Each of these actions is an audit event and a notice the member sees at
  their next sign-in ("Je wachtwoord is op 3 mei opnieuw ingesteld door Anne."), so
  an admin cannot take over an account unseen. A server admin who lost their own
  password uses the management command `reset_password` on the server.

### 6.4 API (additive)

`listMembers` exists since 1c. New, under `/workspaces/{workspace_id}`:
`createInvitation`, `listInvitations`, `revokeInvitation`, `updateMemberRole`,
`removeMember`, `leaveWorkspace`. Account actions under `/users/{user_id}`:
`createPasswordReset`, `clearSecondFactor`, `endUserSessions`, `listUserDevices`. Without a session:
`getInvitation` and `acceptInvitation` (token in the body), `completePasswordReset`.
For the signed-in user: `changePassword`, `createMyExport`, `getMyExport`.

## 7. Devices and sessions

### 7.1 Devices

- **Pairing stays QR** (and the pairing link of 6.2 on the same phone). A pairing code
  is made by a signed-in user for their own account.
- **Overview**: "Apparaten" lists the user's own devices with name, app version, first
  paired and last seen. An admin sees, per member, the same list.
  `Device` gains `app_version` (sent by the app at pairing and refreshed from a request
  header at most once a day).
- **Revoking**: by the user themselves (exists) and by an admin under the rule for
  account actions (6.3). A device belongs to the account, so it stops for every
  workspace; the member pairs again. Both write an audit event.
- **A token that is not used stops**: 180 days after it was last used. The date is
  computed from the existing `last_seen_at` when the token is checked; there is no
  second column and no extra write. A device that was never seen counts from its
  pairing. An expired token answers exactly as a revoked one (401, the same code), so
  a 1c app handles it; the app then shows pairing. Devices paired from an SSO session
  have one more rule (9.4).
- **What a revoked phone still has.** The revocation takes effect at the phone's next
  request. The app then clears the pairing and the token from the Keychain (1b). What
  stays on the phone: scans that were not uploaded yet (they wait for a new pairing
  with the same server, or can be deleted), and the events in the phone's calendar
  with the task's text and sender (the app can no longer remove them). The app keeps
  no copy of documents (there is no document cache), so nothing else is readable.
  The design does not promise a remote wipe.

### 7.2 Sessions

`UserSession` records each web session: the session key (Django's own session table
already holds it in plain form, so hashing it here would add nothing and would make
ending a session impossible), how the user signed in (password or SSO), the user, when it
started, when it was last used, the client address and a short description of the
browser. "Waar je bent ingelogd" lists them; a user ends any of them (both rows are deleted); changing the
password or switching on the second factor ends all others; an admin can end all
sessions of a member (it happens anyway on removal).

Session lifetime is a server setting with a home default: 14 days of inactivity, at
most 90 days. An organisation shortens it.

## 8. Private post, departments and how the database enforces it

### 8.1 The model

- `Document.visibility`: `workspace` (default: everyone in the workspace, as in 1c) or
  `restricted`.
- A restricted document is readable by its **readers**:
  1. every user linked to a person the document is on the name of;
  2. every member of a department the document is on the name of;
  3. every user it was explicitly shared with (`DocumentShare`), which includes, by
     default, whoever scanned it and whoever made it private (also by confirming a
     letter that becomes private). That is the spec's example: a letter for mother,
     scanned by her son, private for the two of them. Anybody who can read the
     document can remove a share, their own included.
  Only current members of the workspace count as readers.
- **Who may make a letter private**: a member who can read it.
- **Who may make it public again**: a reader who is an addressee (linked to the
  person, or a member of the department). When no addressee has an account, any
  reader. So the son cannot open his mother's letter to everyone once she has an
  account. It is an audit event and a line in the document's history.
- **Nobody gives themselves access.** A link between a person and an account, and a
  department membership, are access grants. They are made by an admin, never for the
  admin's own account when that person or department has private post (409
  `cannot_grant_self`; the invitation flow and emergency access are the ways), and a
  person that is linked can be unlinked only by removing the member or by that member.
  Each grant is an audit event and a line in the history of every private letter it
  opens, where its readers see it.
- **Automatic actions do not widen the readers.** The pipeline and rules (automatic
  or run by hand) do not add an addressee to a letter that is already private; they
  leave a suggestion, which only its readers see and can accept. Labels can still be
  added.
- **"Zorg is private by default"** (spec 14): a workspace setting, on for Thuis and
  off for Zakelijk. When the label with system key `zorg` comes onto a document (by
  hand, by accepting a suggestion or by a rule), the document becomes restricted. A
  proposal for that label does not make it private; the label itself does. The user
  sees it at once: a lock with "Privé voor <namen>".
- **A rule action "privé maken"** is added to the rules engine as a new action type
  in the existing format version (raising the version would make the parser skip
  every existing rule). It is allowed only in a rule that runs automatically, not in
  one that proposes: there is no "proposal to make private". A 1c web or app that
  edits such a rule must send the action back unchanged; the contract test covers it.
- **Tasks** of a restricted document are restricted with it. The calendar on the
  phone only gets tasks the user can read.

### 8.2 Departments

`Person.kind` is `person` or `department`. A department has members
(`DepartmentMember`: department, user). Everything 1c built for persons works for a
department: on whose name, name variants ("Afd. Finance", "Crediteuren"), rules,
suggestions, the filter in web and app. A restricted letter on a department's name is
readable by its members. Thuis hides the kind; a later "groep" (for example "Ouders")
can use the same thing.

### 8.3 Row-level security

A second transaction-local setting joins `brievie.workspace_id`:
`brievie.actor`, which is the user's id for a request, `system` for code that must
see everything, and empty otherwise. `workspace_member` sets the user (also on the
device-token path). `workspace_context` takes the actor as an argument without a
default and restores the previous one on exit, as it does with the workspace; it is
used in requests and commands too, so "job" does not imply "system". A job that works
for a user (the export) runs as that user. Inside a request, `system_actor()` is a
narrow context manager for the few services that must write rows the user cannot see;
it is used only in `documents.visibility`, in removing a member, in the deletes
named below (always after the authorisation check), and in the archive-folder job of
the back-up design. A test lists its callers. The policies read the setting
through two helper functions (`brievie_actor_id()`, null for `system` or empty, and
`brievie_is_system()`), because a cast of the text `system` to a uuid fails and
PostgreSQL does not promise the order in which it evaluates an OR.

`DocumentReader(workspace, document, user, reason, expires_at)` holds one row per
reader of a restricted document; `reason` is `addressee`, `department`, `share` or
`emergency`.

Policies:

- **Document**: the existing policy (the workspace matches, for reading and writing)
  stays as it is. Visibility is a second, **restrictive policy for reading only**:
  the document is `workspace`-visible, or the actor is `system`, or a `DocumentReader`
  row exists for this document and this actor that has not expired. Writing is checked
  against the workspace only. This matters: with one policy for both, making a letter
  private as somebody who is not yet a reader would fail on the new row, and every
  later write in that request with it.
- **Every table with a foreign key to a document** (pages, extractions, suggestions,
  history events, addressee and label links, shares, rule runs, tasks; for a task
  "has no document, or"): the same restrictive reading policy, written as a correlated
  `EXISTS (select 1 from document d where d.id = child.document_id)`, which the
  document's own policies filter. A test fails when a table with such a foreign key
  has no policy, as the existing coverage test does for workspaces.
- **DocumentReader**: the workspace matches and the row is the actor's own, or the
  actor is `system`. A user cannot see who else reads a letter through this table;
  the names shown with the lock come from the API, for a letter they can read.
- An empty actor sees no restricted document. Code that forgets the actor fails
  closed, as code that forgets the workspace does today.

There is no circle: document → reader → nothing; child → document → reader.

**The order inside a service that makes a letter private**: write the history line
and anything else that hangs on the document, write the readers as the system
(including the actor's own share), then switch the visibility. `WorkspaceModel.save`
today reads "not visible" as "other workspace"; that assumption is corrected.

**Deleting.** PostgreSQL's own foreign-key checks ignore row-level security; Django's
delete collector does not. Deleting a person, a label or a rule that is used on a
private letter the actor cannot see would pass Django's check and fail at commit.
So "is it in use" counts as the system, and these deletes run as the system after the
authorisation check; the repeated-upload lookup by client id does the same. Every
delete test gets a case with somebody else's private letter.

**Keeping the readers right.** `documents.visibility.recompute(document)` is the only
code that writes `addressee`, `department` and `share` rows, and it writes as the
system: as the user it could not insert a row for somebody else, and a delete of
somebody else's row would silently do nothing. It counts only current members. It
runs in the same transaction as every change of its inputs: visibility, addressees, shares, a person's
account link, department membership, removing a member. A nightly job recomputes every
restricted document as the system and logs how many rows differed (a number, no ids of
people); a non-zero number is a defect to investigate. Tests cover every input.

**What follows from enforcing it in the database**, without separate code: lists,
counts, the inbox, filters, a rule's "test on existing post", the history and the
task list only ever contain what the user may read. Rules that run inside a request
(at confirm) run as that user; the pipeline and a manual rule run are the system and
see everything.

What the database does not solve, and the design states:

- **The AI service** reads a letter when it is processed, before anybody could make
  it private. Private is about people in the workspace, not about the AI service the
  workspace chose (`docs/PRIVACY.md` says so).
- **Page files on disk** are sealed per workspace, not per reader. Somebody with the
  master key and the disk reads everything; that was already true.
- **The audit log** names a private document by its id only.
- **Back-ups** contain private post.

### 8.4 Contract

`DocumentSummaryOut` gains `visibility` (default `workspace`) and `reader_names` (the
names shown with the lock; empty for a public letter). New: `setDocumentVisibility`
(`visibility`, `shared_user_ids`). `ConfirmIn` gains `visibility` with the default
"leave as it is". A 1c app shows a private letter the user may read without the lock
and cannot change it; it never receives a letter the user may not read.

### 8.5 Emergency access

A workspace setting "Noodtoegang voor de beheerder". Off for Thuis, on for Zakelijk;
only a server admin changes it, and changing it is an audit event that every member
sees as a notice at their next visit.

With it on, an admin can open the private post **of one person or department**:
"Noodtoegang tot de privé-post van <naam>", with a reason of at least ten characters.
That writes `emergency` reader rows for the admin on that person's restricted
documents, valid for one hour, as the system. Every use is an audit event and a line
in the history of each opened letter, which its readers see ("<beheerder> opende deze
brief met noodtoegang: <reden>"). With it off there is no such button and no code path.

Access is per person, not per letter, because an admin cannot see a private letter
exist at all; that is the point of 8.3.

## 9. Single sign-on (OpenID Connect), opt-in

### 9.1 What the admin sets up

A server admin fills in, under "Aanmelden": the issuer URL, the client id and secret
(the secret is sealed with the master key), which email domains may sign in, and three
switches: "automatisch aanmaken" (off; when on: for which domains, in which workspace,
with the role viewer), "alleen via SSO" (off), and "koppel op e-mailadres" (off).
The issuer is one tenant's address, never a shared multi-tenant endpoint. One
provider per server. A "Test" button runs discovery and reports what failed in a plain
sentence. Without this page being touched, nothing about SSO exists for a user.

### 9.2 The flow

Authorization code with PKCE; `state` and `nonce` are kept in the session; the ID token
is validated (signature against the provider's keys, issuer, audience, expiry, nonce).
The redirect address is `https://<server>/api/v1/auth/oidc/callback`.

### 9.3 Which account

`ExternalIdentity(user, issuer, subject)`, unique on issuer and subject.

1. The identity is known: sign in as that user.
2. It is not known and the visitor came through an invitation: a new account is made
   and linked, or, when the visitor is signed in to Brievie at that moment, the
   identity is linked to that account. An existing account is never looked up by the
   address the provider sends: an invitation link can be passed on, and an address
   claim is not proof.
3. It is not known and an account exists with the same email address: the user signs
   in with their password once and links the identity from their account page. With
   "koppel op e-mailadres" on, the link is made automatically when the provider marks
   the address as verified in the way recorded for that provider (not every provider
   sends such a mark, and with some the address can be set by the tenant), the domain
   is on the list, and the account is not an admin or server admin.
4. None of these: with "automatisch aanmaken" on and the domain allowed, an account
   is made as viewer in the configured workspace; otherwise "Vraag de beheerder om een
   uitnodiging."

An email address alone never signs anybody in: only the pair of issuer and subject
does, after the first link.

### 9.4 When the provider is down, or says no

- **Emergency sign-in.** A server admin always keeps a local password (and should add
  the second factor); "alleen via SSO" never applies to a server admin. The sign-in
  page keeps a small "Inloggen met wachtwoord" link.
- **Existing sessions and paired phones keep working** while the provider is down:
  they do not ask it anything.
- **The other side of that**: somebody who was disabled at the provider keeps a
  working phone until the token expires or an admin removes them. So: a web session that was started through SSO lasts at
  most 12 hours; a device that was paired from such a session must be confirmed by a
  new SSO sign-in on the web within a period the server admin sets (90 days by
  default, at least 7), after which it stops like an expired token; and removing the
  member in Brievie works at once. Back-channel logout and
  SCIM, which close this gap fully, are later.

### 9.5 The app

The app has no SSO screen. A user signs in on the web (through SSO), opens "Apparaten"
and pairs with the QR code, or on the phone itself with the pairing link (6.2). The
device token is the app's credential, as now.

### 9.6 What a home server can and cannot do

- The identity provider never calls the server; the browser does. So the server's own
  certificate authority is not the obstacle. The **name** is. Read on 2026-11-17:
  Google requires a host under the public suffix list and refuses raw addresses, so a
  `.local` name does not pass; Microsoft Entra requires `https` and states no rule
  about the name, so an internal name seems allowed (inferred from what the page does
  not say; to be tried); Authentik states no restriction; Keycloak's page could not
  be read.
- The server must reach the provider over the internet (discovery, tokens, keys). A
  self-hosted provider with its own certificate authority needs that authority's
  certificate configured (`BRIEVIE_OIDC_CA_FILE`).
- In practice: SSO with a public provider needs the server on a public name (an own
  domain or a tunnel); on a home network without that, the settings page says so
  before anything is filled in. That is acceptable: SSO is the organisation's layer.

## 10. Second factor (TOTP)

- A user adds an authenticator app under "Beveiliging": a QR code and the secret as
  text, confirmed with one code; ten recovery codes, shown once, stored as hashes.
  The secret is sealed with the master key.
- Signing in then takes two steps; the second is rate limited by the existing lockout,
  and a code cannot be used twice.
- A server setting says who must have it: nobody (default), server admins, or
  everyone. A user who must but has not, is led to set it up at the next sign-in.
- Lost phone: a recovery code; without one, an admin clears the member's second factor
  (audit event); a server admin uses the management command.
- It guards the web sign-in and therefore pairing (a pairing code needs a session).
  Phones that are already paired are not asked again.
- A sign-in through SSO is not asked: the provider does it. This is a property of
  the sign-in, not of the account: a password sign-in on an account that also has an
  SSO identity is asked like any other.
- The rule sits where a session is created, not on the sign-in page, so accepting an
  invitation and completing a reset pass through it too.

## 11. Audit log

Who did what, when, from where, without document content.

- `WorkspaceAuditEvent` (under row-level security, read by workspace admins) and
  `ServerAuditEvent` (read by server admins). Each: time, actor (user, device, system
  or SSO), a code from a closed list, the target's kind and id, the actor's and the
  target person's name as they were then, the client address, and a few plain values
  per code (the new role, the reason for emergency access). Never a letter's sender,
  summary, text or label names.
- **What is logged**: sign-in succeeded, failed, locked out; second factor added,
  removed, cleared; password changed, reset link made; session ended; invitation
  made, revoked, accepted; member's role changed; member removed or left; device
  paired, revoked, expired; workspace setting changed; SSO settings changed; rule
  made, changed, deleted, run by hand; label or person deleted; visibility changed; a person linked to or
  unlinked from an account; a department member added or removed; emergency access switched on or off and every use; export made and downloaded.
- **Append-only for the application**: the app's database role may insert and read
  these two tables, not change or delete (revoked in the migration). A test checks
  the privileges of the app role itself, because the test suite runs as the owner and
  would not notice a forgotten revoke. The rows have no foreign keys (actor and target
  are an id and a name), since a cascade would need the rights that were revoked.
  Old rows are removed by a function owned by the schema owner that takes a workspace,
  enters its context itself (the owner cannot bypass row-level security either) and
  refuses a cut-off younger than 90 days; a job calls it after the retention period:
  one year by default, configurable.
- **Reading it**: a page with filters on person, kind of event and date, and a CSV
  download. At home it sits under "Instellingen" and nobody needs to look.
- The per-document history of 1c (`DocumentEvent`) stays what the user sees on a
  letter; the audit log is the organisation's record.

## 12. Enterprise conditions: in 1d or later

| Condition | Where | Why |
|---|---|---|
| Audit log | 1d, part A | every later part writes to it |
| Session management | 1d, part A | belongs with members and devices |
| Device tokens that expire | 1d, part A | closes a standing open item |
| Private post, emergency access | 1d, part B | |
| SSO | 1d, part C | |
| Second factor | 1d, part C | same screens as SSO ("Aanmelden", "Beveiliging") |
| Retention periods | later (phase 2, Zakelijk) | needs deleting documents and a trash, which do not exist yet; the fiscal retention rules belong to the business phase |
| Back-up and tested restore | later (the "beheer" part of the roadmap) | its own design: what is in a back-up (database, sealed files, the master key apart), a restore test from the web. 1d adds one requirement to it: the new tables and the sealed SSO and TOTP secrets are part of the back-up |
| Access from outside (second trust model, tunnel) | 1d, part D (section 19) | optional layer; a configuration-only first step exists |
| SMTP mail | later | every flow in 1d works with a link first |
| SAML, SCIM, back-channel logout | later | on request of a real organisation |

## 13. Parts (delivery order)

Each part is delivered and usable on its own, with its own implementation plan.

| Part | Content | What the owner sees |
|---|---|---|
| **A. Leden en apparaten** | roles and server admin; invitations by link or QR; accept, password, reset link; remove and leave; own export; devices per member, revoke by admin, token expiry, app version; sessions; audit log | invite the partner with a QR code; they scan, choose a password, open the app; "Leden" and "Apparaten" pages; the log |
| **B. Privé en afdelingen** | visibility per letter, readers, the second row-level-security layer, "Zorg is privé", shares, departments, the rule action, emergency access; lock in web and app | a Zorg letter that the partner does not see; a letter for Finance that only Finance sees |
| **C. SSO en tweede factor** | OpenID Connect, identity linking, the options, emergency sign-in; TOTP with recovery codes and the policy | sign in with the company account; an authenticator app |

A fourth part, **D. Toegang van buiten**, is described in section 19 with its own
steps; it can follow A.

App work per part: A is small (the server link with the typed pairing code, the app version
header); B is the lock, "Privé" in the review and on a
letter, and sharing; C needs nothing.

## 14. Compatibility

- Every change is additive: new tables, new endpoints, new fields with defaults; the
  contract tests of 1c guard it.
- **A 1c app against a 1d server**: works. It does not show locks, cannot make a
  letter private, and gets a 401 when its token expired (it then shows pairing, as it
  does for a revoked device).
- **A 1d app against a 1c server**: works. The new fields are optional and missing;
  the app hides "Privé" and sharing when the server does not offer them, as it hides
  the organise sections against a 1b server.
- One behaviour changes for existing installations: device tokens start to expire.
  The expiry counts from the later of the device's last use and the moment the
  update was installed (recorded once by the migration), so no existing phone stops
  on the day of the update.

## 15. Edge cases the plans must test

- An invitation used twice at the same moment; used after it was revoked; for an
  email address that already has an account; for a person who is already linked.
- The last admin removing themselves; the last server admin being deactivated.
- A member removed while their phone uploads; while they are confirming a letter.
- A user who is a member of two workspaces, removed from one.
- A private letter in: the post list, the inbox counts, a person filter, a rule test,
  a manual rule run, the history of a linked task, a suggestion, the task list, the
  calendar sync, the audit log, an export.
- Readers after: an addressee added or removed, a person linked to another account, a
  department member added or removed, a member removed, a share removed, the
  visibility switched twice.
- A job that forgets to say it runs as the system: it sees no private letter and the
  test notices.
- Emergency access: off (no path), on (reason required, one hour, visible to the
  readers), expiring while the letter is open.
- SSO: an unknown identity with a matching but unverified email address; the provider
  down (server admin signs in with a password; others see a clear sentence); the
  provider returning another subject for the same email address; "alleen via SSO" with
  a member who has a password.
- Second factor: a code used twice; recovery codes; a required factor at first sign-in;
  the clock of the phone a minute off.
- A reset or invitation token in a log line (there must be none).

## 16. Deviations from `docs/SPEC.md`

- Spec 16 calls step 1d "Invoer en beheer" (watch folder, IMAP, Berichtenbox, wizard,
  export, back-ups). This phase takes the name for members, roles and sign-in; the
  intake channels and back-ups keep their place on the roadmap after it.
- Spec 7 and 12: the member's "map-beperking" is not built (no folders yet);
  `document_acl` becomes `DocumentShare` plus the derived `DocumentReader`;
  `audit_log` becomes two tables.
- Spec 14 "Lid verlaat werkruimte: keuze per document: meenemen of achterlaten" becomes:
  everything stays, and the member can export their own post first (decision 2).
- Spec 11 advises Cloudflare Access in front of the web interface for SSO; this design
  adds SSO in Brievie itself, and keeps that advice for whoever wants both.
- Spec 8 and 14 say a Zorg letter is visible to the person only. Here whoever scanned
  or confirmed it stays a reader by default (the spec's own example of the son and
  his mother's letter), shown with the lock, and the person can remove that share.
- Relations (leveranciers, klanten) stay phase 2; departments are the addressee side.

## 17. Left to the builder

Names of private helpers, the Dutch sentences (B1, je-form), the layout of the new
pages within the existing design, test file names. Not left to the builder: the
rules of sections 6 to 11, the policies of 8.3, and the list of what is logged.

## 18. Open points for the plans

- The exact rules each public identity provider applies to redirect addresses, checked
  against their documentation when part C is planned.
- Whether `authlib` is still the right client library at that moment (version,
  maintenance, licence), recorded in `DECISIONS.md`.
- The cost of the document-visible check on the child tables at 100,000 documents,
  measured with a test before part B is built; the fallback is the same reader table
  per child table.

## 19. Access from outside the home (optional layer, part D)

Added on 2026-11-17 on the owner's request ("later zou het ook met cloudflare moeten
werken"; reference: how a self-hosted Vaultwarden does it). Nothing in this section
changes a server that stays on the home network.

### 19.1 What exists today (checked in both repositories)

- The app trusts **either** the server's own certificate authority (the root is pinned
  at pairing; system roots are then ignored) **or** the system's roots (when the QR
  code has no `ca_sha256`). The choice is made once, at pairing, and holds for every
  address of the server (`ServerTrust.swift`, `PairingPayload.swift`).
- The app already stores several addresses and tries them in order, local first.
- The server already accepts several public URLs (`BRIEVIE_PUBLIC_URLS`, each host in
  `ALLOWED_HOSTS`) and puts all of them in the QR code. Caddy serves one host name
  with its own authority (`tls internal`).
- The client address for the sign-in lockout is taken from `X-Forwarded-For`, counted
  from the right by `BRIEVIE_PROXY_COUNT` (1 behind Caddy).
- Two things stand in the way of a publicly trusted address: one trust setting for all
  addresses (a private authority at home plus a public certificate outside fails on
  the second), and the app's background upload follows redirects, which with system
  trust could send a scan to another host (noted in the app's `DECISIONS.md`).

### 19.2 The difference with Vaultwarden, stated plainly

Vaultwarden encrypts the vault on the device; the server, and anything in front of it,
sees only ciphertext. That is why a tunnel that ends TLS at a third party is
defensible there. Brievie reads the letters on the server (rules, AI, later search),
so **whoever ends TLS can read the post**. With a Cloudflare tunnel on a public host
name that party is Cloudflare: its documentation says the edge certificate is the one
"Cloudflare presents to clients", so the visitor's TLS session ends there (`docs/SPEC.md`
11 says the same; a sentence from Cloudflare that it can read the traffic was not
found in the pages read). Two more limits read there: a request body is at most 100 MB
on the free plan, and the terms allow Cloudflare to limit use that is mostly pictures
or other large files without a paid service (whether that covers a tunnel is not
stated). A variant in which Cloudflare only passes the
encrypted stream on exists, but needs Cloudflare's own client on the phone, which
makes it a private network with extra steps. So the honest table is:

| Way | Who can read the traffic | What the user needs | Reachable by |
|---|---|---|---|
| Home network only (default) | nobody outside | nothing | devices at home |
| Private network: Tailscale or WireGuard | nobody outside; end to end | the VPN app on each phone and computer | only own devices |
| Own domain, own port forward or VPS proxy | nobody outside (TLS ends on the server) | a domain, an open port or a small proxy | everyone: the sign-in page is on the internet |
| Cloudflare tunnel | Cloudflare, technically | a Cloudflare account and a domain | everyone, behind Cloudflare's filter |

The product recommends them in that order for a home, and says in the setup text what
the last one means. For an organisation the third or fourth with SSO and Cloudflare
Access (19.6) is the usual shape.

### 19.3 Two trust models, per address

1. **Own authority, pinned** (now, default). No domain needed, strictest: only this
   server's root is accepted.
2. **Publicly trusted certificate, system trust.** The pattern Vaultwarden recommends
   also for a server that is only reachable at home: an own domain with a Let's
   Encrypt certificate through the DNS challenge, which needs no open port. The same
   name then works at home and outside (through a VPN, a tunnel or a port forward).
   Cost for the user: a domain, an API token of the DNS provider in `deploy/.env`,
   and a Caddy image that contains the DNS module (Brievie would publish its own
   Caddy image with a few providers: a dependency with a line in `DECISIONS.md`).
   A known pitfall: routers with DNS-rebind protection refuse a public name that
   points to a private address.

A server can have both at once: the `.local` name under its own authority and an
outside name with a public certificate. For that the trust becomes a property of the
**address**, not of the connection:

- **Server**: `BRIEVIE_PUBLIC_URLS` keeps its meaning (addresses served under the own
  authority when one exists). New: `BRIEVIE_EXTERNAL_URLS`, addresses with a publicly
  trusted certificate. Both lists go into `ALLOWED_HOSTS` and the CSRF origins, with
  the existing system check.
- **QR code** (still `v: 1`; the app ignores unknown fields): `urls` and `ca_sha256`
  stay exactly as they are, so a 1c app pairs as before and uses the pinned addresses.
  A new field `addresses: [{"url": …, "trust": "pinned" | "system"}]` lists all of
  them. A 1d app uses `addresses` when present. An address with `system` trust is
  never checked against the pin, and a `pinned` address never falls back to system
  trust.
- **An app that is already paired** gets the list from a new authenticated endpoint
  (`listServerAddresses`), asked at start over a connection it already trusts, and
  stores it. No pairing again. The app accepts only `https` addresses, at most a
  handful, and a pinned address only with the authority it already has.
- **App conditions before system trust is supported**: redirects are refused in the
  background upload session as they are elsewhere.

### 19.4 Server side

- **Caddy stays** in every variant: it is the one place for the headers, the content
  policy and the request limits. A tunnel connector (`cloudflared`, optional
  container) talks to Caddy on a listener inside the container network, not to the
  API directly.
- **The real client address.** The lockout must count the visitor, not the tunnel.
  Caddy, not Django, decides which header to believe: on the tunnel's listener it
  takes the address from Cloudflare's `CF-Connecting-IP` and trusts it only from the
  connector; on the public listener it keeps today's behaviour. Django keeps
  `BRIEVIE_PROXY_COUNT=1`. A test sends a forged header through each listener.
- **HSTS** stays opt-in for the own authority and can be on for an external name.
- **A system check** warns when an external address is configured and no server admin
  has a second factor.

### 19.5 Protection once the sign-in page is reachable

What Vaultwarden's hardening guide advises, as far as it fits:

| Advice | Brievie |
|---|---|
| Registration closed, invitations controlled | already so: nobody gets in without an invitation (6.2); invitation and pairing endpoints are rate limited |
| Second factor | part C; the settings page urges server admins to add it as soon as an external address exists, and the admin can require it |
| Lockout on failed sign-ins, with the real client address | exists (django-axes); 19.4 keeps the address right |
| The admin page guarded separately | server settings (SSO, addresses, emergency access) ask for the password or second factor again when the last check is older than 15 minutes |
| No secrets in proxy logs | tokens travel in headers, bodies or URL fragments, never in a query string (6.2) |
| Run as a normal user, read-only where possible | already the container setup; unchanged |

### 19.6 Cloudflare Access in front

Access puts Cloudflare's sign-in before the web interface. Two consequences:

- **The app cannot show an interactive sign-in at each request.** Two honest options:
  leave `/api/v1` paths that carry a device token outside Access (the API then stands
  on its own: token, lockout, rate limits), or give the app a service token that
  Access accepts (two static headers on every request). The second needs the app to
  store and send those headers per address and the server to hand them out after
  pairing; it is one shared secret for all phones. The design chooses the first for
  part D and leaves the second as a later option, because the device token is
  already a strong, revocable credential per phone.
- **With OIDC**: Access can use the same identity provider as Brievie's own SSO. The
  user then signs in once at the provider and passes both. Brievie does not accept
  Cloudflare's header as a sign-in in this phase: its own session or SSO stays the
  only way in, so a mistake in the Access policy never lets anybody in.

### 19.7 Texts that change

The site and the planned `SELFHOST.md` say not to put the server on the internet
(`docs/SPEC.md` 14: "niet direct aan internet zonder tunnel/VPN"). That becomes: at
home is the default; for outside, the table of 19.2 with what each way means for who
can read along; never a bare port forward to the API without Caddy. `docs/PRIVACY.md`
gains the sentence about who ends TLS.

### 19.8 Where it belongs, and the smallest step

A part of its own, **D**, after A. It needs nothing from B or C, but its advice leans
on the second factor of C.

| Step | Content |
|---|---|
| **D0, today, configuration only** | A private network (Tailscale) on the server and the phone; the server's Tailscale name added as a second host name in Caddy **under the same own authority**, and as a second entry in `BRIEVIE_PUBLIC_URLS`. The pin still holds, nothing is public, no third party reads along. The phone pairs once more to learn the second address. Needs the deploy files to accept more than one host name, and a try-out: Caddy's documentation says it does not manage `.ts.net` names itself but fetches their certificate from the local Tailscale service, so whether the explicit `tls internal` keeps the own authority for that name must be seen working; a plain second name through Tailscale's own DNS is the fallback. Not verified on a device. The Tailscale account would be the owner's (free for personal use, up to six users). This is how the owner can test outside the home now. |
| **D1** | Trust per address (19.3), `listServerAddresses`, the redirect fix in the app, `BRIEVIE_EXTERNAL_URLS`, the Caddy image with the DNS challenge, the texts. |
| **D2** | The optional tunnel container with the right client address (19.4), the re-check for server settings, the Access guidance. |

A Cloudflare quick tunnel (a random `trycloudflare.com` name, meant for testing) does
not work with today's app: the app is paired to the own authority and refuses
Cloudflare's certificate. That is the pin doing its job.

## 20. What the independent reader changed (2026-11-17)

The reader's report is `fase-1d-lezerscontrole.md` in the session notes. All nineteen
findings and ten questions were taken; the designer's rulings, each with its cost if
it turns out wrong:

| Ruling | Why | Cost if wrong |
|---|---|---|
| Visibility is a restrictive policy for reading only; writes are checked against the workspace (8.3) | one policy for both makes "make private" fail and poisons the rest of the request | a write to a row one cannot read is possible inside the workspace; services already authorise before writing |
| Readers are written, and some deletes are done, as the system inside a request (8.3) | as the user the inserts fail and the deletes silently do nothing | `system_actor()` is powerful; it is limited to named services and a test lists its callers |
| Nobody gives themselves access; grants are logged and shown to the readers (8.1) | linking a person to oneself was a way round "private means private" | an admin at home who really is that person must be linked by the invitation flow |
| Automatic actions do not add addressees to private letters (8.1) | a rule could make its author a reader | a private letter may wait for a suggestion to be accepted |
| Whoever makes a letter private stays a reader; only an addressee makes it public again (8.1) | otherwise the transaction has no valid outcome, and the scanner could open the letter to all | a reader too many, visible with the lock and removable |
| Account actions belong to the server admin (6.3) | an account spans workspaces; an admin of one could take over an admin of another | in a larger organisation the workspace admin must ask the server admin |
| An invitation never finds an existing account by the provider's address; linking on address is off by default (9.3) | an address claim is not proof | a first SSO sign-in needs one password sign-in to link |
| The second factor sits where a session is made (10) | invitation and reset would otherwise skip it | none known |
| The pairing link carries no secret; the code is typed (6.2) | another app can claim the URL scheme | eight characters to type on the phone |
| Token expiry is computed from `last_seen_at`; expired answers as revoked (7.1) | no extra write, and a 1c app already handles it | no separate text for "expired" |
| "Privé maken" is a new action type, automatic rules only (8.1) | a version bump would skip all rules; there is no suggestion kind for it | a proposing rule cannot make private |
| The audit purge enters the workspace context itself; no foreign keys in audit rows (11) | under forced row-level security the owner sees nothing either | none known |

Still to verify when the plans are written: what the 1c app does with calendar events
of tasks that disappear from the list (a letter that became private), and whether it
matches on the 401 code.
