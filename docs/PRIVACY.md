# Privacy: where your data is and what leaves the server

This describes the server as built (phase 1c, with recorded consent for AI). It is
updated with every phase.

## What is stored, and how

| Data | Where | Protection |
|---|---|---|
| Page images of a document | Files in the `documents` volume | Encrypted (AES-256-GCM) with a key per workspace; that key is stored wrapped with `BRIEVIE_MASTER_KEY` |
| API key of an external AI service | Database | Encrypted with the workspace key |
| Recognised text (OCR), proposals, summaries, tasks, persons (names and name variants), labels, rules, the history of changes | Database | **Not encrypted**: needed for review and (later) search. Protected by row-level security per workspace and by masking BSNs before anything is stored. Put the database volume on an encrypted disk |
| The decisions about AI (see "Consent for AI") | Database | Not encrypted; row-level security per workspace. No API key, no full address, no document text |
| Passwords, pairing codes, device tokens | Database | Only as hashes |

A BSN (citizen service number) is replaced by `[BSN]` before text is stored, logged or
sent to an AI service. The page image itself still shows it; that file is encrypted.

Logs never contain document text, names of persons or labels, rule names or values,
tokens or passwords.

Every member of a workspace sees all its post, whoever the letters are addressed to.
Visibility per document and "Zorg private by default" arrive when members can be invited;
until then a workspace has one account.

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

AI is off until an administrator switches it on with `manage.py configure_ai`.

- **Asked first.** Before a workspace is switched to `external`, the command says in
  Dutch what is sent with every letter (the table above), to which service, host and
  country (Mistral: France, EU; OpenAI and Claude: United States; another address:
  unknown, look it up yourself), that the service's own terms and retention apply, and
  that the administrator decides for everyone whose post is in the workspace. It goes on
  only after the word `akkoord` is typed, or with `--accept-external-ai` in a script,
  which states the same. Without either, nothing changes.
- **Recorded.** Every decision is a row the application only adds, never changes: who
  (`command line` today; a user once AI can be set in the web interface), when, the
  mode, the supplier, the host of the address and the model. Switching AI off, moving to
  another service and moving to the own network are rows too. The database itself does
  not forbid changing a row, and the rows go when the workspace is deleted.
- **Asked again for another address.** A consent covers one address (host, port and
  path). Another model or a new key at the same address is recorded without a new
  question; any other address needs a new `akkoord`. `local` is recorded without a question: nothing leaves the network.
- **Enforced.** The server sends text to an external service only while there is a
  consent for exactly the configured address, and it follows no redirect to another
  one. A workspace that was set to `external` before consent was recorded reads no
  letters with AI until `configure_ai` is run once more; letters scanned in between
  fail and are started again by hand.
- **Visible.** Every member sees on the Inbox and Post pages whether AI is on, which
  service and country the text goes to and since when. The API gives the same
  (`GET /api/v1/workspaces/{id}/ai`: mode, supplier, host, since), never the model, the
  full address or the key.
- **Limits.** Consent is the administrator's, for the whole workspace: there is no
  consent per member or per letter. Switching off stops sending; it does not take back
  what a service already received. The people who write to you are not asked.

The iPhone app recognises the text on the phone itself and uploads the pages and that
text to your server only.

## The master key

`BRIEVIE_MASTER_KEY` in `deploy/.env` unlocks every document. Without it the files
cannot be read: keep a copy in a safe place, apart from the back-up of the data.
