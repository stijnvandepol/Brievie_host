# "Beheer": back-up and restore, export and archive folder, updates and releases: design

Date: 2026-11-17. Covers `Brievie_host` (and what changes in `Brievie_site` and
`Brievie_ios` because of it). Design only. Nothing here creates tags, releases or
packages on GitHub; that is the owner's step.

The owner asked for this in three sentences: "Denk ook na over hoe updates op de host
gaan plaatsvinden", "Denk ook na over backups", and, for a NAS, "de mogelijkheid om die
data direct per gebruiker naar hun eigen map te verplaatsen zo staat alles direct
afgeschermd". He delegated the choices ("Regel dit allemaal, zoek dit van tevoren uit
wat hier het beste voor is"). Every choice below is the designer's, made after reading
the sources named with it; each says what it costs if it turns out wrong. The source
notes are in the session notes folder: `onderzoek-backup.md`, `onderzoek-updates.md`,
`onderzoek-nas-mappen.md`, `onderzoek-host-stand.md`.

## 1. Intent

A Brievie server holds a household's or a company's post. Today (checked in the code
and on the site):

- **Losing it is one mistake away.** There is no back-up command. The site gives one
  that was never tested, has no restore, stores the letters' text unencrypted, and is
  tied to one PostgreSQL version. Losing `BRIEVIE_MASTER_KEY` loses every document;
  losing Caddy's volume makes every phone pair again.
- **Updating is `git pull` and a rebuild** on the user's machine, with no version
  numbers, no back-up first and no way back.
- **The data sits in Brievie only.** There is no export, and on a NAS the files are
  sealed blobs in a Docker volume.

The product principle holds: a simple default, an optional advanced layer on the same
core. At home: one command, one sheet of paper, a warning when the back-up is old. For
an organisation: retention, back-ups that cannot be overwritten, a restore drill, a
pinned version.

## 2. Goals, non-goals, anti-goals

**Goals**

- G1. One command makes a complete, encrypted, verified back-up; one command restores
  it on an empty machine, and the phones stay paired.
- G2. The restore is tested: by CI on every change, and by the admin with one button.
- G3. The admin sees when the last good back-up was, and is warned when it is old.
- G4. Everything can be taken out in an open form (files plus readable data).
- G5. Optionally, a readable copy of each letter in a folder per person on the NAS.
- G6. Releases with version numbers, prebuilt images, and one update command that
  backs up first and says how to go back.
- G7. Every released app works with an agreed range of server versions, and both
  sides can say so in a plain sentence.

**Non-goals**

- A back-up service run by us; any destination that is not the user's own.
- Updating and restoring from the web interface (spec 11 and 16). The web starts a
  back-up, reports and warns, and shows what to type. Giving the web process control
  over Docker, or the superuser's password, is a risk the product does not need yet.
  The cost is stated in 4.5: restoring and updating need a shell on the machine.
- Automatic updates.
- Zero-downtime updates: a server is away for a minute.
- Rotating the master key. The file format leaves room (`BRV1`); it is its own design.
- Two-way synchronisation with a folder (section 6.4).
- Deleting documents and retention of documents (phase 2).

**Anti-goals**

- A back-up that was never restored.
- A back-up that can be read without a secret, or a secret that lies next to it.
- A restore that silently misses rows (row-level security) or files.
- An update that leaves a half-migrated server and no instruction.
- A server that calls out to the internet without being asked.
- A readable folder that suggests more protection than it gives.

## 3. Constraints and facts

`CLAUDE.md` and `docs/DECISIONS.md` hold. Facts that shape the design:

- Services: `postgres:18` (volume on `/var/lib/postgresql`, the layout PostgreSQL 18
  needs), `migrate` (runs on every start), `api`, `worker`, `caddy`. Volumes:
  `pgdata`, `documents`, `caddy-data` (holds the private authority's key),
  `caddy-ca`, `caddy-config`.
- Roles: `postgres` (superuser), `brievie_owner` and `brievie_app`, both without
  `BYPASSRLS`. Every workspace table has forced row-level security.
- Page files are written once (`{workspace}/{document}/{index}.page`), sealed with a
  per-workspace key that is itself sealed with the master key in the database. There
  is no sweep of orphaned files ("comes with back-ups", `DECISIONS.md`).
- The database holds the letters' text in plain form (masked for BSNs).
- No tags, no published images; `backend/pyproject.toml` says 0.1.0; the health
  endpoint returns `status` and `api_version` and checks nothing.
- `install.sh` lives in `Brievie_site` and clones the default branch.

## 4. Back-up and restore

### 4.1 Approaches considered

| Option | Finding (sources in `onderzoek-backup.md`) | Verdict |
|---|---|---|
| **restic** | encrypts everything (AES-256, key from a password), deduplicates, has retention (`forget`), verification (`check`), and back ends for a local path, SFTP, S3-compatible storage and its own REST server with an append-only mode; BSD-2; an official image | **chosen** |
| age (or tar plus age) | encrypts a stream well; no deduplication, retention, verification or upload: all of that would be ours; every back-up a full copy | rejected |
| borg | comparable to restic; no S3 back end confirmed | rejected |
| Volume tarballs (what the site says now) | needs the stack stopped, unencrypted, tied to the PostgreSQL major version, no restore path | rejected; the site's text goes |
| Writing our own | no reason next to restic | rejected |

Cost if restic is wrong: it sits behind one module and one container; the dump and
the file list are plain inputs, so another tool can take its place without touching
the rest. Not confirmed from the pages read: an arm64 build of the official image
(needed for a Pi; first task of the plan), and restic with S3 object lock.

### 4.2 What a back-up contains

One back-up is two restic snapshots with the same run id as a tag:

1. **The database**, a `pg_dump` in custom format, streamed straight into restic
   (`backup --stdin-from-command`, which makes no snapshot when the dump fails). It
   never lies on disk. It is taken by a new role `brievie_backup` (`LOGIN`,
   `BYPASSRLS`, `CONNECT` on the database, member of `pg_read_all_data`: it reads
   everything and can write nothing), because under forced row-level security a dump
   by the owner fails or, with `--enable-row-security`, silently contains only what
   that role may see. That flag is never used. A test dumps as this role and finds
   rows of two workspaces. `docs/DECISIONS.md` records that a role which bypasses
   row-level security now exists, read-only and used by the back-up service alone;
   the application's roles still cannot bypass it. The PostgreSQL client in the image
   is version 18 from PostgreSQL's own package repository, pinned to the server
   image's major version, with a test that compares the two.
2. **The files**:
   - the document files (`documents` volume), without the temporary `.tmp-*` files;
   - Caddy's private authority. Caddy's volume is readable by root only, so the
     existing script in the Caddy container that publishes the root certificate also
     copies the authority (`pki/authorities/local`) to a volume that only the
     back-up service mounts, owned by its user, mode 0600. Without it a restored
     server has a new certificate and every phone must pair again;
   - `deploy/.env` without the master key line;
   - a **manifest**: Brievie version, schema state, counts of documents and page
     files per workspace (counted as the back-up role, so private post is counted),
     the authority's fingerprint, the host name, time.

Roles and passwords are not in the dump: a restore makes them from the restored
`.env`. **Not in a back-up: the master key.** It is the one secret the user keeps
apart (4.4).

### 4.3 Consistency while the server runs

The server keeps running; nothing is stopped. Order: the dump first (PostgreSQL gives
a consistent snapshot without blocking anybody), then the files. Files are written
once and never changed, so every file the dump refers to exists by the time the files
are read; a file that arrived after the dump is an orphan in the back-up, which is
harmless. The reverse order could restore a database that points at missing files.
(Immich documents the same order for the same reason.) The reader confirmed the argument against the code: intake writes a file before its
row is committed and deletes one only when the row was never committed.

With the back-up comes the **storage sweep** that `DECISIONS.md` promised: a command
that lists page files no document refers to and that are older than a day, and
removes them only when asked, never by itself. It reads the list of known pages as
the back-up role: with the private post of phase 1d the application's role does not
see every page, and a sweep through that role would take a private letter's files
for orphans and destroy them. A test has a private letter of another member.

### 4.4 The key, and the recovery sheet

- **One secret.** `BRIEVIE_BACKUP_PASSWORD` in `.env` is the restic password. By
  default the installer derives it from the master key (HKDF from the `cryptography`
  package that is already used, its own label), so a home user keeps exactly one
  thing: the master key. A restore derives it again from the key on the sheet. The
  back-up service gets this password and not the master key.
- **Two secrets, for who wants them**: an organisation sets its own random back-up
  password. It replaces the derived one (the derived key is removed from the
  repository), so the master key alone no longer opens a back-up; the sheet then has
  a second line and the restore asks for both.
- **The recovery sheet** ("herstelblad"), after the examples of 1Password's Emergency
  Kit and Proton's recovery file: the server's name, the date, the master key as text
  in groups of four, the fingerprint of the server's authority, where the back-up
  goes, and the four steps of a restore. `install.sh` writes it as one self-contained
  HTML file next to the installation and says: print it or save it somewhere that is
  not this machine. `./brievie recovery-sheet` makes it again. There is no QR code on it: the back end has no QR
  generator and none is added for this.
- **The web interface never shows the master key.** A stolen admin session must not
  yield it. The web only asks the server admin to tick "Ik heb het herstelblad
  bewaard" and warns until that is done.
- **For an organisation**: the key in the company's secret store, known to two
  people; `BRIEVIE_MASTER_KEY_FILE` (a Docker secret) instead of a line in `.env`; a
  separate back-up password.

### 4.5 Commands, schedule, destinations

**Where the logic lives.** Everything is a management command in the back-end image;
`./brievie` in the repository root is a thin POSIX-shell wrapper around
`docker compose` so that nobody types a compose line. The same code is therefore
reachable from the web (a job), from the wrapper, and from a NAS's task scheduler.

| Command | Does | Needs a shell |
|---|---|---|
| `./brievie backup` | asks the running service for a back-up and waits: dump, files, `forget --prune` with the retention, record the result | no: also nightly by itself and with a button in the web |
| `./brievie backup --check` | `restic check` with a sample of the data read back | no: weekly by itself |
| `./brievie restore [run]` | section 4.6 | yes |
| `./brievie restore-test` | section 4.7 | yes |
| `./brievie recovery-sheet`, `./brievie status`, `./brievie sweep` | the sheet; versions, last back-up, last restore test; the storage sweep | yes |

So on a Synology without SSH the back-up runs and is visible; restoring, the restore
test and updating need a terminal (or the NAS's task scheduler running the wrapper).
That is less than `docs/SPEC.md` 16 asks ("terugzetten werkt vanuit de
webinterface") and is listed as a deviation. Cost if wrong: a web restore can be
added later on the same commands, with the superuser question solved then.

- **Two kinds of container, split by what they may do.**
  - A service `keeper` that is always on: a Procrastinate worker on the queues
    `backup` and `files`. It has the back-up role's password, the back-up password,
    the documents and the copied authority read-only, and the destination. It has no
    master key (until the archive folder is switched on, 6.3) and no superuser
    password. It makes back-ups, checks them, and reports.
  - **One-off containers** for restore and restore-test, started by the wrapper,
    never listening to a queue. Only they get the superuser's password and the master
    key, for as long as they run.
- **One at a time**: a PostgreSQL advisory lock around back-up, restore and sweep;
  the wrapper asks the running service instead of starting a second copy; the
  existing retry of stalled jobs is extended to the `backup` queue. A back-up run
  that a restore brings back as "started" is marked "afgebroken door herstel".
- **Existing installations.** The first run of `./brievie` brings an installation
  that predates this up to date, without a reinstall: it adds the missing lines to
  `.env` (as `init-env.sh` already does for the master key) and creates the back-up
  role through the superuser on PostgreSQL's local socket
  (`docker compose exec postgres psql`), idempotently, because roles are otherwise
  made only when the database volume is first created and the owner role may not
  create roles. The role is also added to the init script, so a new installation and
  CI have it.
- **Schedule and retention** (defaults; settings in `.env`): every night; keep what
  is younger than 7 days, weekly for a month, monthly for a year, expressed with
  restic's `--keep-within-*` options, which a forged snapshot cannot trick; a check
  every week. On an append-only destination the service does not prune.
- **Destinations**:
  - a folder (an external disk or a mounted NAS share): `BRIEVIE_BACKUP_DIR` is a
    path on the host that compose mounts into the service. The first back-up is set
    up explicitly (`./brievie backup --init`), which leaves a marker file; a folder
    without the marker is refused. Otherwise a disk that is not plugged in would give
    an empty folder on the system disk and a "back-up" next to the data. Changing the
    folder restarts the service;
  - SFTP, S3-compatible storage, a restic REST server:
    `BRIEVIE_BACKUP_REPOSITORY` in restic's notation, **without credentials in it**
    (a value with a password in the address is refused); credentials go in their own
    variables. What the web shows and what a run records as its reason passes a
    filter with a test, because restic's messages name the repository.
  Nothing is set by default: a fresh server warns "Er is nog geen back-up ingesteld".
- **The advice in the text** is the 3-2-1 rule in plain words: one copy outside the
  machine, one outside the house.

### 4.6 Restore

`./brievie restore`, on a machine with Docker and the deploy files (the site's
installer gets `--restore`). Compose cannot be used before there is a `.env`, and the
`.env` is inside the back-up, so the order is fixed:

1. Ask for the destination and the master key (and the back-up password when it is a
   separate one). Open the repository with a plain `docker run` of the back-up image,
   outside compose. A wrong key ends here with a clear sentence; nothing is written.
2. Read the manifest. Refuse a back-up from a newer Brievie than the one installed.
   Ask whether the host name stays: with another name the certificate is valid for
   the new one, but the phones know only the old one and pair again.
3. Refuse when the installation already holds data, unless `--replace` is given;
   then stop the API and the workers and first make a back-up of what is there.
4. Write `deploy/.env` from the back-up plus the master key. This must happen before
   PostgreSQL starts for the first time, because the roles get their passwords from
   it then. With `--replace` the database is dropped and created again and the
   roles' passwords are set to the restored ones.
5. Start PostgreSQL only. Restore the dump in a one-off container **as the
   superuser**, without the services it normally waits for (`--no-deps`): otherwise
   `migrate` would first create an empty schema and the restore would collide with
   it. Only a superuser can load rows into tables with forced row-level security.
6. Put the document files back (owned by the application's user) and Caddy's
   authority (as root), **before Caddy starts**; a Caddy that started earlier has
   made a new authority.
7. Start the stack; `migrate` brings the schema to the installed version.
8. Verify: counts against the manifest, one page per workspace opened with the master
   key, the authority's fingerprint equal to the manifest's. Open back-up runs and
   back-up jobs from the dump are closed.

A back-up is also the way across a PostgreSQL major version (section 7.6), which is
why it is a logical dump and not a copy of the data directory.

### 4.7 A restore that is tested

- **CI** (a new job next to `stack`): start the stack, load two workspaces with a
  letter each, pair a device, make a back-up, destroy every volume, restore, and
  check: the sealed page opens, both workspaces are complete, the device token still
  signs in and the authority's fingerprint is unchanged.
  A second case restores a back-up whose authority is older than a week (Caddy's
  intermediate certificate has then expired) and still signs the phone in.
- **The admin**: `./brievie restore-test` restores the newest back-up into a
  temporary database and directory beside the real ones, runs the checks of step 8
  against them (the checks take a database and a storage path as arguments), removes
  both, and records "Hersteltest geslaagd op …". It runs in a one-off container with
  the superuser, so it is not a job of the always-on service; the web warns when the
  last test is older than 90 days, and a NAS's task scheduler can run it. It needs
  free space for a second copy and says so when there is not enough.

### 4.8 In the web interface (server admins)

A card "Back-up" under settings: destination (without credentials), last successful
back-up and its size, last check, last restore test, and the warnings: never made,
older than three days, last run failed, recovery sheet not confirmed, restore never
tested or long ago. The local fallback of an update (7.3) does not count as a
back-up here. The card is for server admins, a role phase 1d part A brings; it is
delivered after that part. A server-level table `BackupRun` holds the runs (kind, started, finished,
result, a short reason; no paths with names of people). API: `listBackupRuns`,
`getBackupStatus`. Starting a back-up from the web is a job on the `backup` queue.

### 4.9 The optional layer for organisations

- **Back-ups that cannot be overwritten**: a restic REST server in append-only mode
  on another machine, or object storage with object lock; the server that makes the
  back-ups then cannot remove them, and pruning is done from elsewhere. Described in
  `SELFHOST.md`; the server only has to cope with a repository it may not prune.
- **Retention** as settings; **a restore drill** as the scheduled restore test with
  its result in the audit log; **the key with a second person** (4.4).

## 5. Export in an open form

One building block, `documents.export`, used three times: the member's own export
(phase 1d, section 6.3 there), the workspace export here, and the archive folder of
section 6.

- **A letter becomes** one PDF of its pages plus its data. The PDF is made with
  ReportLab (BSD; one library for pages and text), with the recognised text as an
  invisible layer per page when the pipeline kept the text per page; searching and
  copying then work, selecting a word does not land on the word, because there are no
  word positions. When there is no text per page, the text goes in a `.txt` beside
  it. Rejected: OCRmyPDF (needs Tesseract and does the recognition a second time),
  PyMuPDF (AGPL). Not confirmed: that ReportLab embeds a JPEG page without
  re-encoding it; the plan measures it, the fallback is img2pdf for the pages.
- The pipeline keeps the text per page (`DocumentPage.ocr_text`), so the text layer
  is possible.
- **The workspace export** (spec 8.9): a ZIP, sealed on the server like every
  document file and downloadable by the one who asked for 24 hours, with
  `<jaar>/<afzender>/<datum> <titel>.pdf`, one `brievie-export.json` with everything
  the database knows (fields, persons, labels, tasks, history) in a documented shape,
  and a `LEESMIJ.txt`. It runs as the user who asked, so it holds what that user may
  read: an admin's export does not contain somebody else's private post. The complete
  copy of a server is the back-up.
- **Honest in the text**: the BSN is masked in the text but stands on the page image;
  an export is not encrypted once it is downloaded.
- Importing an export is not built; the back-up is how a server moves.

## 6. Storage on a NAS, per person

### 6.1 What was asked and what it can mean

"The data per user in their own folder, so everything is shielded at once." Three
shapes were weighed against how Paperless-ngx, Immich and Nextcloud do it and what
they warn about (`onderzoek-nas-mappen.md`):

| Shape | Finding | Verdict |
|---|---|---|
| 1. The location of the sealed files is configurable | what Immich and Paperless do with their media folder; lowest risk; the NAS's snapshots and back-up then include the files | **yes**, small |
| 2. A readable archive folder, one way | like Paperless's exporter; little trouble as long as the application is the only writer | **yes**, optional |
| 3. The folder as primary storage, two ways | all three projects warn against touching their folders: a rename becomes "missing" or "delete plus new", metadata is lost, scans are needed | **no** |

### 6.2 Shape 1: where the sealed files live

`BRIEVIE_DOCUMENTS_DIR` in `deploy/.env` binds the documents volume to a folder, for
example a shared folder on the NAS. The files stay sealed and unreadable in the file
manager. The text explains that the NAS's back-up then covers the files but not the
database, so `./brievie backup` is still needed. Moving an existing installation is a
documented copy with the stack stopped.

### 6.3 Shape 2: the readable archive folder

Off by default; one setting per workspace: "Leesbare kopie in een map".

- **Where**: `BRIEVIE_ARCHIVE_DIR` binds one folder into the server. Below it:
  `<werkruimte>/<persoon>/<jaar>/<datum> <afzender> - <titel>.pdf`, and
  `<werkruimte>/Gezamenlijk/…` for a letter on nobody's name.
- **The folder belongs to the person**, not to the account: a letter is on the name
  of a person, and a person need not have an account (pap). Per person an admin can
  set another path below the archive folder; on a Synology with the `homes` share
  bound as the archive folder that is `anne/Brievie`, which is the user's own home
  folder, shielded by the NAS.
- **One way.** After confirming, a job writes the PDF (section 5) with a temporary
  name in the same folder and renames it. Brievie stays the source: what a user
  changes, renames or deletes in the folder does not come back. A table `ArchiveCopy`
  records what was written where, with a hash.
- **When the letter changes** (another person, sender, title or date): the copy is
  written at its new place, and the old one is removed only when it still is the file
  Brievie wrote (same hash); otherwise it is left alone. A letter on two names gets a
  copy in both folders. An archived person keeps their folder; nothing new is written
  there.
- **Private letters** go only to the folders of their addressees (persons and
  departments), never to `Gezamenlijk`; a private letter without an addressee is not
  written. **Visibility is a trigger too**: when a letter becomes private (by hand,
  by the Zorg label, by a rule), its copies outside the addressees' folders are
  removed if they still are what Brievie wrote; a copy the user changed is left and
  the admin gets a notice that it is still there. Removing a member does not remove
  copies: a home folder is the NAS's territory, and the text says so.
- **Where the job runs**: on the `files` queue of the `keeper` service (4.5), which
  has the documents; with the archive folder on it also gets the master key and the
  archive folder to write in. It runs as the system (it must read private letters);
  phase 1d's short list of code that may do so gains this job, the sweep and the
  manifest counts.
- **File names** are made safe for SMB and Windows: the forbidden characters and
  reserved names replaced, no trailing dot or space, composed Unicode (NFC), a length
  limit, a number added on a clash, never relying on upper and lower case.
- **Ownership**: the files are written as the application's user (uid 10001), which
  must be able to write in the folder. There is no setting to write as another user:
  a container under another uid could not read the sealed documents. Whether a
  Synology or QNAP home folder then lets its NAS user read them, through inherited
  ACLs, could not be confirmed from documentation; it must be tried on a real NAS
  before this is promised on the site. **Not verified.** The same uid must own a
  folder given as `BRIEVIE_DOCUMENTS_DIR` (6.2); the text gives the command.

### 6.4 What the text must say plainly

- A readable PDF in a folder is **no longer encrypted by Brievie**; its protection is
  the NAS's.
- The **text of every letter is always in the central database**. The shielding
  between members comes from roles and private post (phase 1d, enforced in the
  database), not from the folders.
- The **BSN stands on the PDF**.
- The folder is a copy. Deleting a file there does not delete the letter.

## 7. Updates and releases

### 7.1 What the comparable projects do, and what is taken over

| Project | Their way (`onderzoek-updates.md`) | Taken over | Not taken over |
|---|---|---|---|
| Immich | version pinned in `.env`, `pull` and `up`; no downgrade; the app works with the current and previous major of the server | the pinned version; "no way back but a restore"; an explicit app and server rule | breaking changes that need reading release notes before every update |
| Nextcloud | a back-up asked before every upgrade; no skipping a major version; a banner for admins; channels | refusing a jump that was not tested; the banner | an updater inside the web process; maintenance mode by hand |
| Paperless-ngx | back-up, `pull`, `up`; migrations at start; a PostgreSQL major by dump and load | migrations at start; the dump as the way across a PostgreSQL major | `latest` as the default tag |
| Vaultwarden | `pull` and `up`; multi-arch images; tags `latest`, `testing`, fixed | multi-arch images; fixed tags | floating `latest` |

### 7.2 Versions and releases

- **Semantic versions** for the server, one source (`backend/pyproject.toml`), a tag
  `vX.Y.Z` per release, and `CHANGELOG.md` in the Keep a Changelog form, with a
  section "Voor je bijwerkt" when a release asks something of the admin.
- The health endpoint gains `server_version` (additive) and checks the database.
- **Images** for amd64 and arm64 in the GitHub Container Registry, built by a release
  workflow on a tag, with a build attestation and an image scan. The compose file
  loses `pull_policy: never` on `migrate` and `worker`, and CI's `stack` job uses the
  development override. `docker-compose.yml` refers to
  `…/brievie-backend:${BRIEVIE_VERSION}`; building locally moves to a development
  override. A NAS or Pi no longer builds anything. A new package is private until its
  owner makes it public: **the owner's step**, like making the first tag.
- `BRIEVIE_VERSION` in `.env` is always a fixed version. There is no `latest`.

### 7.3 One command

`./brievie update [version]`, in a fixed order, stopping at the first failure with a
sentence that says what happened and what to type:

1. **Check**: Docker, free space, the installed and the target version, and that the
   jump is allowed. Every release carries `deploy/release.json` (version, the oldest
   version it can be reached from, the PostgreSQL major); the updater reads the
   target's file before anything else (from the tag: `git fetch` and `git show` while
   the server is still built locally, the release's file once images are published).
   A refused jump names the version to go to first.
2. **Fetch** the images (or the tag). Nothing running has changed yet.
3. **Stop** the API, the worker and the keeper. The worker gets a stop period of a
   minute; a job that was cut off is picked up again by the existing retry.
4. **Back up** (section 4), with the server stopped, so that nothing arrives between
   the back-up and the update and a way back loses nothing. Without a configured
   destination: into a local repository in the installation folder, so there is
   always a way back (it lies next to the key and is not counted as a back-up).
5. **Migrate**, **start**, **check health**.
6. **Record** the version and the time.

The server is away for as long as the back-up takes; restic sends only what is new,
so after the first one that is short.

**The way back** is restoring the back-up of step 2 with the previous version:
`./brievie restore pre-update-<version>` (the back-up of step 4), which the failure message prints.
Migrations are not reversed. An update is not started automatically, by anything.

### 7.4 Knowing that an update exists

Off by default. When a server admin switches it on, the server asks the public
releases list of the repository once a day and shows server admins a line "Er is een
nieuwe versie" with the change log and the command. The text says what this
discloses: the server's address to GitHub, nothing else. Without it, the site and the
change log are where one looks. Not confirmed from the page read: that the list can
be asked without a token (the limit is 60 requests an hour per address).

### 7.5 App and server versions

The App Store updates the app by itself; the server waits for its admin. So:

- **The server never breaks an older app**: `/api/v1` stays additive. The contract
  test grows from one fixture ("1b") to one per released minor version (a patch may
  not change the contract; the test holds a patch to its minor's fixture). The
  fixture is added in the release commit, before the tag; CI refuses a version in
  `pyproject.toml` without one. Recorded renames are kept per fixture. The 1b
  fixture stays as long as a 1b app can exist. This replaces the line in
  `DECISIONS.md` that says the fixture is replaced at a release.
- **A newer app meets an older server**: it hides what the server does not offer, as
  it already does. The app states the oldest server version it supports (the rule:
  every server release of the last twelve months) and shows "Werk je server bij"
  below that.
- **The server can refuse an app that is too old** with a code of its own and a
  Dutch sentence, used only when a release must (a security fix). The app sends its
  version (phase 1d).
- CI gets an **upgrade test**: install the previous release, load data with a sealed
  document, update to the build under test, open the document. It can exist only
  after the first release.

### 7.6 PostgreSQL, encryption, the worker

- **PostgreSQL major version**: the image stays pinned. A release that moves to a new
  major says so, and `./brievie update` then takes the path of a restore: dump, a new
  volume, load, with the old volume kept until the admin removes it. No in-place
  upgrade.
- **A change in the encryption format**: the reader accepts the old and the new
  marker, the writer writes the new one, and nothing is rewritten in a migration at
  start; re-sealing is a background job that can stop and continue. The upgrade test
  opens a document that was sealed by the previous release.
- **The worker** is stopped before the migration and started after it, so no job runs
  against a half-migrated database.

### 7.7 What changes on the site and in `install.sh`

- `install.sh` installs the newest release (the deploy files of that tag and the
  pinned images) instead of cloning the default branch, writes the recovery sheet,
  and has `--restore`.
- The installation page: updating becomes `./brievie update`; the back-up section
  becomes `./brievie backup`, the destinations, the restore and the sheet; the volume
  tarball command goes.
- The limits page: "back-ups en updates regel je zelf" becomes what the product now
  does and what stays the user's (a destination, the sheet).
- These are texts on the site: **the owner's to approve**.

### 7.8 The optional layer for organisations

Pinning a version and updating a test instance first (a restore of last night's
back-up into a second installation is exactly that); `SECURITY.md` and GitHub's
security advisories for reporting and announcing; installing without internet from an
archive of the images (`docker save`), with `./brievie update --from-archive`.

## 8. Parts, smallest first

Each part is delivered on its own, with its own plan.

| Part | Content | Needs from the owner |
|---|---|---|
| **B1. Back-up en herstel** | the back-up role, the `keeper` service, `./brievie` with `backup`, `restore`, `status`, `sweep`, bringing an existing installation along, the manifest, the recovery sheet, the CI restore job, `SELFHOST.md` | nothing |
| **B2. Schema en hersteltest** | the nightly schedule, retention, the weekly check, `restore-test` | nothing |
| **U1. Versies en bijwerken** | version source, change log, `release.json`, `server_version` and a real health check, `./brievie update` (still building locally) | nothing |
| *(phase 1d part A, then:)* **B3. Zichtbaar** | the web card, warnings, "Nu back-up maken" | nothing |
| *(phase 1d part B, then:)* **E. Export en archiefmap** | `documents.export`, the workspace export, `BRIEVIE_DOCUMENTS_DIR`, the archive folder | a NAS to try the ownership on |
| **U2. Releases en images** | the release workflow, multi-arch images, compose on images, the installer on a release | the first tag, the package made public |
| **U3. Compatibiliteit en melding** | contract fixtures per release, the app's oldest server, the refusal code, the update notice, the upgrade test | a first release to upgrade from |
| **U4. Voor organisaties** | append-only destinations, offline installation, `SECURITY.md` | nothing |

B1 comes first of everything, also before phase 1d: it is the smallest part and it
closes the largest risk that exists today.

## 9. Edge cases the plans must test

- A dump as the back-up role contains every workspace; a dump as the owner fails
  loudly rather than being used.
- A back-up while a scan uploads; a file newer than the dump; a document deleted
  between dump and files.
- No free space for the dump; the destination unreachable; a wrong master key at
  restore (a clear sentence, nothing written).
- A restore over an installation that holds data; a back-up from a newer version.
- A restore on another machine with another host name: the phones need the old name
  or pair again; the text says so.
- An update interrupted after the migration; an update across two releases; a
  refused jump.
- The update notice with no internet; with the limit reached.
- Archive folder: a title with a slash, a colon, a name that differs only in case, a
  very long sender; the folder not writable; a file the user changed and the letter
  then moves; a private letter; a person renamed.
- No master key, back-up password or page content in any log line.

## 10. Deviations from `docs/SPEC.md`

- Spec 11: "Web-update-knop met automatische database-back-up" and "Back-up-wizard"
  become a command line with a web card that reports and warns (section 2).
- Spec 16: "Back-up maken en terugzetten werkt vanuit de webinterface": making does,
  restoring needs a shell (4.5).
- Spec 11 asks for signed images; this design has a build attestation and an image
  scan, no separate signature.
- Spec 11: channels `stable` and `beta` are not introduced; there are releases, and
  pre-releases when needed.
- Spec 8.9 "Export: alles als ZIP" is the workspace export of section 5; import is
  not built.
- Spec 16 puts export and back-ups in step 1d; they are parts of their own here, and
  back-up comes before the members of 1d.

## 11. Left to the builder

The Dutch sentences, the layout of the card and the sheet, names of private helpers,
the exact shell of `./brievie`. Not left to the builder: what a back-up contains, the
order of 4.3, 4.6 and 7.3, that the master key is never in a back-up or on the web,
and the checks of the restore test.

## 12. Open points for the plans

- The official restic image on arm64; restic with object lock.
- ReportLab and JPEG pages.
- Ownership of archive files on a Synology and a QNAP home folder.
- The releases list without a token.
- How `install.sh --restore` gets the deploy files before anything is installed.

## 13. What the independent reader changed (2026-11-17)

Report: `beheer-lezerscontrole.md` in the session notes (ten questions, fourteen
findings; all taken). The rulings that changed the design, each with its cost:

| Ruling | Why | Cost if wrong |
|---|---|---|
| The logic is in management commands; the wrapper is thin; restoring and updating need a shell | a Synology project cannot run a script, and the web must not hold the superuser's password | less than spec 16 asks; a web restore can come later on the same commands |
| An always-on `keeper` with read-only powers; restore and restore-test in one-off containers | one long-lived worker would otherwise hold the master key, a role that reads everything and the superuser | the restore test is not scheduled by the server itself |
| A read-only role that bypasses row-level security exists for the back-up | no other way to a complete dump | one more secret in `.env`; it cannot write |
| The dump is streamed into restic, never staged on disk | no letter text on disk outside the database; no staging volume to own | two snapshots per back-up instead of one |
| The back-up password is always in `.env`, derived by default | the keeper needs no master key | the derivation must stay stable for ever (a test with a fixed vector) |
| A local folder needs an explicit first set-up and a marker | an unplugged disk would give a back-up on the system disk | one more step the first time |
| Update order: check, fetch, stop, back up, migrate, start | a way back must not lose scans that arrived during the update | the server is away for the length of a back-up |
| `release.json` in each release | the updater must know the allowed jump before it changes anything | one more file to keep right at a release |
| The web card after 1d part A, export and archive folder after 1d part B | they need the server admin and the actor of row-level security | they come later than back-up itself |
| The archive folder reacts to visibility; the sweep and the counts read as the back-up role | copies of a letter that became private would stay in the shared folder; a sweep through the application's role would delete private letters' files | none known |
| One contract fixture per released minor, added in the release commit | a workflow on a tag cannot add to the tag | more fixtures to keep |
