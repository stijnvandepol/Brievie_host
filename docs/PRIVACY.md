# Privacy: where your data is and what leaves the server

This describes the server as built (phase 1c). It is updated with every phase.

## What is stored, and how

| Data | Where | Protection |
|---|---|---|
| Page images of a document | Files in the `documents` volume | Encrypted (AES-256-GCM) with a key per workspace; that key is stored wrapped with `BRIEVIE_MASTER_KEY` |
| API key of an external AI service | Database | Encrypted with the workspace key |
| Recognised text (OCR), proposals, summaries, tasks, persons (names and name variants), labels, rules, the history of changes | Database | **Not encrypted**: needed for review and (later) search. Protected by row-level security per workspace and by masking BSNs before anything is stored. Put the database volume on an encrypted disk |
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
workspace's own labels and persons can be chosen. Rules are never sent. With `off`,
names are still recognised in the letter and rules still run, on the server itself.

The iPhone app recognises the text on the phone itself and uploads the pages and that
text to your server only.

## The master key

`BRIEVIE_MASTER_KEY` in `deploy/.env` unlocks every document. Without it the files
cannot be read: keep a copy in a safe place, apart from the back-up of the data.
