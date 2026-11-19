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
| Web sessions: client address, a short description of the browser ("Firefox op Windows"), start and last use | Database | Not encrypted; removed when the session ends or runs out |
| Devices: name, app version, when paired and last used | Database | Not encrypted |
| The audit log (see "The log") | Database | Not encrypted; row-level security per workspace; the application cannot change or delete rows |
| A member's export (see "The export") | Files in the `documents` volume, under `exports/` | Sealed (AES-256-GCM) while it is stored; not in the back-up |
| Back-ups | Only the destination you chose (a disk, a NAS share, a REST server or S3-compatible storage) | Encrypted by restic with the back-up password. The database dump is streamed into the back-up and never stored unencrypted outside the database. The master key is not in a back-up. See `docs/SELFHOST.md` |

A BSN (citizen service number) is replaced by `[BSN]` before text is stored, logged or
sent to an AI service. The page image itself still shows it; that file is encrypted.

Logs never contain document text, names of persons or labels, rule names or values,
tokens or passwords.

Every member and viewer of a workspace sees all its post, whoever the letters are
addressed to. Members can be invited now; visibility per document and "Zorg private by
default" are a later part. Until then, invite only people who may see everything.

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

## Honest limits

- The e-mail address on an invitation is not verified (above).
- An admin can reset a member's password (above): that is a real power, recorded but
  not preventable.
- The log is not secret from whoever runs the server: they own the database.
- Text in the database (recognised text, summaries, names) is not encrypted: put the
  database volume on an encrypted disk.
- A phone that is stolen works until it is revoked or 180 days pass without use. An
  admin or the member can revoke it at once.

## The master key

`BRIEVIE_MASTER_KEY` in `deploy/.env` unlocks every document. Without it the files
cannot be read: keep a copy in a safe place, apart from the back-up of the data.
