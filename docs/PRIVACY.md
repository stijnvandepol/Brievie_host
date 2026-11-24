# Privacy: where your data is and what leaves the server

This describes the server as built (phase 1d part A: members, roles and devices, with
recorded consent for AI). It is updated with every phase.

## What is stored, and how

| Data | Where | Protection |
|---|---|---|
| Page images of a document | Files in the `documents` volume | Encrypted (AES-256-GCM) with a key per workspace; that key is stored wrapped with `BRIEVIE_MASTER_KEY` |
| API key of an external AI service | Database | Encrypted with the workspace key |
| Recognised text (OCR), proposals, summaries, tasks, persons (names and name variants), labels, rules, the history of changes | Database | **Not encrypted**: needed for review and (later) search. Protected by row-level security per workspace and by masking BSNs before anything is stored. Put the database volume on an encrypted disk |
| The decisions about AI (see "Consent for AI") | Database | Not encrypted; row-level security per workspace. No API key, no full address, no document text |
| Passwords, pairing codes, device tokens, invitation and reset links | Database | Only as hashes (the pairing code also as a key derived from it, sealed with the master key, for the ten minutes it lives) |
| Accounts: name, e-mail address, whether the account is switched on, whether it is a server admin | Database | Not encrypted; the account belongs to the whole server, not to one workspace |
| The secret of an authenticator app (two-step verification) | Database | Sealed (AES-256-GCM) under a key derived from the master key, bound to its account; a database dump without the master key gives no secret. In the back-up in its sealed form |
| Recovery codes of two-step verification | Database | Only as HMAC-SHA256 under another key derived from the master key: a leaked dump cannot be tried against them without that key |
| Web sessions: client address, a short description of the browser ("Firefox op Windows"), start and last use | Database | Not encrypted; removed when the session ends or runs out |
| Devices: name, app version, when paired and last used | Database | Not encrypted |
| The audit log (see "The log") | Database | Not encrypted; row-level security per workspace; the application cannot change or delete rows |
| A member's export (see "The export") | Files in the `documents` volume, under `exports/` | Sealed (AES-256-GCM) while it is stored; not in the back-up |
| Back-ups | Only the destination you chose (a disk, a NAS share, a REST server or S3-compatible storage) | Encrypted by restic with the back-up password. The database dump is streamed into the back-up and never stored unencrypted outside the database. The master key is not in a back-up. See `docs/SELFHOST.md` |

A BSN (citizen service number) is replaced by `[BSN]` before text is stored, logged or
sent to an AI service. The page image itself still shows it; that file is encrypted.

Logs never contain document text, names of persons or labels, rule names or values,
tokens or passwords.

Who sees which letter follows the workspace's settings (**Instellingen → Wie ziet wat**),
not the workspace alone. In *Thuis* everyone sees all post, except private letters. In an
*Organisatie* everyone sees the post on their own name and that of their departments;
whoever sorts post ("Verdeelt post", or the admins while nobody does) sees post that
carries no name anybody reads; admins read all post only while "Beheerders lezen alle
post" is on. A private letter is seen only by its readers (see "Privé-post" below). The
database enforces this (row-level security), not only the screens, and every change that
lets somebody see more is previewed and confirmed by an admin. Every member reads who can
see their own post under **Wie kan mijn post zien?**, and on a letter who can see it.

## What leaves the server

| AI setting of the workspace | What is sent, and where |
|---|---|
| `off` (default) | Nothing |
| `local` | The masked text, with the names of the workspace's labels and the names and name variants of its persons, to an address inside your own network only (for example Ollama). Never ids, rules or the page images |
| `external` | The masked text of the letter, with the names of the workspace's labels and the names and name variants of its persons, to the AI service you configured (default: Mistral, Paris). Never ids, rules or the page images |

The names go along so the AI can choose labels and addressees from the workspace's own
lists; it answers with short numbers that the server translates back, and only the
workspace's own labels and persons can be chosen. At most 50 persons and 100 labels
(by name) are offered per letter. Rules are never sent. With `off`,
names are still recognised in the letter and rules still run, on the server itself.

## Consent for AI

AI is off until an administrator of the workspace switches it on: on the page "AI" of
the web interface, or with `manage.py configure_ai`. Both go through the same rules.

- **Asked first.** Before a workspace is switched to `external`, the command says in
  Dutch what is sent with every letter (the table above), to which service, host and
  country (Mistral: France, EU; OpenAI and Claude: United States; another address:
  unknown, look it up yourself), that the service's own terms and retention apply, and
  that the administrator decides for everyone whose post is in the workspace. It goes on
  only after the word `akkoord` is typed, or with `--accept-external-ai` in a script,
  which states the same. Without either, nothing changes. The web interface shows the
  same text, written by the server, in a dialog with a tick box "Ik heb dit gelezen en
  ga akkoord"; the browser then sends back the address the text was about, and the
  server refuses when that is not exactly the address it is asked to switch on. An
  agreement for one service can therefore not switch on another.
- **Recorded.** Every decision is a row the application only adds, never changes: who
  (the administrator's account from the web interface or the app, `command line` from
  the command), when, the
  mode, the supplier, the host of the address and the model. Switching AI off, moving to
  another service and moving to the own network are rows too. The application's
  database role can add and read these rows but not change or delete them (a database
  privilege, tested). The owner of the database still can, so this protects against a
  bug or a hacked application, not against whoever runs the server.
- **Asked again for another address.** A consent covers one address (host, port and
  path). Another model or a new key at the same address is recorded without a new
  question; any other address needs a new `akkoord`. `local` is recorded without a question: nothing leaves the network.
- **Enforced.** The server sends text to an external service only while there is a
  consent for exactly the configured address, and it follows no redirect to another
  one. A workspace that was set to `external` before consent was recorded reads no
  letters with AI until `configure_ai` is run once more; letters scanned in between
  fail and are started again by hand.
- **Visible.** Every member sees on the Inbox, Post and AI pages whether AI is on, which
  service and country the text goes to and since when. Somebody who is invited sees
  the same line on the invitation page before accepting, and so does anyone who holds the
  invitation link, with a yes or no whether a key is stored (never the key). Only an administrator can change
  it; a member or viewer gets no buttons and the server refuses them. The API gives the same
  (`GET /api/v1/workspaces/{id}/ai`: mode, supplier, host, since, and whether a key is
  stored; the model for an administrator only), never the full address or the key.
- **The API key only goes in.** It is typed in the form or asked by the command, stored
  encrypted with the workspace key, and never sent back to a browser or an app, not
  shown again and not logged. Changing the model keeps the stored key; a key never
  moves along to another address, there it must be typed again. Changing the AI
  settings is limited to 10 attempts per 10 minutes per administrator and workspace.
- **Limits.** Consent is the administrator's, for the whole workspace: there is no
  consent per member or per letter. Switching off stops sending; it does not take back
  what a service already received. The people who write to you are not asked.

The iPhone app recognises the text on the phone itself and uploads the pages and that
text to your server only.

## Automated processing (the autopilot)

Normally a person checks every letter before it is final. An administrator can let
Brievie skip that, per workspace: put letters under persons and labels by itself, and
confirm them by itself. That is automated processing without a person in between, so:

- **Opt-in, off by default.** Only an administrator can switch it on, from a web session
  (not with a phone's token), only while AI is on, and only after reading the notice the
  server shows and agreeing to it. The notice says nobody checks first; the AI believes
  what the letter says, so a wrong or misleading letter gives a wrong sender, date or
  task; what the guard rails check; that Brievie can still miss something, so read the
  letters that matter yourself; and that everything automatic is marked and can be undone.
- **Logged.** Every switch that changes is a row in the log: who, when, which switch
  (`ai_auto_organise` or `ai_auto_confirm`) and whether it went on or off.
- **Marked.** What Brievie did by itself has no person behind it and says so: the list
  and the letter show "Automatisch door AI", so does a task the AI made (also in the task
  list of the API, `created_by_ai`), the history says it was automatic, and the list can be
  filtered on it. No user is recorded for what nobody did.
- **Reversible.** A member can undo an automatic label or person, and reopen a letter
  Brievie confirmed ("Zelf controleren"): its fields are emptied and the tasks Brievie
  made that are still open are removed. A change somebody undid is not made again.
- **Nothing extra leaves the server.** It uses the answer the AI already gave; no more
  text goes to the service than without it. Switching AI off, changing the mode
  (external or local), or agreeing to another address switches both off. A server owner can forbid it for the whole server
  (`BRIEVIE_AI_AUTOPILOT_ALLOWED=false`, see SELFHOST).
- **What the guard rails check.** A letter is confirmed only when the sender, the date
  and every deadline the AI gives are literally in the letter (with enough certainty), and
  a letter that names a term (or an invoice due date) is left for a person unless every
  action (every payment) has a date. A rule's proposal to put a person on a letter is
  never applied. A rule's label only when the rule holds on what is settled (its words
  in the letterhead, a sender or type a person confirmed, labels and persons already on
  the letter), or when the AI picked the same label with a confidence of 0.800 or more;
  not when the rules disagree on the letter, when more than three rule labels qualify,
  or when the administrator agreed only to the notice from before this rule (they are
  asked to agree again). An AI pick of a person only when the letter's quote names that
  person.
- **Limits.** It reduces checking; it does not make the AI right. The rails check that a
  value is in the letter, not that the letter is true: whoever writes a letter decides what
  is in it, so a wrong or misleading letter can still be confirmed with a wrong sender,
  date or task. A wrong name match, a needless task or a term the rails do not recognise
  can be applied or confirmed without anyone looking.

## Members, sessions and devices

- **Who can do what to an account.** A workspace admin can invite, change roles, remove
  a member and, for a member whose only workspace this is (and who is no server admin),
  make a reset link, end the sessions and revoke the phones. A server admin can do the
  same for every account. Nobody can read another person's password. Nobody can make a
  reset link for their own account.
- **What a reset does.** The person who holds an admin's reset link chooses the new
  password; the account's sessions end and its phones are unpaired. The member gets a
  notice. **An admin who made a reset link and used it can sign in as that member.**
  That is what a reset is; Brievie cannot prevent it, and records it: the log shows the
  link's creation, who made it, and the revoked phones. The first session after a reset
  cannot dismiss the member's notice, but a later one by the same admin can, so the
  notice is not proof.
- **Sessions and phones.** A member sees their own sessions (address, browser) and
  phones (name, app version). An admin sees a member's phones, not their sessions. A web
  session ends after 14 days unused or 90 days at most; a phone after 180 days unused.
- **Two-step verification.** A code from an authenticator app after the password, or a
  recovery code for a lost phone. The secret and the recovery codes are shown once, when
  they are made (answers that are not cached), and are never in a log, a log row, a notice
  or a later answer. A wrong code is logged as a failed sign-in (no code in it); after
  five wrong codes within 15 minutes the second step of the account locks, for 15 minutes,
  doubling each time up to a day, and the member gets a notice that somebody had the
  password but not the code. An admin who clears a member's lost factor is logged, the
  member is told and every session of the member ends; the admin types their own password
  for it. A reset link does not skip the code: whoever holds the link still needs it.
- **The e-mail on an invitation is not verified.** An admin may fix an address on an
  invitation, so that only that address can accept. Brievie sends no mail, so it only
  compares what is typed with what the admin entered. Anyone who has the link and knows
  the address can accept it. Do not rely on it to keep a link from the wrong person:
  send the link to the right person only.
- **Removing a member.** The member's account is switched off when it was their last
  workspace; their name stays in the log. Their post stays in the workspace.

## The log

Brievie records who did what, so an owner can see what happened. A row holds: when, the
actor (a person, a phone, the system), the actor's name as it was then, the client
address, what was done (one of a fixed list), what it was done to, and a few small
values (a role, a count, "yes" or "no"). Names are kept only for people, phones,
invitations (the admin's note or the address) and workspaces. A label, a rule, a letter,
an export or a session is an id only. Never a letter's content, a token or a password.

- **What is logged**: sign-ins and failed sign-ins (a failure for a known account is a row about that account;
  for an unknown e-mail address the row names nobody and the typed address is not
  stored), password
  changes and resets, sessions ended, phones paired, revoked and expired, server admins
  appointed, invitations made, withdrawn and accepted, roles, removals and leaving,
  workspace settings (AI settings too), rules made and changed, labels and persons
  deleted, exports made and
  downloaded.
- **Who reads it.** Admins read their workspace's log; server admins read the server's.
  Members and viewers cannot, and a phone cannot (only a web session can). The CSV file
  holds what the screen holds: the address too.
- **For how long.** 365 days by default; set `BRIEVIE_AUDIT_RETENTION_DAYS` (at least
  90). The database refuses to remove anything younger than 90 days.
- **Limits.** Every row holds the client address (for a phone or a browser behind a proxy, as far
  as the proxy reports it); it is personal data of whoever acted. The rows of a workspace that is deleted are not purged
  automatically (no workspace deletion exists yet). A back-up holds the log; a restore
  brings it back.

## The export

Every member can download their own post as a ZIP ("Mijn post exporteren"): a photo of
every page of the letters addressed to them, a data file with sender, summary, labels,
persons and tasks, and a readme. In the text and the data file the BSN is replaced by
`[BSN]`; **on the photos of the pages it is readable**, because they are the pages. The
file is sealed while it is on the server and removed after 24 hours; it is not in the
back-up. Once downloaded it is an ordinary unencrypted file: the member decides where it
goes. An admin can switch exports off for a workspace (off by default in Zakelijk).
Making and downloading are in the log, with the number of letters, not their names.

## The trash

A member (not a viewer) can move a letter to the trash ("Verwijderen"). It is then gone
from every list, count, search, task list, export and rule run, and no text of it goes to
an AI service, but nothing is removed yet: it can be put back for 30 days. Only members who
may see the letter see it in the trash.

- **The purge.** After 30 days, or earlier when an admin chooses "Definitief verwijderen" or
  "Prullenbak legen", Brievie removes the letter for good: its sealed page files, its rows
  (text, summary, proposals, links to persons and labels, suggestions, history, shares,
  rule runs) and its tasks, also the done ones. Persons and labels stay. What remains is
  the log: who moved it, put it back or purged it, with the letter's id only, never its
  sender, summary or text.
- **Back-ups keep it longer.** A back-up made before the purge still holds the letter until
  that back-up expires: with the current retention, monthly snapshots up to a year
  (`docs/SELFHOST.md`). Restoring such a back-up brings the letter back. The web says this
  in the question before "Definitief verwijderen" and "Prullenbak legen".
- **An export made before stays.** A member's export made before the purge stays on the
  server until its 24 hours are over; a downloaded one is the member's own file.
- Brievie does not watch a retention term yet (for example the fiscal duty to keep an
  invoice): an admin who purges early must mind that themselves. The web says so on the
  trash page.

## Duplicate letters

When a letter arrives, Brievie compares it with the live letters of the same workspace that
arrived in the last 180 days, to notice the same letter scanned twice. This runs in the
server's own database, as the system, over all those letters, private ones included: the
comparison must see both to find a pair. Nothing is sent to an AI service or any other
party, and nothing is compared across workspaces.

- **Shown only to who sees both.** A pair ("Dubbel?") is shown, counted and decided only by
  a member who may see both letters. A member who sees one of them gets no row, no count, no
  mark on that letter and the same "not found" answer as for a pair that does not exist, so
  nobody learns that a letter they may not see exists. A viewer sees no pairs.
- **What is kept.** The pair row holds the two letter ids, two scores and the choice (who,
  when). The history of both letters says that they were merged or called two letters. A
  merge moves the other letter to the trash (30 days, see above), written to the log as
  `document_trashed` with reason `duplicate` and ids only.
- **No content in logs.** Detection and merge log ids, counts and error type names only:
  never a sender, an amount, a reference or a word of the text, and no audit row holds them.

## Search

Searching letters (`searchDocuments`) runs in the server's own database, inside the
workspace of the member who asks and only over the letters that member may see. Nothing is
sent to an AI service or any other party. It reads the same stored data as the rest (the
masked text, sender, summary, label and person names), so the stored text is not encrypted
for it; see the table above.

- **The term is not kept.** It is sent in the request body (never in an address). Brievie does
  not store it, does not write it to its log or the audit log, and does not repeat it in an
  answer or an error. A search leaves no row. The web app keeps it in memory only (not in the
  address or the browser's storage) and forgets it on signing out or switching workspace.
- **PostgreSQL's own log.** PostgreSQL logs a statement that fails (its default,
  `log_min_error_statement = error`), for example one cancelled or cut off. The database
  driver puts the values into the statement text before sending it (client-side binding), so
  such a log line holds the search term. The same already holds for any failing statement
  that carries letter text. That log stays inside the database container; treat it like the
  database itself.
- **A BSN is taken out.** A BSN in the term, also written with other separators (a slash,
  a comma, a dash, an invisible character), the literal `[BSN]` and the words "bsn" and
  "burgerservicenummer" are removed before anything else; the answer says so. Stored text
  holds no BSN, so none can be found.
- A fragment of the letter (at most 240 characters) comes back with each hit, to the member
  who may read that letter.

## Mijn taken

- "Mijn taken" is a list made on every request from the letters a member may read; no
  copy of anyone's list is stored. A task, its words, its amount, its counts and the names
  "for whom" reach only someone who may read its letter: a task of a letter in the trash or
  of someone else's private post is in no list and no count, also not an admin's.
- A task is handed only to a member who may read the letter, never to a viewer. When that
  member loses sight of the letter (it goes to someone else, the member leaves or becomes a
  viewer, a person is unlinked), Brievie takes the task back itself and says so in the
  letter's history ("Brievie haalde een taak weg bij …"), with no person as actor.
- Ticking off, handing over and filling in a date are history on the letter, under the
  letter's own rights; they are not in the admin log.
- An amount is stored only when someone confirmed it, or when the invoice's amount is
  literally in the letter (the autopilot); the words of a term are stored BSN-masked.
- Reminders on the phone hold no letter text unless the member turned that on (the app's
  rule); no log line holds a task's description, amount or quote.

## Ongelezen and "Blijft liggen" (what you have read)

- Brievie keeps, per member and letter, that the member has read it: opened it on the
  letter page or in the app's reader, scanned or uploaded it, or marked it read. A plain
  look at a list, a refresh in the background or a script does not count. This mark is
  personal data about reading behaviour and is used for exactly two things: the member's
  own "Ongelezen" (bold rows, the number at Mijn post), and, in an organisation, whether
  any addressee has opened a confirmed letter, for the sorters and admins ("Nog niet
  geopend"). Only a member's own opening of the confirmed letter counts there: not a scan
  or upload, not "Alles gelezen", not an opening while the letter is still being checked,
  and not one through emergency access.
- What "Nog niet geopend" tells, exactly: that none of the letter's addressees opened it
  before today. A letter opened today leaves the list at the start of the next day, so a
  sorter can learn on which day the addressees (with one addressee: that person) first
  opened a letter, never the hour, nor which of several addressees did. Nobody else ever
  sees a member's own marks: the database lets each member read only their own rows. At
  home "Blijft liggen" does not exist.
- In het Nederlands: "Nog niet geopend" laat de postverdelers en beheerders van een
  organisatie zien dat geen van de geadresseerden een bevestigde brief vóór vandaag heeft
  geopend. Een brief die vandaag is geopend, verdwijnt pas de volgende dag uit de lijst.
  Zo kan een verdeler zien op welke dag de geadresseerden (bij één geadresseerde: die
  persoon) een brief voor het eerst openden, nooit het uur, en nooit wie van meerdere
  geadresseerden. Scannen, "Alles gelezen", openen tijdens het nakijken of met noodtoegang
  telt niet als openen.
- The marks are never in the letter's history, the log, an export or a log line. They go
  with the letter when it is purged, and for one member when they leave the workspace.
  "Blijft liggen" lists and counts only letters the viewer may read: a private letter they
  may not read is in no list and no number.

## Privé-post (private letters)

- A letter can be private: only its readers see it (whoever it is addressed to with an
  account, the members of its departments, whoever it is shared with, and whoever made it
  private or scanned it). A letter labelled Zorg becomes private on its own at home. The
  database itself keeps everybody else out (row-level security), not only the screens.
- What private protects against: the other members of the workspace, admins included.
  Nobody reads it unnoticed: an admin's emergency access, a grant (linking a person,
  adding a department member, an accepted invitation), a reset link or ended sessions
  each leave a history line on the letter or a notice the member sees.
- What it does not protect against: whoever administers the server machine (the database,
  the master key, the back-ups); the AI service, which reads a letter while it is
  processed, before it can be private; page files, which are sealed per workspace, not
  per reader.
- A workspace admin can learn **that** somebody has private post, never what: from four
  answers (`person_in_use` when deleting a person, `cannot_grant_self`, `email_required`
  on an invitation, `server_admin_only` for an account action on a reader of private
  post). Members cannot (they cannot delete persons).
- An invitation for a person with private post needs an e-mail address, which the server
  does not verify: an admin who controls that address could accept it; the grant then
  shows as a history line on every private letter of that person.
- Once a member reads private post, only the server admin may make a reset link for them,
  end their sessions or manage their devices.
- The log names a private letter by its id only.

## Reaching Brievie from outside

- With a **VPN** (Tailscale, WireGuard) the connection is encrypted from the phone to the
  home server; nobody in between can read it. This is the recommended way.
- With **Cloudflare Tunnel** (and with a reverse proxy at a hosting party) Cloudflare ends the
  encryption on its own servers: everything that passes through, including scanned pages,
  BSN-masked text and the app's device token, is readable there, and Cloudflare's terms apply.
- Through the public address only the app works by default; the web sign-in stays at home
  (`BRIEVIE_PUBLIC_WEB`, docs/SELFHOST.md section 25). The tunnel token stays in the
  `cloudflared` container: the api never has it, no log or page shows it.

## Honest limits

- The e-mail address on an invitation is not verified (above).
- An admin can reset a member's password (above): that is a real power, recorded but
  not preventable.
- The log is not secret from whoever runs the server: they own the database.
- Text in the database (recognised text, summaries, names) is not encrypted: put the
  database volume on an encrypted disk.
- A phone that is stolen works until it is revoked or 180 days pass without use. An
  admin or the member can revoke it at once.
- Two-step verification with an authenticator app does not stop a fake sign-in page that
  passes the code on at once (phishing). Passkeys would; they are not built yet.
- Codes depend on the server's clock: on a server without NTP they may fail. A server
  admin sees a warning when the server's clock is more than 30 seconds off.

## The master key

The master key unlocks every document. Without it the files cannot be read: keep a copy in
a safe place, apart from the back-up of the data. With `./brievie` it is `BRIEVIE_MASTER_KEY`
in `deploy/.env`. With only a compose file it is made on the first start and kept as a file in
the Docker volumes `secrets` and `secrets-api` (mode 0400, on the same disk as the data); it
is no variable of any container, so not in `docker inspect`, `docker compose config`, a log or
a process list, and the keeper (back-ups) has no copy. A theft of the disk or of a back-up
exposes what `deploy/.env` exposed before: the disk holds the key next to the data, a back-up
does not hold it. The recovery sheet (a server admin downloads it in the web interface after
typing the password again, never cached, recorded in the log) is the other copy; the key goes
over https to the browser of that admin, as the sheet file next to a checkout did before.
