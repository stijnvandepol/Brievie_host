# Intake without an iPhone: web upload and the scan folder

Date: 2026-11-20. Branch `docs/intake-and-2fa-design` (host). Design only; no code yet.

## Intent

Today only the iPhone app can add a letter (`createDocument`: JPEG pages plus the text the
phone recognised). Looking at Brievie as an organisation, that is the first wall: the office
has a multifunction scanner, half the staff carry Android phones, and a PDF that arrives
digitally (a tax assessment, an invoice by mail) has to be printed and scanned again. At home
it is a smaller gap, but a real one: a PDF on the laptop cannot get in.

This design adds two ways in, both feeding the **same pipeline** as an app scan:

- (a) **Upload in the web interface**: one or more PDF, JPEG or PNG files.
- (b) **The scan folder** ("scan naar map"): the office scanner writes to a folder on the
  NAS, Brievie picks the files up.

and an outlook for (c) **PDF by e-mail**, which is not in v1.

SPEC section 8 already lists "Webupload (slepen) en een watch-map op de NAS" and IMAP; SPEC
section 16 put them in 1d, and `docs/DECISIONS.md` (1b, Open items) deferred server-side OCR
to "web upload and the watch folder". `Document.Source` already has `upload`, `mail` and
`watch_folder`. This spec fills that in.

## Goals, non-goals, anti-goals

- Goal: a member adds a PDF, JPEG or PNG from any browser (laptop, Android, iPad) and gets a
  letter that is processed exactly like a scan: sealed on disk, BSN-masked, AI only under the
  recorded consent, autopilot when it is on, rules, proposals.
- Goal: an office scanner that can "scan to folder" (SMB) delivers letters without anybody
  opening Brievie, with an optional folder per person that sets the addressee.
- Goal: home needs nothing new. Upload is a button on a page that already exists; the scan
  folder is off and invisible until the server owner sets one environment variable.
- Non-goal (v1): splitting one file into several letters; duplicate detection; e-mail intake;
  an Android app; OCR quality tuning per hardware profile (PaddleOCR, docling); HEIC/TIFF.
- Anti-goal: a second pipeline, or a shortcut that skips masking, sealing or consent for
  "just a PDF".
- Anti-goal: Brievie deleting or moving a file it did not import, or anything outside the
  configured folder.
- Anti-goal: a user recorded as having added a letter that a scanner dropped.
- Anti-goal: unencrypted copies of letters kept around by Brievie longer than needed.

## Constraints found in the code

- `createDocument` takes JPEG pages **and their text**: there is no server-side OCR today. The
  pipeline (`apps.pipeline.steps.run_pipeline`) reads `Document.ocr_text` and nothing else.
  So both new ways need a step that turns a file into pages and text **on the server**.
- Pages are JPEG files sealed per workspace (`<ws>/<doc>/<n>.page`, AAD
  `<ws>/<doc>/<n>`); viewers and the member export read those. A PDF must end up as the same
  pages, or every reader changes.
- Uploads never touch the disk unencrypted (memory upload handler, 32 MiB
  `BRIEVIE_MAX_UPLOAD_BYTES`, 30 pages `BRIEVIE_MAX_PAGES`, limits in Caddy and middleware).
- The worker runs as uid 10001 (`deploy/Dockerfile`), mounts only the `documents` volume, and
  serves the `pipeline` queue. A bind mount with an optional host path already exists as a
  pattern: the back-up service's `${BRIEVIE_BACKUP_DIR:-backup-unset}:/backup` with
  `BRIEVIE_BACKUP_DIR_SET`.
- Roles: `member` scans and organises, `viewer` only reads (1d 6.1).

## Design

### 1. The shared part: file intake

One new function takes a file and makes a `processing` document of it; both ways call it.

```
apps/intake/            # new app, as SPEC section 11 already names it
  services.py           # create_document_from_file(...)
  files.py              # sniffing, page count, limits (pure, no database)
  render.py             # PDF/PNG/JPEG -> JPEG pages + text (in the worker)
  ocr.py                # Tesseract behind an interface with a fake for tests
  inbox.py              # the scan folder job (part 3)
  inbox_files.py        # the ONLY code that reads, removes or moves files on the share
```

`create_document_from_file(workspace, file_bytes, kind, client_id, source, scanned_by,
original_name, preset_addressee=None)`:

1. Checks the file (section 1.2) **before** anything is stored.
2. In one transaction: inserts the `Document` (`status=processing`, `source`, `scanned_by`,
   empty `ocr_text`, `page_count=0`), writes the **original** sealed at
   `<ws>/<doc>/original` with AAD `<ws>/<doc>/original`, records `original_kind`,
   `original_sha256`, `original_size`, and queues `process_document`. The cleanup of written
   files on failure is the one `create_document_from_upload` already has.
3. With a preset addressee (scan folder, per-person folder): adds it through
   `apps.documents.organise` with `actor=None`, origin `watch_folder`, link marked
   `automatic` (section 3.6).

**Besluit: the request stores the original; the worker makes the pages.** Rendering a PDF and
OCR take seconds per page; a request must not. The document shows as "Wordt verwerkt" at once,
like a scan does.

**Besluit: keep the original, sealed.** A PDF that arrived digitally has vector text and
sometimes a signature; turning it into JPEG pages only would lose quality and the thing a
person may need to forward or keep for the tax office. The pages stay the common format
every reader uses; the original is extra and downloadable ("Origineel downloaden"). Cost:
roughly double the storage for uploads. App scans have no original (`original_kind` empty).

#### 1.1 The pipeline gets a first step: prepare

`process_document` (the existing job, same retries, same "never stays processing" guarantee)
runs a new step before `run_pipeline` when the document has an original and no pages:

1. Open the original (`open_sealed`), check its SHA-256 against `original_sha256`.
2. Render pages (`render.py`):
   - PDF: `pypdfium2` renders each page at 200 dpi, longest side capped at 2500 px, JPEG
     quality 85. Text per page: the PDF's own text layer when it holds at least 20
     non-space characters on that page; otherwise OCR.
   - JPEG: kept as is when `jpeg_size` accepts it (orientation from EXIF applied); else
     re-encoded. PNG: converted to JPEG (Pillow).
   - OCR: the `tesseract` binary (languages `nld+eng`) through `subprocess` with a timeout
     of 60 s per page, no shell, input on stdin. Behind an interface with a fake for tests
     (SPEC 19: external calls behind an interface, timeout, bounded retries).
3. Mask BSNs in every page text (`mask_bsns`), then in one short transaction under the
   document's row lock (only while it is still `processing`): write the sealed pages and the
   `DocumentPage` rows, `page_count`, `ocr_text`. Files written before a failed commit are
   removed, as in `create_document_from_upload`.
4. Continue with `run_pipeline` unchanged: AI under the consent rules, proposals, rules,
   autopilot.

Failures are permanent errors with a code, so `mark_failed` shows them like any failed
document: `file_unreadable` (damaged or not what it claimed), `file_encrypted` (PDF with a
password), `too_many_pages`, `ocr_failed` (Tesseract missing or timed out on every page). A
page without any text is not an error: the letter gets no proposals and a person fills it in,
as with an empty scan today.

**Besluit: Tesseract directly, not OCRmyPDF.** SPEC section 10 names OCRmyPDF. We do not need
its product (a searchable PDF); we need page images and text, and we already keep the
original. OCRmyPDF pulls in Ghostscript, qpdf and more for nothing we use. Tesseract alone
is one apt package plus the Dutch language data, multi-arch in Debian. If OCR quality turns
out poor, PaddleOCR or docling slot in behind the same interface (SPEC hardware profiles).

**New dependencies** (each needs its `docs/DECISIONS.md` line before it is added):
`pypdfium2` (Apache-2.0/BSD-3; PDFium, the PDF engine of Chrome, heavily fuzzed; wheels for
amd64 and arm64, no system libraries), `Pillow` (HPND; PNG decoding, JPEG encoding), and the
Debian packages `tesseract-ocr` and `tesseract-ocr-nld` (Apache-2.0) in the image. Rejected:
`pypdf` for rendering (it cannot render), poppler/`pdftoppm` (an extra system tool with a
weaker security record than PDFium), PyMuPDF (AGPL-compatible but commercial dual licence and
a large binary).

#### 1.2 Checks before anything is stored

In `files.py`, pure:

- Kind from the **content**, not the name or the browser's type: `%PDF-` → PDF, `FF D8 FF` →
  JPEG, PNG signature → PNG. Anything else: 422 `file_type_not_supported`.
- Size: the existing `BRIEVIE_MAX_UPLOAD_BYTES` (32 MiB) per file.
- PDF: opened with `pypdfium2` to count pages only (no rendering): more than
  `BRIEVIE_MAX_PAGES` (30) → 422 `too_many_pages`; encrypted → 422 `file_encrypted`;
  cannot be opened → 422 `file_unreadable`.
- Images: dimensions read from the header; more than 40 megapixels → 422 (decompression
  bombs); Pillow's `MAX_IMAGE_PIXELS` set to the same value as a second guard.
- The original file name is kept for the person's recognition only (`original_name`, at
  most 200 characters, BSN-masked), never logged and never sent to an AI service.

#### 1.3 One file is one letter (v1)

**Besluit: in v1 one file becomes one letter.** A PDF with three letters becomes one document
with all their pages. Splitting needs either a separator page, a page-boundary guess by the
AI, or a split screen; each is a design of its own. The upload page says so plainly:

> Eén bestand wordt één brief. Zitten er meer brieven in één bestand? Scan of bewaar ze dan
> los.

For the scan folder the same sentence goes into the set-up text ("Zet je scanner op één
bestand per brief."). Most office scanners have a "split per document" or "one file per scan"
setting; batch scanning a pile into one PDF is the case we do not handle yet.

#### 1.4 Duplicates

Not detected in v1 (owner: later). `original_sha256` is stored and indexed per workspace, so a
later check ("Deze brief staat er al in, van 3 november") is a query, not a migration.

### 2. Web upload

- **Who**: role `member` or `admin` of the workspace (as scanning). A viewer does not see the
  button.
- **Where**: a button "Brief toevoegen" on the post page, and dropping files anywhere on that
  page. A file picker with `accept="application/pdf,image/jpeg,image/png"` and `multiple`. On
  Android and iPad the browser's picker also offers the camera.
- **How**: the browser sends **one request per file** (`uploadDocumentFile`), at most three at
  a time, with a progress line per file. **Besluit: one request per file**, not one request
  for the whole batch: each file succeeds or fails on its own, a retry repeats one file, and
  the existing per-request limit (32 MiB in Caddy and middleware) applies unchanged. A batch
  in the browser is at most 50 files; more is refused in the browser ("Kies maximaal 50
  bestanden tegelijk.").
- **Idempotent**: the browser makes a `client_id` (UUID) per file; the same `client_id` with the
  same `original_sha256` answers 200 with the existing document, another file under that id
  409 `document_conflict` (same rule as `createDocument`).
- `scanned_by` is the uploader; `source=upload`. The rest is the pipeline.
- Auth: a session with CSRF **or** a device token, like `createDocument`. Not session-only: this
  is not administering people, and a future Android app (or a script) can use the same
  endpoint.

Texts (B1):

- Button: "Brief toevoegen"; drop zone: "Sleep PDF's of foto's hierheen".
- Per file while busy: "Bezig met uploaden…", then "Toegevoegd. Brievie leest de brief nu."
- Errors: "Dit bestand kan Brievie niet lezen. Gebruik een PDF, JPG of PNG.", "Deze PDF heeft
  meer dan 30 pagina's.", "Deze PDF is beveiligd met een wachtwoord. Haal de beveiliging
  eraf en probeer het opnieuw.", "Dit bestand is groter dan 32 MB."

### 3. The scan folder

#### 3.1 Switching it on (server owner, then workspace admin)

- **Server**: `BRIEVIE_INBOX_DIR` in `deploy/.env`, a host path (for Synology:
  `/volume1/scans/brievie`). Compose maps it into a **new service** `intake` (same image,
  `procrastinate worker --queues intake`) at `/inbox`; unset, a named volume `inbox-unset` is
  mapped instead and `BRIEVIE_INBOX_DIR_SET` is empty, exactly the back-up pattern. Unset is
  the default: nothing about a scan folder exists.
- **Workspace**: page "Scanmap" under settings, admin only, shown only when the server has a
  scan folder. The admin switches it on and chooses the folder name (default: the workspace
  name in lower case, letters, digits and `-` only, e.g. `kantoor`). Optional: "Map per
  persoon" (off). Each change is one audit row `workspace_setting_changed` (`setting`
  = `inbox_folder` / `inbox_per_person`, `value` = on/off; the folder name is not in the
  row: workspace names are allowed, but a folder name can be anything).

**Besluit: a separate `intake` service**, not the existing worker. Only this container mounts
the share; the pipeline worker never sees it. It can get its own supplementary group for the
share's rights (3.7) without touching the `documents` volume's owner, and a stuck SMB mount
cannot stall document processing. It needs the master key (it seals the original) and the
`documents` volume, like the worker.

**Besluit: the folder per workspace has a chosen name, not the workspace id.** A scanner's
display or a Synology share listing shows the name; a UUID is unusable there. Names are
unique across the server (one root for all workspaces), enforced by a unique index;
a taken name answers 409 `inbox_folder_taken`.

#### 3.2 Layout on the share

```
/inbox/                         = BRIEVIE_INBOX_DIR
  kantoor/                      workspace "Kantoor": files here become letters
    Jan de Vries/               only with "Map per persoon": addressee preset to Jan
    Anne/
    mislukt/                    files Brievie could not take in, with a reason file
```

- The `intake` service creates the workspace folder, `mislukt/` and the person folders when
  they are missing (a person folder's name is the person's display name, made safe for SMB:
  no `\ / : * ? " < > |`, at most 60 characters; two persons with the same safe name get
  ` (2)`). A renamed person keeps their folder (the mapping is by id, stored in
  `InboxFolder`); a deleted person's folder is no longer read, and its files are moved to
  `mislukt/` with reason `person_gone`.
- Reserved names that are never a person folder: `mislukt`, and anything starting with `.`,
  `@` (Synology's `@eaDir`), `#` (`#recycle`) or `~`.
- Only files directly in a workspace folder or a person folder are read. Deeper folders are
  ignored (not an error).

#### 3.3 Picking up a file

A periodic job on the `intake` queue, every minute (`*/1`), and per workspace with the scan
folder on:

1. **List** candidates through `inbox_files.list_candidates` (3.5): regular files with
   extension `.pdf`, `.jpg`, `.jpeg` or `.png` (case-insensitive), not hidden, not ending in
   `.tmp`/`.part`.
2. **Stable check.** Each sighting is recorded in `InboxFile` (path relative to the root,
   size, mtime in ns, device and inode). **A file is taken only when size and mtime are the
   same at two looks at least 60 seconds apart, and the size is above zero.** Scanners write
   over SMB in pieces; one minute of quiet is long for a scanner and short for a person.
3. **Read** it (`inbox_files.read_candidate`): at most `BRIEVIE_MAX_UPLOAD_BYTES`; the
   fingerprint (device, inode, size, mtime) is checked again on the open descriptor. A file
   that changed between the look and the read waits for the next round.
4. **Import** with `create_document_from_file`: `source=watch_folder`, `scanned_by=None`,
   `original_name` = the file name, `client_id` = UUID5 of (workspace, relative path, SHA-256,
   mtime). That id makes a re-import after a crash between commit and removal answer with the
   existing document instead of a second one.
5. **Verify** after the commit: read the sealed original back, open it, compare SHA-256.
6. **Remove** the source (`inbox_files.remove_imported`, 3.5) and mark the `InboxFile` row
   `imported` with the document id.
7. A file that fails the checks of 1.2, or whose import raises a permanent error, is
   **moved** to `mislukt/` (`inbox_files.move_to_failed`) with a small text file next to it
   (`<naam>.reden.txt`, Dutch, B1: "Brievie kon dit bestand niet lezen: de PDF is beveiligd
   met een wachtwoord."), and the `InboxFile` row records the reason code. A database error
   or a full disk is not the file's fault: the file stays where it is and the next round
   tries again.

The import itself runs in its own short transaction per file. Nothing is open while files are
read. A workspace's round stops after 20 files (the rest next minute), so one big batch does
not starve other workspaces.

#### 3.4 Remove, not move to `verwerkt/`

**Besluit: after a verified import the source file is removed.** No `verwerkt/` folder in v1.

- The point of Brievie's storage is that letters are not lying around unencrypted. A
  `verwerkt/` folder is exactly that: every letter of the office, readable by everybody with
  rights on the share, for the whole purge period. That is worse than the problem it
  solves.
- Nothing is lost: Brievie keeps the original, sealed, byte for byte (1), and it is
  downloadable from the letter.
- Removal happens only after the commit **and** a read-back of the sealed original. Between
  import and removal the file is still there; a crash in that window leads to the
  idempotent re-import of step 4, which finds the existing document and removes the source.
- Note for the admin: a file removed by a container is **not** placed in Synology's
  `#recycle`; the recycle bin only covers deletions through SMB, AFP or File Station. The
  set-up text says that the letter is then in Brievie, not in the recycle bin.

Rejected: move to `verwerkt/` with a purge period (unencrypted copies, a second erase path that
runs on a timer, and a purge that removes files Brievie did not just verify); keep the source
and remember it (the share fills up and stays a plain copy of all post). A `verwerkt/` option
with a short purge period is cheap to add later if an office asks for it; it would be a
setting with a warning, not the default.

`mislukt/` files are **never** removed or moved by Brievie: they were not imported, so
removing them would lose a letter. The "Scanmap" page shows how many there are with their
reason and date; the admin puts a file back in the folder (it is tried again) or removes it
by hand.

#### 3.5 Guarded file functions (erase code)

This part removes and moves files, so the binding safety rules for erase code apply.
`apps/intake/inbox_files.py` is the **only** code that touches the share, and each function
refuses on its own, in code, before it acts:

- **Root**: the root is `settings.BRIEVIE_INBOX_ROOT` (`/inbox` in the container), resolved
  once with `realpath`. Refused when it is not an existing directory, is `/`, equals or
  contains `BRIEVIE_STORAGE_ROOT`, or lies inside it, or when `BRIEVIE_INBOX_DIR_SET` is
  empty. Refusal raises `InboxRefused(code)`; the job logs the code only.
- **Paths**: a path is a tuple of name components relative to the root, never a string that
  is joined. Each component must be non-empty, not `.` or `..`, contain no `/` or NUL, and
  not start with `.`; at most two components below the workspace folder.
- **No symlinks anywhere**: the walk opens the root directory, then each component with
  `os.open(name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW, dir_fd=parent)`; files with
  `O_NOFOLLOW`. A symlink at any level fails the open and is refused, not followed.
- **Only regular files**: `fstat` on the opened descriptor: `S_ISREG`, `st_nlink == 1` (a hard
  link could make a file outside the root readable through the share), allowed extension,
  size within the limit.
- **Remove** (`remove_imported(path, expected)`): only a file whose `InboxFile` row is
  `imported` and whose current (device, inode, size, mtime) equal the fingerprint that was
  imported; then `os.unlink(name, dir_fd=parent_fd)`. Anything else: no removal, a code.
- **Move** (`move_to_failed(path, reason)`): `os.rename` within the same workspace folder into
  `mislukt/` with `src_dir_fd`/`dst_dir_fd`; never across workspaces, never overwriting (a
  name that exists gets a number). The reason file is created with `O_CREAT | O_EXCL |
  O_NOFOLLOW`.
- **Never recursive**: no `rmtree`, no glob, no removal of directories, ever.
- **Tests run only in pytest's `tmp_path`**: a fixture sets the root to a fresh `tmp_path`
  directory and an autouse guard fails any test where the root is not under the session's
  temporary directory. Tests cover each refusal: `..`, absolute names, a symlinked file, a
  symlinked directory at each level, a hard link, a FIFO, a file that changed after import, a
  root equal to or inside the storage root, a root of `/`.
- **Real proof only in a container**: a CI job runs the `intake` service in Compose with a
  bind-mounted temporary folder, a different owner uid, and a symlink pointing outside, and
  checks that the outside file still exists afterwards. Never against a folder on a
  developer's Mac outside a temporary directory, never against the test server `brievie`.

#### 3.6 Who did it

Nobody. A letter from the scan folder has `scanned_by = null` and `source = watch_folder`;
the history line for a preset addressee has no actor and origin `watch_folder` (new value of
`DocumentEvent.Origin`), and the `DocumentAddressee` row is `automatic`, so it can be undone
like an autopilot addition. No audit row per file: the audit log records what people and
administrators did; the intake of a letter is visible on the letter itself ("Binnengekomen via
de scanmap") and in the "Scanmap" overview.

The preset addressee comes from the folder, not from the letter, so it is set **before** the
pipeline runs; name matching then sees an addressee that is already there and proposes nothing
for that person. The autopilot (when on) treats it as any existing addressee.

Visibility: the workspace default (as for every letter until part B of 1d). When private post
exists, "Map per persoon" may get the option "Post in deze map is privé"; not in v1.

#### 3.7 Rights on the share (UID/GID) and Synology without SSH

The container runs as uid 10001. On a Synology the shared folder belongs to DSM users and its
rights are Synology ACLs, which the kernel enforces against the process's uid and groups. A
container user that matches no DSM user or group gets "permission denied".

- Compose gives the `intake` service `group_add: ["${BRIEVIE_INBOX_GID:-100}"]`. 100 is DSM's
  `users` group, which every DSM user is in.
- Set-up on a Synology, with DSM and Container Manager only (no SSH):
  1. In DSM, make a shared folder (e.g. `scans`) and a folder `brievie` in it.
  2. Make a DSM user for the scanner (`scanner`) with read/write on that shared folder only.
  3. On the folder `brievie` (File Station → Eigenschappen → Machtiging): give the group
     `users` read and write, including "Verwijderen" for subfolders and files.
  4. In Container Manager → Project → Brievie → edit the `.env`: `BRIEVIE_INBOX_DIR=/volume1/scans/brievie`
     (and `BRIEVIE_INBOX_GID` only when another group was used).
  5. Rebuild the project. In Brievie → Instellingen → Scanmap: switch on, choose the name.
  6. In the scanner: "scan naar map" via SMB to `\\<nas>\scans\brievie\kantoor`, as user
     `scanner`, one file per document, PDF.
- **The page checks what the worker sees.** Since the admin cannot run `id` or `ls -l`, the
  "Scanmap" page shows the `intake` service's own check: the folder exists, can be read, a
  probe file (`.brievie-check`, made with `O_EXCL` and removed through the guarded remove)
  can be written and removed, and the folder's owner uid/gid. Texts: "Brievie kan de scanmap
  niet lezen. Geef de groep 'users' lees- en schrijfrechten op deze map." / "De scanmap werkt."
- Linux + Docker: the same variable; the docs show `chgrp` and `chmod g+rwxs` on the folder.
- SMB1: many older scanners only speak SMB1. The docs advise against switching SMB1 on in DSM
  (it is insecure); a scanner that can only do SMB1 can often send via FTP(S) to DSM instead,
  into the same folder.

**Open to verify on a real Synology** (left to the builder, with the owner's NAS): that a DSM
ACL granting `users` read/write is honoured for a container process with supplementary gid
100, and whether the scanner-created files get ACLs that allow removal by that group.

#### 3.8 Unencrypted while waiting

A letter on the share is unencrypted from the moment the scanner writes it until Brievie
removes it: normally one to two minutes, longer when the `intake` service is down, and
indefinitely for a file in `mislukt/`. This is stated in `docs/PRIVACY.md` with the advice:
rights on the folder for the scanner account and the `users` group only, not for every staff
member; SMB encryption on in DSM; the "Scanmap" page shows a warning when files have been
waiting for more than an hour ("Er wachten 4 bestanden langer dan een uur. Draait Brievie?")
and when `mislukt/` is not empty.

### 4. E-mail (outlook, not in v1)

Two ways a PDF could arrive by mail:

- **IMAP polling of a dedicated mailbox** (e.g. `post@kantoor.nl` at the existing provider):
  the `intake` service logs in every few minutes, takes PDF attachments, files the message in
  an IMAP folder. No open port, works behind NAT and on a home network, spam and SPF/DKIM are
  the provider's job.
- **Inbound SMTP** in Brievie: needs port 25 reachable from the internet, an MX record, and
  Brievie doing spam and sender checks. Not something a home server or a small office should
  run.

**Recommendation: IMAP polling**, with an allow-list of sender addresses per workspace (a
mailbox address leaks; anybody could otherwise put letters in), only PDF/JPEG/PNG attachments,
the mailbox password sealed like the AI key (write-only), and messages moved to a folder
"Brievie/verwerkt" in the mailbox rather than deleted. The real cost is sign-in: Microsoft 365
and Google no longer accept a plain password over IMAP, so OAuth2 per provider is needed. That,
not the parsing, is why it is not v1. The MijnOverheid notification idea (SPEC 8) builds on the
same mailbox.

### 5. Android

Said plainly: **in v1 web upload is the Android answer.** The web interface works in a phone
browser; "Brief toevoegen" there offers the camera or a scanner app's PDF. It is not a
document scanner (no edge detection, no perspective fix), so Android users get better results
from a scanner app that saves a PDF and then uploading that. An Android app stays later (SPEC
11); `uploadDocumentFile` accepts device tokens, so such an app would need no new endpoint.

## Data model

- `Document` (existing table, additive): `original_kind` (`""`, `pdf`, `jpeg`, `png`; default
  `""`), `original_sha256` (char 64, blank), `original_size` (int, null), `original_name`
  (char 200, blank), index on (`workspace`, `original_sha256`). `scanned_by` is already
  nullable.
- `DocumentEvent.Origin`: new value `watch_folder`.
- `InboxSettings` (workspace content table, `WorkspaceModel`, RLS forced): `enabled` (false),
  `folder_name` (unique across the server), `per_person` (false).
- `InboxFolder` (workspace, RLS): `person` (FK, null for the workspace folder itself),
  `name` on disk; unique (`workspace`, `person`).
- `InboxFile` (workspace, RLS): `relative_path`, `size`, `mtime_ns`, `device`, `inode`,
  `first_seen_at`, `state` (`seen`, `imported`, `failed`), `reason` (code), `document` (FK,
  null). Rows of `imported` older than 30 days are purged by the job; `failed` rows stay while
  the file is in `mislukt/`.
- Settings: `BRIEVIE_INBOX_ROOT` (`/inbox`), `BRIEVIE_INBOX_DIR_SET`, `BRIEVIE_INBOX_STABLE_SECONDS`
  (60), `BRIEVIE_OCR_TIMEOUT_SECONDS` (60).

## API sketch (additive, `/api/v1`)

- `POST /workspaces/{id}/documents/files` — `uploadDocumentFile`. Multipart: `file` (one), form
  fields `client_id` (UUID). 201 `DocumentOut` (new) / 200 (same file again) / 409
  `document_conflict` / 413 / 422 (`file_type_not_supported`, `file_encrypted`,
  `too_many_pages`, `file_unreadable`, `validation_error`). Role member. Session (CSRF) or
  device token. The path joins the multipart allow-list of `ApiJsonBodyMiddleware` and gets
  its own Caddy `handle` block with the upload limit.
- `GET /workspaces/{id}/documents/{document_id}/original` — `downloadDocumentOriginal`. The
  original, opened; 404 when there is none. `Cache-Control: no-store`.
- `DocumentOut` / `DocumentSummaryOut`: `source` (new in the API, default `scan`; the model
  field exists), `has_original` (false), `original_name` (`""`).
- `GET /workspaces/{id}/inbox` — `getInboxSettings` (admin, session): `available` (server has
  a folder), `enabled`, `folder_name`, `per_person`, `check` (`ok` / `not_readable` /
  `not_writable` / `missing` / `unknown`), `owner_uid`, `owner_gid` (int, default 0),
  `waiting_count`, `oldest_waiting_minutes` (int, default 0), `failed` (list of
  `InboxFailureOut`: `name`, `reason`, `failed_at`).
- `PUT /workspaces/{id}/inbox` — `updateInboxSettings` (admin, `auth=session_auth`):
  `enabled`, `folder_name`, `per_person`. 409 `inbox_folder_taken`, 409 `inbox_unavailable`.
- No endpoint removes or moves a file on the share.

Every new field has a default; nullable fields are primitives; no `anyOf`. iOS: no change
needed; the app may later show `has_original` and `source`.

## Security and privacy

- Same guarantees as a scan: sealed before disk (the original too), BSN masked before storing
  text, AI only under recorded consent (`run_pipeline` unchanged), no content or file names in
  logs (codes and document ids only).
- Untrusted files: kind sniffed from content, page count and pixel limits before rendering,
  PDFium in the worker (not in the API process except for the page count), Tesseract with a
  timeout, no shell. The API's page count opens the PDF in memory with a time limit; a PDF
  that takes longer than 5 s to count is refused as `file_unreadable`.
- Scan folder: guarded functions (3.5), separate container, only the share and the documents
  volume, no published port. Plain letters wait on the share for about two minutes (3.8).
- One share for all workspaces: whoever can write to `/inbox` can put a letter into any
  workspace's folder. For a home or one office that is fine; separate trust needs separate
  servers or a later per-workspace mount. Stated in `docs/SELFHOST.md`.

## Home and organisation defaults

| | Home | Organisation |
|---|---|---|
| Web upload | on for members, nothing to set up | same |
| Scan folder | off; does not show | server owner sets `BRIEVIE_INBOX_DIR`, admin switches on per workspace |
| Folder per person | off | optional |
| E-mail | not available | not available (outlook) |

## Risks

- **OCR quality** of Tesseract on poor scans is lower than VisionKit on the phone. Mitigation:
  scanners at 300 dpi give Tesseract good input; the interface allows a better engine later.
- **Image size** grows (Tesseract + Dutch data, PDFium, Pillow: roughly 60–80 MB). Acceptable;
  a "Licht" profile can still run it.
- **CPU on a small NAS**: OCR of a 30-page PDF can take minutes on an ARM NAS. It runs in the
  queue; the letter shows "Wordt verwerkt". `ABANDONED_AFTER` (15 minutes) must exceed the
  worst case: 30 pages × 60 s is 30 minutes. **Raise `ABANDONED_AFTER` to 45 minutes**, or
  limit OCR per job and continue in a second job; the builder measures on a DS220+ class
  device first.
- **Synology ACLs** may not behave as assumed (3.7); the page's check makes it visible.
- **Deleting**: a bug in the scan folder code removes a letter. The guards (3.5), removal only
  after a verified read-back, and `mislukt/` never touched keep the blast radius to "a file
  that Brievie already holds sealed".

## Test approach

- Tests first for `files.py` (sniffing, limits, encrypted, bombs) and the stable check (pure:
  given two sightings, decide).
- `render.py` with small fixture PDFs: text layer, scanned-only page, mixed, rotated, 31 pages,
  encrypted, damaged; OCR through the fake; one integration test with real Tesseract marked
  for the container CI job.
- `uploadDocumentFile`: roles (viewer 403), idempotency, conflict, limits, CSRF, device token,
  that nothing is written on a refused file, that the original is sealed with its AAD.
- Pipeline: a document from upload ends exactly like a scan (proposals, autopilot on and off,
  consent off → no AI call); failures end `failed` with their code.
- Scan folder: `inbox_files.py` refusals (3.5) in `tmp_path` only; the job with a fake clock:
  unstable file waits, stable file imported and removed, crash between commit and removal
  re-imports idempotently, bad file to `mislukt/` with reason, `mislukt/` never touched, person
  folder presets the addressee with origin `watch_folder` and no actor.
- Container CI job (3.5): uid mismatch, symlink to outside, outside file survives.
- Contract test: additive only, no `anyOf`, defaults on new fields.

## Left to the builder

Private helper names, test layout, migration order, the exact render resolution after a quick
quality check, the Caddy block, the web components (reuse the Fluent 2 upload patterns), the
exact Dutch of texts not quoted here (B1, through Lingui and `gettext`), and verifying 3.7 on a
real Synology.

## Calls made on the owner's behalf

1. Keep the original file sealed next to the JPEG pages (storage cost for uploads roughly
   doubles).
2. Tesseract directly instead of OCRmyPDF; new dependencies `pypdfium2`, `Pillow`, Tesseract
   (DECISIONS lines needed).
3. Remove the source after a verified import; no `verwerkt/` folder; `mislukt/` is never
   touched by Brievie.
4. A separate `intake` container for the scan folder, with `BRIEVIE_INBOX_GID` (default 100).
5. Folder names chosen per workspace, unique per server; one share for all workspaces.
6. Web upload also accepts device tokens (ready for an Android app).
7. A preset addressee from a person folder is marked `automatic` and can be undone.
8. IMAP polling recommended for e-mail later; not in v1.
