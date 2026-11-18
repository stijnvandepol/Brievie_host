# "Beheer": back-up and restore, export and archive folder, updates and releases: design

Date: 2026-11-17; updated 2026-11-18 to say what part B1 (back-up and restore) built
on the branch `feat/b1-backup`: sections 4, 7.3, 7.7, 8 to 13 are corrected in place
and section 14 lists what the build changed. Covers `Brievie_host` (and what changes in
`Brievie_site` and `Brievie_ios` because of it). Design only. Nothing here creates tags,
releases or packages on GitHub; that is the owner's step.

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
- G2. The restore is tested: by CI on every change (part B1), and by the admin with
  one command (part B2).
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
the rest. Confirmed during the build: restic 0.18.1 is published for amd64 and arm64
(a Pi or a NAS on ARM works); the back-end image copies its binary from the official
image, pinned by version and digest. Not confirmed: restic with S3 object lock.

### 4.2 What a back-up contains

One back-up is a **run**, and a run is two restic snapshots of the installation. Both
carry the run id as a tag (`run:<uuid>`), their kind (`kind:db` or `kind:files`) and
**one shared time**: the moment the run started (restic's `--time`, in UTC). Retention
groups snapshots by host and paths, so two halves with different times could be thinned
apart at the edge of a day, a week or a month, and an older restore point would silently
lose its database or its files (found in the final review). The restic host is
`brievie-<first 12 characters of the installation id>`, so two installations on one
repository never prune each other's snapshots.

1. **The database**, a `pg_dump` in custom format, streamed straight into restic
   (`backup --stdin-from-command`, file name `brievie.pgdump`, which makes no snapshot
   when the dump fails). It never lies on disk. It is taken by the role
   `brievie_backup` (`LOGIN`, `BYPASSRLS`, `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`,
   `CONNECT` on the database, member of `pg_read_all_data`: it reads everything and can
   write nothing), because under forced row-level security a dump by the owner fails or,
   with `--enable-row-security`, silently contains only what that role may see. That
   flag is never used. The dump is taken inside a read-only `REPEATABLE READ`
   transaction that exports its snapshot (`pg_dump --snapshot=<id>`), so the manifest's
   counts (below) describe exactly what the dump holds.
   `docs/DECISIONS.md` records that a role which bypasses row-level security now
   exists, read-only and used by the back-up service and the sweep alone; the
   application's roles still cannot bypass it. The PostgreSQL client in the image is
   version 18 from PostgreSQL's own package repository, with a test that compares its
   major version with the server image's.
   **Proof, honestly:** the unit tests read as this role with `row_security = off` and
   prove that a role bound by row-level security fails instead of reading fewer rows.
   The real `pg_dump` against the real database has run only in a container stack and
   in the CI job `restore` (4.7), not in a unit test. Cost: a regression in the dump's
   command line shows in CI, not in the fast tests.
2. **The files** (one restic snapshot of three directories):
   - the document files (`documents` volume), without the temporary `.tmp-*` files;
   - Caddy's private authority. Caddy's volume is readable by root only, so the script
     in the Caddy container that publishes the root certificate also copies the
     authority (`pki/authorities/local`) to the volume `caddy-authority`, which only the
     keeper and the one-off `admin` container mount (the keeper read-only), files mode
     0600 owned by uid 10001. Without it a restored server has a new certificate and
     every phone must pair again. The copy is made file by file, so a back-up during
     Caddy's renewal of the intermediate can hold a mismatched pair (low odds; a
     directory swap would fix it; not built);
   - the config folder `backup-config`: the **manifest** and a **copy of
     `deploy/.env`**. The installation id that also lives in that folder is excluded.
3. **The manifest**: format number, run id, time, Brievie version, the list of
   applied migrations, PostgreSQL version, host name, the authority's fingerprint, and
   per workspace the counts of documents, pages and page files. The counts are read as
   the back-up role in the same transaction snapshot that `pg_dump` dumps, so a letter
   scanned during the dump cannot make the restore report a false difference. That
   transaction stays open on its own connection while the dump runs: a deliberate
   exception to "no transaction during an external call" in `CLAUDE.md`. Cost: a long
   dump holds a snapshot open and delays vacuum meanwhile.

**What is not inside a back-up.** The master key (`BRIEVIE_MASTER_KEY`). The passwords
of the database superuser and of the owner role and the owner's URL
(`POSTGRES_PASSWORD`, `BRIEVIE_OWNER_PASSWORD`, `DATABASE_OWNER_URL`): the always-on
service must not hold them, so they cannot pass through it. A guard refuses to store
the `.env` copy when any of those keys is present in any spelling (a quoted key,
`export`, a space, a commented-out key that still has a value). The installation id
(4.6). **What is inside, and is not "without secrets":** the `.env` copy still holds
`SECRET_KEY`, the application role's and the back-up role's database passwords, the
back-up password and the credentials of a remote destination. That is acceptable because
the whole repository is encrypted with the back-up password, but it means whoever holds
the back-up password can read them. The sheet and `docs/SELFHOST.md` say so. A back-up
without the `.env` copy fails (`env_copy_missing`, "Typ ./brievie start"): a restore
needs that copy, and a back-up that cannot be restored must never be recorded as
succeeded. Cost: a NAS scheduler that runs `manage.py backup` without `./brievie` fails
until `./brievie start` has stored the copy once.

Roles and passwords are not in the dump. A restore makes new passwords for the
superuser and the owner role, and takes the application role's and the back-up role's
passwords from the restored `.env` copy (4.6).

### 4.3 Consistency while the server runs

The server keeps running; nothing is stopped. Order: the dump first (PostgreSQL gives
a consistent snapshot without blocking anybody), then the files. Files are written
once and never changed, so every file the dump refers to exists by the time the files
are read; a file that arrived after the dump is an orphan in the back-up, which is
harmless. The reverse order could restore a database that points at missing files.
(Immich documents the same order for the same reason.) The reader confirmed the
argument against the code: intake writes a file before its row is committed and
deletes one only when the row was never committed.

With the back-up comes the **storage sweep** that `DECISIONS.md` promised:
`./brievie sweep` lists the page files that no document refers to, and
`./brievie sweep --delete` removes them. Nothing removes them by itself. It runs as
uid 10001 in the one-off container and reads the list of known pages as the back-up
role with `row_security = off`: with the private post of phase 1d the application's
role does not see every page, and a sweep through that role would take a private
letter's files for orphans and destroy them. (A test with a private letter of another
member belongs to the first 1d part that adds private post; B1 has proven that a role
bound by row-level security fails instead of listing a partial set.) The rules, all
in code:

- files on disk are listed first and the known pages read after, so a page written
  in between is known; only files of the layout `<uuid>/<uuid>/<n>.page` and `.tmp-*`
  next to them are candidates; a file younger than 24 hours is never touched (checked
  again just before it is removed); no link is followed; directories are never removed;
  deletion goes through directory descriptors (4.10); the output is counts and a total
  size, never a path;
- it holds the back-up lock, so it never runs beside a back-up or a restore;
- **it refuses, and deletes nothing, when:** the known pages cannot be read as the
  back-up role; a stored path has a form it does not know; the database knows no page
  at all while page files exist (an empty or wrong database must never empty the
  store; there is no override flag in B1, cost: a server whose only content is orphans
  cannot sweep them and must delete them by hand); the storage folder is not
  `/data/documents` (for `--delete`); the installation id does not match (for
  `--delete`); a restore is in progress; it is not run inside Brievie's container.
- after a restore, pages uploaded between the dump and the files snapshot have no row
  and are removed by the first `sweep --delete`.

### 4.4 The key, and the recovery sheet

- **One secret.** `BRIEVIE_BACKUP_PASSWORD` in `.env` is the restic password. By
  default it is derived from the master key (HKDF-Expand with SHA-256, the label
  `brievie/backup-password/v1`, which never changes: existing back-ups depend on it),
  so a home user keeps exactly one thing: the master key. **The derivation happens only
  inside the container**, by `manage.py derive_backup_password`, and `./brievie` appends
  the result to `deploy/.env` (the installer and `init-env.sh` do not derive it: on the
  host the only tools are `openssl`, which takes a key only as an argument that any
  local user can read from the process list, and `python3`, which a NAS may not have).
  A restore derives it again from the key on the sheet. The keeper gets this password
  and not the master key.
- **Two secrets, for who wants them**: `./brievie backup --init --separate-password`.
  If `deploy/.env` already holds a back-up password that the user set (not a
  placeholder, not the derived one) it is kept and used; otherwise a random one is
  made and written there. It is chosen **only at the first set-up** of a destination:
  an existing repository keeps the password it was made with, and changing it is not
  built (cost: moving to a separate password means a new repository). The master key
  alone then no longer opens the back-ups. Right after a successful set-up the wrapper
  writes a **new recovery sheet that holds both secrets** and tells the user to print
  it at once; when the set-up fails, the old value in `.env` is put back. A password
  with a quote or a line break is refused. A restore asks for the second secret as soon
  as the key alone does not open the back-up (and up front with
  `./brievie restore --separate-password`).
- **The recovery sheet** ("herstelblad"), after the examples of 1Password's Emergency
  Kit and Proton's recovery file: the server's name, the date, the master key as text
  in groups of four, the fingerprint of the server's authority, where the back-up
  goes (a folder's path or a repository's address, never credentials), the separate
  back-up password when there is one, and the four steps of a restore. It is one
  self-contained HTML file `brievie-herstelblad.html` next to the installation, mode
  0600. **The first `./brievie start` writes it, once**; later starts do not (a sheet
  that was printed and removed, as told, never comes back with the key on it), and
  `./brievie recovery-sheet` makes a new one. `install.sh` does not write it (it is in
  another repository and unchanged in B1; 7.7). The sheet tells the user to print it or
  save it somewhere that is not this machine, and that the file must then be removed.
  The credentials of a remote destination are on no sheet; `docs/SELFHOST.md` tells the
  user to keep them off the machine as well. There is no QR code on it: the back end
  has no QR generator and none is added for this.
- **The web interface never shows the master key.** A stolen admin session must not
  yield it. The web only asks the server admin to tick "Ik heb het herstelblad
  bewaard" and warns until that is done (part B3).
- **For an organisation**: the key in the company's secret store, known to two
  people; a separate back-up password. `BRIEVIE_MASTER_KEY_FILE` (a Docker secret)
  instead of a line in `.env` is not built in B1.

### 4.5 Commands, schedule, destinations

**Where the logic lives.** The logic is in management commands in the back-end image;
`./brievie` in the repository root is a POSIX-shell wrapper around `docker compose`
so that nobody types a compose line. As built, more sits in the shell than the design
wanted: settling which installation this is, preparing the restored `.env`, asking for
the destination and the typed confirmations all happen before there is an image, a
compose project or a `.env` to run a command in. Cost: shell is harder to test (the
wrapper's tests run it against a fake `docker`), and U1 moves out what it can.
The terminal shows Dutch sentences only; docker's progress and the commands' log lines
go to `deploy/.brievie-last.log` (`BRIEVIE_VERBOSE=1` shows everything), and a failed
step shows its last lines and the next thing to type.

| Command | Does | In B1 |
|---|---|---|
| `./brievie start` | proves the installation is this checkout's, brings an older installation along (4.5 below), builds and starts everything, makes sure the back-up role, the back-up password and the `.env` copy exist and the installation has its id; the first start writes the recovery sheet | shell |
| `./brievie backup` | asks the running keeper for a back-up and waits: the dump, the files, retention, the result recorded | shell |
| `./brievie backup --init [--allow-same-disk] [--separate-password]` | the first set-up of a destination (below) | shell |
| `./brievie backup --check` | `restic check` with a 5% sample of the data read back | shell |
| `./brievie backup --safety` | the safety back-up of a restore (and of an update, section 7): tagged `kind:safety`, no retention in its own run, kept 30 days; prints its run id; not in the help text | internal |
| `./brievie restore [number] [--replace] [--separate-password] [--keep-hostname] [--skip-safety-backup]` | section 4.6 | shell |
| `./brievie status [--list]` | versions, destination (kind only), last good back-up and its size, last check, last error, a run that was interrupted, a clock warning; `--list` shows the runs on the destination with number, date and whether each is complete, read from the destination itself, so it works while the keeper is stopped | shell |
| `./brievie sweep [--delete]` | the storage sweep (4.3) | shell |
| `./brievie recovery-sheet`, `./brievie help` | a new sheet; the help text | shell |

`restore-test` is part B2 and `update` part U1. **In B1 every command needs a shell**:
the nightly schedule is B2 and the button in the web is B3, so until then a back-up is
made when somebody (or a NAS's task scheduler running the wrapper) runs
`./brievie backup`. That is less than `docs/SPEC.md` 16 asks ("terugzetten werkt vanuit
de webinterface") and is listed as a deviation. Cost if wrong: a web restore can be
added later on the same commands, with the superuser question solved then.

- **Two kinds of container, split by what they may do.**
  - A service `keeper` that is always on: a Procrastinate worker on the queue `backup`
    **only** (the queue `files` of the archive folder, 6.3, is not served by anything
    in B1). It has the application role (the queue and the table `BackupRun`), the
    back-up role's password, the back-up password, the destination's credentials, the
    documents and the copied authority read-only, the writable `backup-config` volume
    and the destination. It has no master key and no superuser or owner password. It
    makes back-ups and checks and reports. A running step gets a minute to finish when
    the keeper is stopped; every five minutes a periodic task gives jobs of a keeper
    that died back to the queue (one waiting at most, so nothing piles up when no keeper
    runs).
  - **One-off containers**: the compose service `admin` behind the profile `admin`,
    always `run --rm --no-deps` with an explicit command (its default command only
    prints help, never the application), started by the wrapper and never listening to
    a queue. It runs as root, because a restore writes every volume, except where a
    command needs no root and runs as uid 10001 (the sweep, reading and writing the
    installation id). Only it gets the superuser's password and the master key, for as
    long as it runs, and it mounts the back-up destination read-only for every restore
    step that only reads (`BRIEVIE_BACKUP_MOUNT_MODE`).
- **One at a time.** (a) A PostgreSQL advisory lock around back-up, restore and
  sweep. An advisory lock belongs to one database, so a restore, which replaces the
  database the service is connected to, takes the lock of the database it restores into
  (`database_lock`). (b) A request for a back-up returns the run that is already open;
  an open run whose job has gone is closed as "afgebroken" and its leftover snapshots
  are removed, unless it has both halves (a run that died after its files snapshot is a
  whole back-up). (c) The wrapper itself: a directory `deploy/.brievie-lock` allows one
  `./brievie` per installation at a time; a lock older than 12 hours is removed only
  after a typed JA. (d) A back-up run that a restore brings back as open is closed with
  "afgebroken door herstel".
- **Existing installations.** Every `./brievie` command brings an installation that
  predates this up to date, without a reinstall: `init-env.sh` adds the missing lines
  to `.env` (it recognises its keys in every spelling), and the back-up role is made or
  corrected through the superuser on PostgreSQL's local socket
  (`docker compose exec postgres psql`, the password on stdin, never as an argument),
  idempotently, because roles are otherwise made only when the database volume is
  first created. `make dev` does the same through `deploy/ensure-backup-role.sh`. The
  role is also in the init script, so a new installation and CI have it. A secret is
  never an argument of a started process: not the master key, not a password.
- **Retention** (a fixed setting in B1; as `.env` settings in B2). After each
  successful run the keeper applies, per installation and in this order:
  1. list the installation's own snapshots; **stop, remove nothing, and record the
     warning `clock_skew` when one is dated more than 24 hours in the future**
     (retention is relative to the newest snapshot, so one wrong clock would otherwise
     remove almost everything; the run itself still counts as succeeded; a clock that
     runs *behind* is not detected);
  2. remove runs that have only one of their two snapshots and are older than 48
     hours, by their run tag;
  3. remove safety back-ups older than 30 days, counted from now, by their run tag
     (restic's `--keep-within` counts from the newest snapshot of a group, so a lone
     safety snapshot would never expire that way);
  4. `forget --prune --host <host> --group-by host,paths --keep-tag kind:safety
     --keep-within 2d --keep-within-daily 7d --keep-within-weekly 1m
     --keep-within-monthly 1y`: **every snapshot of the last two days stays**
     (counted from the newest), then one a day for a week, one a week for a month, one
     a month for a year.
  Why the two-day window: the daily rule alone keeps one snapshot a day, so a second
  back-up on the same day removed the first. The CI job found the safety back-up of a
  `--replace` pruning the very run the user had chosen, after the database was
  already dropped. The safety back-up's own run applies no retention at all. On an
  append-only destination (`BRIEVIE_BACKUP_APPEND_ONLY=true`) the service never prunes
  or removes anything. Cost: busy days keep more snapshots. A check runs when asked
  (`backup --check`); the weekly one is B2.
- **Destinations**:
  - a folder (an external disk or a mounted NAS share): `BRIEVIE_BACKUP_DIR` is a
    path on the host that compose mounts at `/backup` in the keeper (a named volume
    while it is unset). The repository lies in the subfolder `restic`. The first
    back-up is set up explicitly (`./brievie backup --init`), which hands the tree to
    uid 10001 and writes the marker `brievie-backup.json` **last**; a folder without
    the marker is refused (`destination_not_ready`), and no other command ever creates
    anything there. Otherwise a disk that is not plugged in would give an empty
    folder on the system disk and a "back-up" next to the data. `--init` also refuses a
    folder on the same disk as the documents (`same_disk`) unless `--allow-same-disk`
    is given; that comparison of device ids is best effort and can miss Docker
    Desktop, btrfs subvolumes and a separate `/var/lib/docker`. A corrupt or foreign
    marker is repaired by running `--init` again, but only when the configured password
    opens the repository. Changing the folder restarts the keeper (the wrapper does it);
  - S3-compatible storage or a restic REST server: `BRIEVIE_BACKUP_REPOSITORY` in
    restic's notation (`rest:https://…`, `s3:https://…`), **without credentials in it**
    (a value with a password in the address is refused); credentials go in their own
    variables (`BRIEVIE_BACKUP_REST_USERNAME`, `BRIEVIE_BACKUP_REST_PASSWORD`,
    `BRIEVIE_BACKUP_S3_ACCESS_KEY_ID`, `BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY`). What the
    wrapper prints and what a run records as its reason passes a filter with a test,
    because restic's messages name the repository. Setting a folder and a repository
    together is refused. **SFTP is not built** (it needs an ssh client and key handling
    in the image; a NAS share as a folder or a REST server covers the same case).
  Nothing is set by default: a fresh server says "Er is nog geen back-up ingesteld".
  A restored server writes to the old destination only when its admin says so (4.6).
- **The advice in the text** is the 3-2-1 rule in plain words: one copy outside the
  machine, one outside the house.

### 4.6 Restore

`./brievie restore`, on a machine with Docker and the deploy files. (`install.sh
--restore` is not built; 7.7 and 12.) Compose cannot be used before there is a `.env`,
and the `.env` is inside the back-up, so the order is fixed. The wrapper's own rules
come first, and they bind every destructive command, `update` included (4.10): the
compose project and the image are worked out **once** and passed with `-p` to every
call; the installation is proven to be this checkout's (no container of another
checkout under the same project name; no data volume without this checkout's
`deploy/.env`; the installation id in the data volume equals the one in `.env`);
nothing else runs meanwhile (the lock of 4.5).

**Fresh or replace.**
- **Fresh**: there is no `deploy/.env` (a new machine). Refused when a data volume of
  the project exists without a `.env`, or when another checkout's containers carry the
  project name.
- **An empty installation counts as fresh.** An installation that has a `.env` but whose
  own PostgreSQL runs and holds no user, no workspace and no document (or no tables)
  has never held data. It is replaced after one yes (`j` in a terminal, or
  `BRIEVIE_RESTORE_EMPTY_OK=ja`), without a safety back-up and without the typed word.
  Cost: an installation that was installed but never started is not recognised as
  empty (PostgreSQL must run to be asked); it needs `--replace`, which then wants a
  safety back-up that cannot be made without a destination.
- **Anything else needs `--replace`**: without it the restore refuses an installation
  that holds data and says so.

**The order:**

1. **Open the back-up, outside compose.** Ask for the source (a folder path, a Docker
   volume of this project as `volume:<name>`, or a `rest:` or `s3:` address with its
   credentials) and the master key (typed without echo; spaces, dashes and capitals
   are accepted). A plain `docker run` of the back-up image reads the repository
   read-only. The newest run that has both snapshots is taken (it can be one made with
   unreadable files, "gemaakt, maar niet compleet"), or the run number given; the
   number comes from `./brievie status --list`. A wrong key or password ends here with a
   clear sentence and nothing is written; the key is checked against the dump's first
   workspace key (which streams the whole dump once; it is streamed again at step 6).
   Refuse a back-up from a newer Brievie than the one installed: one whose manifest
   lists migrations this code does not know. The manifest's run must be the run chosen.
2. **Prepare the new `.env` in full** in `deploy/.restore/env` (mode 0600; it holds the
   master key; the folder goes when the restore ends or is given up before step 5)
   before anything here changes: the copy from
   the back-up, plus the master key, a new password for the superuser and for the
   owner role (they are used only when the database is first created and by `migrate`),
   and the back-up password that opened the repository. **Settings that are not taken
   from a back-up**, because they belong to the machine and not to the data: the folder
   and repository of the destination and their credentials, `BRIEVIE_IMAGE`,
   `COMPOSE_PROJECT_NAME`, the host ports (`POSTGRES_PORT`, `BRIEVIE_HTTP_PORT`,
   `BRIEVIE_HTTPS_PORT`), every `COMPOSE_*` and `DOCKER_*` key and the installation id.
   An installation that already has a `.env` keeps its own values; a new machine gets
   the project and ports it runs with. Ask whether the host name stays (`j/n`, or
   `--keep-hostname`): with another name the certificate is valid for the new one, but
   the phones know only the old one and pair again. On a new or empty installation
   ask whether this server should keep making back-ups to the same place; the answer
   defaults to no, because two servers on one destination must never both prune it.
3. **What is here now is proven ours, confirmed and backed up** (only when there is
   data): check the installation id (a volume that has none, from before the id
   existed, needs a typed JA to adopt it); for `--replace` the typed word **WISSEN**
   (a terminal, or `BRIEVIE_RESTORE_CONFIRM=WISSEN`; piped input never counts); stop
   the API, the worker and Caddy; make the **safety back-up** with `backup --safety`
   (its run id is printed, and again at the end); stop the keeper.
4. **`restore_check` before anything is destroyed.** One read-only call proves the
   chosen run still has both of its snapshots. If not (retention may have removed it
   meanwhile), nothing is replaced, Brievie starts again, and the message points at
   `./brievie status --list`.
5. **From here things are replaced, and a marker says how far it got**:
   `deploy/.restore-in-progress` (run, safety back-up, step, time). The old
   `deploy/.env` moves to `deploy/.env.before-restore-<time>` (it holds the master
   key: the user is told to remove it when the restore is fine) and the prepared one
   takes its place; if the second move fails the first is undone. Everything is stopped.
6. **The database.** Start PostgreSQL only, and no other service that would make an
   empty schema first (`--no-deps`). With `--replace` the database is dropped
   (`DROP DATABASE … WITH (FORCE)`) and made again, and the passwords of the roles
   (the back-up role included) are set from the new `.env`. Then, in a one-off container **as the superuser** (only a
   superuser can load rows into tables with forced row-level security), `restic dump |
   pg_restore` under the lock of the target database: **the dump is never staged on
   disk.** In the same transaction in which the runs that the dump brought back as open
   are closed ("afgebroken door herstel"), **the restored queue is settled**: every
   dump holds the job of the back-up that made it, in the status `doing` and holding the
   task lock `backup`, which would stop every new back-up until the stalled-job retry;
   so back-up jobs that wait or run become `failed`, running jobs of other queues go
   back to `todo`, and the old server's workers are unregistered. All of this happens
   before any service starts.
7. **The files.** With `--replace` the three directories (documents, the authority
   copy, the config folder, which keeps its installation id) are emptied first. restic
   restores exactly four things: the documents, the authority copy, the manifest and
   the `.env` copy (never extended attributes or ACLs, never an installation id an
   older back-up holds). **The post-restore clean-up** then walks the trees: links,
   devices, sockets and fifos are removed (and counted), setuid, setgid and sticky
   bits are cleared, and everything goes to uid 10001; it also runs when restic failed.
   Caddy's authority is put in place, root-owned, **before Caddy first starts** (a
   Caddy that started earlier makes an authority of its own and every phone pairs
   again); only the chain `caddy/pki/authorities/local` below Caddy's volume is made,
   and Caddy's leaf certificates are removed so it issues new ones from the restored
   authority.
   **The installation id rules:** the id lives in the data volume and in `.env`, never
   in a back-up. A new machine gets a new id right after its files are back, so a
   source and its copy are never mistaken for each other; an existing installation
   keeps its own, also through `--replace`.
8. **Start and verify.** Start everything (`migrate` brings the schema to the
   installed version), then check against the manifest: the counts of documents and
   pages per workspace, one page per workspace opened with the master key, the
   authority's fingerprint equal to the manifest's. A difference is a failure, not a
   warning. Then the marker and `deploy/.restore` go, and the last line is "Herstel
   gelukt."

**When a restore stops halfway** (an error, Ctrl-C, a power cut): the exit trap prints
at which step it stopped and the two ways forward, and `start`, `backup`, `sweep` and
`status` refuse until the restore is finished. The ways forward are the same run or
the safety back-up, restored again with `--replace --skip-safety-backup` (the one
use of that flag). A fresh restore that stopped changes nothing that mattered.

**What this costs.** `--replace` empties first and restores after: an unreadable
files snapshot only shows once the old files are gone, and the old data then exists
only in the safety back-up. That is why the safety back-up is made first, why
`restore_check` runs before the drop, and why the safety back-up is kept 30 days.

A back-up is also the way across a PostgreSQL major version (section 7.6), which is
why it is a logical dump and not a copy of the data directory.

### 4.7 A restore that is tested

- **CI**, the job `restore` next to `stack`, on GitHub-hosted runners only (it ends with
  `down --volumes` of the fixed project `brievie`, which a self-hosted runner may be
  running itself): `./brievie start`; seed one user, two workspaces with a one-page
  letter and a recorded AI decision each, and a paired device, through the
  application's own services; back up to a folder outside the stack; destroy
  everything (containers, every volume, `deploy/.env`); restore from the folder and
  the master key alone, then restore again over the running server with `--replace`.
  After each restore one request per seeded page must return the seeded bytes over a
  connection that trusts only the **old** authority (`--cacert`, never `--insecure`):
  that single hash proves the sealed page opens, the workspace is complete, the device
  token still signs in and the certificate chains to the old authority. A new back-up
  afterwards must succeed without handwork. **Status:** it passed on a GitHub Linux
  runner for the fresh restore and for `--replace` at an earlier commit; at a later
  commit it failed at the last step (the dump's own back-up job still held the task
  lock; settled since, proven in a container but not on a runner). **To be re-run on
  the final commit of B1 before it merges.** The second case of the original design,
  restoring a back-up whose authority is older than a week (Caddy's intermediate has
  then expired), moves to B2: it needs a forged expired intermediate.
- **Proven by hand, for real**, on throw-away Docker projects on a Mac with Docker
  Desktop (the owner's own project checked unchanged before and after every step): a
  fresh restore; a restore onto an empty installation; a `--replace` restore (refused
  without the typed word; with it the safety back-up first, then the database dropped
  and restored; counts, health and the authority's hash equal to the source; a new
  back-up afterwards).
- **The admin**: `./brievie restore-test` (part B2) restores the newest back-up into a
  temporary database and directory beside the real ones, runs the checks of step 8
  against them (the checks take a database and a storage path as arguments), removes
  both, and records "Hersteltest geslaagd op …". It runs in a one-off container with
  the superuser, so it is not a job of the always-on service and the server never
  schedules it; the web warns when the last test is older than 90 days, and a NAS's
  task scheduler can run it. It needs free space for a second copy and says so when
  there is not enough.
- **Never ran for real in B1** (from the final reviews and the build ledger; none of
  this is a reason to doubt the code, all of it is a reason not to promise it yet):
  a REST or S3 destination, for back-up and for restore (and a restore's lock on a
  remote source); a folder on a real disk (exFAT, NTFS, Synology, btrfs), including
  `chown` and `--init`; a large archive or a restore that takes hours; a restore
  interrupted by Ctrl-C or a power cut after the database was dropped; a real
  `pg_restore` failure after the drop; a failed restic restore after the directories
  were emptied; a hostile snapshot with real restic (links, devices, setuid, extended
  attributes); retention over days and weeks and a wrong clock; two installations on one
  repository; `--replace` with another installation's back-up; `sweep --delete` with
  real orphans; a back-up that ends "not complete" (restic's exit code 3); two
  installations on one machine without project names; a Linux host other than the CI
  runner's; a rebuilt image's `BRIEVIE_IN_CONTAINER` guard and the CI step that installs
  the pinned restic for the real-restic tests, on a runner.

### 4.8 In the web interface (server admins)

Part B3, after phase 1d part A. B1 delivers the data it needs and the same information
on the command line. The table `BackupRun` (server-level) holds the runs: kind (`backup`
or `check`), status (queued, running, succeeded, failed, interrupted), when, a reason
code and a fixed Dutch sentence for it, the size, the destination's kind; never a path
or an address. `./brievie status` shows them.

A card "Back-up" under settings: destination (without credentials), last successful
back-up and its size, last check, last restore test, and the warnings: never made,
older than three days, last run failed, recovery sheet not confirmed, restore never
tested or long ago. The local fallback of an update (7.3) does not count as a
back-up here. The card is for server admins, a role phase 1d part A brings; it is
delivered after that part. API: `listBackupRuns`, `getBackupStatus`. Starting a
back-up from the web is a job on the `backup` queue.

### 4.9 The optional layer for organisations

- **Back-ups that cannot be overwritten**: a restic REST server in append-only mode
  on another machine, or object storage with object lock; the server that makes the
  back-ups then cannot remove them, and pruning is done from elsewhere. In B1 the
  setting `BRIEVIE_BACKUP_APPEND_ONLY=true` makes the service never prune or remove
  anything; the REST and S3 sides are described in `SELFHOST.md` and have never run
  for real (4.7).
- **Retention** as settings (B2); **a restore drill** as the restore test run from a
  NAS's task scheduler, with its result in the audit log once there is one; **the key
  with a second person** (4.4).

### 4.10 Safety of everything that deletes

During the build a test called an unguarded "empty this directory" on the developer's
machine, and it stopped at its first entry only because the top-level entries of `/`
were root-owned. Nothing was lost. The rules that came out of it are **binding for
every later part**: the restore test, the update, the workspace export, the archive
folder and everything else that removes, empties, overwrites or hands over files.

1. **A destructive function carries its own refusal.** Whoever calls it, it judges its
   own path first (`apps.backup.safepaths`), before anything is listed, opened or
   created, and refuses with `unsafe_path` or `directory_not_allowed`.
2. **A fixed allow-list of directories in code, independent of settings.** What may be
   emptied, restored into or swept is `/data/documents`, `/data/authority` and
   `/data/backup-config` (the sweep only the documents; Caddy's authority only its
   fixed path below `/caddy-data`; `/backup` never), whatever a setting says. A setting
   outside the list is refused, not followed: a custom storage folder bound at
   `/data/documents` works, one bound elsewhere does not. Only the test suite can add
   a root, one folder strictly inside its own temporary directory. A new part adds its
   directories to the list in code, with a test, in the same commit as the code that
   deletes in them.
3. **Never follow a link; work through directory descriptors.** A directory is opened
   component by component without following a link, checked to be the one that was
   judged, and then worked on relative to that descriptor; nothing crosses into
   another file system; a file with more than one hard link is refused or not handed
   over.
4. **Destructive commands run only inside the container.** The image sets
   `BRIEVIE_IN_CONTAINER=1`; `restore_database`, `restore_files`,
   `sweep_storage --delete` and `backup_init` refuse without it (`not_in_container`).
5. **Guards are pure functions, tested without touching the disk.** They judge the
   path's text first (absolute, depth, a home directory, the fixed list), so the
   outcome is the same on Linux and macOS, as root and as uid 10001; only then do they
   look at the components.
6. **Tests replace every destructive primitive by a recorder** (`os.scandir`,
   `os.unlink`, `shutil.rmtree`, `os.replace`, `os.chown`, a process start, …) that
   fails when called; every directory setting and the restic binary point into the
   test's temporary directory by default; destructive code is never tested against a
   real path outside it.
7. **Real proofs only in containers with throw-away volumes**, in a Docker project of
   their own with its own image and ports, never against the owner's own project;
   that project is compared (containers, image, volumes) before and after every step.
8. **The wrapper fixes its project once and proves ownership.** The project and the
   image are worked out once and passed with `-p`; before it changes anything the
   wrapper proves the installation is its own (container labels, and the installation
   id marker in the data volume against `.env`) and refuses otherwise; a typed word
   confirms a replacement; it writes and removes only fixed names under its own folder
   (`${here:?}`), with unique temporary names; no confirmation can come from piped
   input.

A part that cannot meet one of these says so in its plan, and the owner decides.

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
- **Where the job runs**: on a queue `files`, which nothing serves in B1: the `keeper`
  (4.5) serves only the queue `backup` and holds no master key, and it must stay
  without one. The archive job needs the documents, the master key and the archive
  folder to write in, so part E gives it a worker of its own that holds the master key
  only when the archive folder is switched on (E's plan confirms the shape). It runs as
  the system (it must read private letters); phase 1d's short list of code that may do
  so gains this job, the sweep and the manifest counts. It deletes only under the rules
  of 4.10: the archive folder joins the fixed list in code, nothing is followed through
  a link, and a copy is removed only when it is still the file Brievie wrote.
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

### 6.3a To check on the owner's Synology

The owner has a Synology (model and DSM version not known yet; open). Nothing about
Synology below was confirmed from its documentation, so before the archive folder is
promised on the site, this list is walked through on that NAS:

1. Is the user home service on, and is each user's folder at `/volume1/homes/<naam>`?
2. As which user does a Container Manager project run a container that asks for uid
   10001, and can that container create a folder `Brievie` inside a home folder that
   is bound into it?
3. After Brievie wrote a PDF there: can the NAS user open it in File Station and over
   SMB from a Mac and a Windows PC? Who is shown as the owner?
4. Can another NAS user (not an administrator) see or open that folder?
5. Does a file name with an accent, written by Brievie, look right in File Station,
   on a Mac and on Windows?
6. Do Hyper Backup and snapshots include the bound folder, and the folder given as
   `BRIEVIE_DOCUMENTS_DIR`?
7. With `BRIEVIE_BACKUP_DIR` on a USB disk: what does the container see when the disk
   is unplugged, and does `./brievie backup` then refuse?

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
3. **Stop** the API, the worker and Caddy, as a restore does. The worker gets a stop
   period of a minute; a job that was cut off is picked up again by the existing retry.
   The keeper stays up until step 4 is done, because the back-up is made through it.
4. **Back up**, with the server stopped, so that nothing arrives between the back-up
   and the update and a way back loses nothing. **The emergency back-up is a safety
   back-up in the sense of 4.5**: `./brievie backup --safety` (tagged `kind:safety`,
   no retention in its own run, kept 30 days, its run number printed). Then stop the
   keeper. B1 can make a safety back-up only to a configured destination (without one
   it stops with "Er is nog geen back-up ingesteld"). The local repository in the
   installation folder for a server without a destination, so that there is always a
   way back (it lies next to the key and is not counted as a back-up), is **U1's to
   add** to B1's destination rules, and it must obey 4.10: it may not lie inside a
   data volume, and the keeper must not be able to prune it.
5. **Migrate**, **start**, **check health**.
6. **Record** the version and the time.

The server is away for as long as the back-up takes; restic sends only what is new,
so after the first one that is short.

**The way back** is restoring the back-up of step 4 with the previous version:
`./brievie restore <number> --replace`, where the number is the safety back-up's run
number, which the update prints and the failure message repeats (B1's restore takes a
run number, not a name; a name such as `pre-update-<version>` for it is U1's to add).
Migrations are not reversed. An update is not started automatically, by anything.

**The wrapper's rules apply to `./brievie update` as they do to `restore`** (4.6 and
4.10): the compose project and the image are fixed once and the installation is proven
to be this checkout's before anything changes; nothing starts while a restore is
unfinished (`deploy/.restore-in-progress`), and a restore does not start while an
update is unfinished; only one `./brievie` runs at a time (the lock of 4.5), which a
hand-over to the new checkout's wrapper must carry; the point of no return (stopping
the server, switching the checkout) is preceded by a typed confirmation, which piped
input never gives; the emergency back-up goes through `backup --safety` and the
update stops, restarting the old version, when it fails; the wrapper writes and
removes only fixed names under its own folder. An update that interrupted halfway says
at which step and what to type, as a restore does.

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
  pinned images) instead of cloning the default branch, ends by running
  `./brievie start` (which writes the recovery sheet at the first start; the installer
  writes none itself), and gets `--restore`. Neither the installer's change nor
  `--restore` exists after B1: `install.sh` is unchanged, and a restore on a new
  machine starts from an installed Brievie and `./brievie restore` (12).
- The installation page: updating becomes `./brievie update`; the back-up section
  becomes `./brievie backup`, the destinations, the restore and the sheet; the volume
  tarball command goes.
- The limits page: "back-ups en updates regel je zelf" becomes what the product now
  does and what stays the user's (a destination, the sheet).
- These are texts on the site: **the owner's to approve**.
- The owner holds `brievie.com`; the site, the install command and the names of the
  images can use it. Where its DNS runs is not known yet, and the owner allowed public
  releases on 2026-11-17 ("versies mag je openbaar uitbrengen"). The release workflow
  publishes only on a tag, with the least rights per job, and so that a pull request
  from a fork never reaches a token that can write. It is proven without publishing
  (a build without a push in CI) before the first tag, which the owner's side sets.

### 7.8 The optional layer for organisations

Pinning a version and updating a test instance first (a restore of last night's
back-up into a second installation is exactly that); `SECURITY.md` and GitHub's
security advisories for reporting and announcing; installing without internet from an
archive of the images (`docker save`), with `./brievie update --from-archive`.

## 8. Parts, smallest first

Each part is delivered on its own, with its own plan.

| Part | Content | Needs from the owner |
|---|---|---|
| **B1. Back-up en herstel** (built 2026-11-18 on `feat/b1-backup`; section 14) | the back-up role, the `keeper` service (queue `backup`) and the one-off `admin` container, `./brievie` with `start`, `backup` (`--init`, `--check`), `restore`, `status`, `sweep`, `recovery-sheet`, bringing an existing installation along, the manifest, the recovery sheet, the safety rules of 4.10, the CI restore job, `SELFHOST.md` | nothing |
| **B2. Schema en hersteltest** | the nightly schedule, retention as settings (B1 has it fixed), the weekly check, `restore-test`, the CI case with an old authority | nothing |
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
- A restore that is interrupted after the database was dropped (B1 has never run it
  for real); a back-up whose run was pruned between the choice and the drop; a
  restore from another installation's back-up; two installations on one repository.
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
- Added with the build of B1 (2026-11-18): spec 16's restore from the web interface
  is not built, and neither is a restore from the installer (`install.sh --restore`);
  in B1 a back-up runs only when `./brievie backup` is run (the schedule is B2, the
  button B3); SFTP is not a destination; retention is fixed, not a setting;
  `BRIEVIE_MASTER_KEY_FILE` is not built.
- The 1d design (`2026-11-17-fase-1d-leden-rollen-sso-design.md`) said the database
  has no role that bypasses row-level security. `brievie_backup` is one, read-only,
  used by the back-up service and the sweep only; `docs/DECISIONS.md` (B1, item 1)
  records it. The application's roles still cannot bypass it.

## 11. Left to the builder

The Dutch sentences, the layout of the card and the sheet, names of private helpers,
the exact shell of `./brievie`. Not left to the builder: what a back-up contains, the
order of 4.3, 4.6 and 7.3, that the master key is never in a back-up or on the web,
the checks of the restore test, and the safety rules of 4.10.

## 12. Open points for the plans

- Restic with object lock. (The official restic image on arm64 is confirmed: 4.1.)
- ReportLab and JPEG pages.
- Ownership of archive files on a Synology and a QNAP home folder.
- The releases list without a token.
- How `install.sh --restore` gets the deploy files before anything is installed:
  not built in B1; a restore on a new machine needs an installed Brievie first.
- What B1 never ran for real, each item a reason not to promise it on the site yet:
  the list at the end of 4.7 (REST and S3, a real disk, an interrupted restore,
  retention over time, two installations on one repository, and more).
- Installing but not starting, then restoring: not recognised as an empty
  installation (4.6). Decide in U1 or B2 whether `./brievie restore` should start
  PostgreSQL to find out.

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

Added 2026-11-18, from the final reviews of part B1 (`b1-eindreview.md`,
`b1-eindreview-wispaden.md`, `b1-veiligheidsreview.md` and `-2.md` in the session
notes). History above stays as it was; these rulings came on top, each with its cost:

| Ruling | Why | Cost if wrong |
|---|---|---|
| `--separate-password` keeps a password the user set, writes a new sheet at once, and a restore asks for the second secret itself | the first build overwrote the user's own password and wrote no sheet: back-ups nobody could open | the separate password can only be chosen at the first set-up |
| Both snapshots of a run carry the run's start as their time | retention groups by host and paths, and could keep one half of a run | restic's `--time` must be given in UTC |
| Retention keeps everything of the last two days | a daily rule alone pruned the run the user had just chosen to restore | more snapshots on busy days |
| The safety back-up is tagged, never prunes in its own run, and is removed by its run tag after 30 days from now | restic's `--keep-within` counts from the newest snapshot, so ordinary retention removed it after two to seven days or never | 30 days of extra storage; the user must keep the printed number |
| `./brievie` has a lock, a restore-in-progress marker and a typed WISSEN | a Ctrl-C after the drop left no hint, and a second terminal could start the stack mid-restore | scripts need two environment variables; a stale lock needs a typed JA |
| The installation id is never in a back-up; the restic host follows it | a failed `--replace` with another installation's back-up locked the user out, and two installations on one repository pruned each other | snapshots from before this ruling keep the host `brievie` and are no longer pruned automatically |
| A retention run that sees a snapshot more than 24 hours ahead removes nothing | one wrong clock would remove almost everything | a clock that runs behind is not detected |
| A restore settles the restored queue and checks the chosen run before it drops anything | CI found the dump's own back-up job blocking every new back-up, and the safety back-up pruning the chosen run | one more read of the repository |
| An installation that never held data counts as fresh | "install, then restore" otherwise needed `--replace` and a safety back-up that cannot be made | it must be running to be asked |
| Destructive paths follow 4.10 | the incident of 2026-11-17 | a custom storage folder outside the fixed list is refused |

## 14. What building part B1 changed (2026-11-18)

Built on `feat/b1-backup` from the plan `2026-11-17-host-b1-backup-herstel.md`. Where the
build and this design disagreed, the design was corrected in place (sections 4, 6.3,
7.3, 7.7, 8 to 12) and the difference is recorded here. The final review listed
fourteen disagreements between design, plan, `SELFHOST.md`, `DECISIONS.md` and code;
by item, they are rows 1, 2, 3, 6, 4, 5 (items 1 to 6), 9 and 11 (item 7), 7 (8), 8 (9),
10 (10), 12 (11), 14 (12) and 15 (13). Item 14 (wrong sentences in `SELFHOST.md`) was
corrected on the branch; `DECISIONS.md` (B1, item 3) still lists SFTP among restic's
destinations, which is restic's capability and not Brievie's (row 16).

| # | Change | Why | Cost if wrong |
|---|---|---|---|
| 1 | The back-up password is derived only inside the container (`manage.py derive_backup_password`) and `./brievie` appends it to `deploy/.env`; the installer and `init-env.sh` do not derive it | the host has only `openssl` (a key as an argument is readable in the process list) or `python3` (a NAS may lack it) | `.env` has no back-up password until `./brievie` has run; a back-up needs the image built first |
| 2 | The first `./brievie start` writes the recovery sheet, once; `install.sh` writes nothing and has no `--restore` | the sheet needs the image and the master key's container; `install.sh` lives in the site repository | the site's installer text must say "run `./brievie start`, then print the sheet"; a restore on a new machine needs an installed Brievie |
| 3 | A separate back-up password is chosen at the first set-up only; the wrapper keeps the user's own or makes one, and writes a new sheet at once | the first build overwrote the user's password (final review, critical) | an existing repository cannot move to a separate password without a new repository |
| 4 | The keeper serves only the queue `backup`; the archive job (queue `files`) is part E's, with a worker that holds the master key | the keeper must never hold the master key | E adds a worker; its shape is E's decision |
| 5 | Retention is fixed in code, applied now, in the order of 4.5; B2 makes the keep values settings and adds the schedule | a retention plan "not in B1" left every back-up forever, and the rules of the final reviews needed a place | B2 must keep the two-day window, the keep tag and the host when it makes values settings |
| 6 | The unit tests read as the back-up role with `row_security = off`; the real `pg_dump` is proven in a container stack and in CI | plan decision 8 | a regression in the dump's command line shows only in CI |
| 7 | The manifest's counts come from the dump's own exported snapshot, in a transaction that stays open during the dump | an upload during the back-up made verify report a false difference | a long dump holds a snapshot open |
| 8 | One advisory lock per database; a restore locks the database it restores into; plus a wrapper lock | advisory locks do not span databases | none known |
| 9 | "Holds data" means a `deploy/.env` that exists, unless the installation is running and empty | the wrapper must decide before compose or an image exists | an installed but never started installation is not recognised as empty |
| 10 | A back-up without the `.env` copy fails; the copy is made by `./brievie start` | `fetch` needs it, and the plan's "the restore makes up what is missing" never existed | a scheduler that runs the manage command without `./brievie` fails until the first start |
| 11 | A typed WISSEN, an adoption JA for data that predates the installation id, the safety back-up and its number, `restore_check` before the drop, the restore-in-progress marker, `--skip-safety-backup` only to resume, the keep-destination question (default no), host keys and `COMPOSE_*`/`DOCKER_*` never taken from a back-up, `deploy/.env.before-restore-*`, `BRIEVIE_RESTORE_*` variables for scripts | the reviews of the destructive paths | the old `.env` holds the master key and piles up until the user removes it |
| 12 | Data presence is judged by `deploy/.env`, the project's data volume and container labels; the image is `brievie/backend:<project>` for any project but `brievie` (`:local` only there) | two checkouts on one machine otherwise build over each other's image and share volumes | U1's image tag from a version must reconcile with this |
| 13 | The wrapper keeps terminal output to Dutch sentences; docker's output goes to `deploy/.brievie-last.log` | the first build printed machine lines and docker noise | `BRIEVIE_VERBOSE=1` for everything |
| 14 | The CI case with an authority older than a week moves to B2 | Caddy's pre-made authority cannot be aged without forging an intermediate | untested until B2 |
| 15 | New commands and codes not in the design: `status --list`, `backup --safety`, `restore_check`, `backup_status --list`, the interrupted-run status with its sentences, `BRIEVIE_BACKUP_MOUNT_MODE`, `BRIEVIE_IN_CONTAINER`, `BRIEVIE_ADOPT_INSTALLATION`, `BRIEVIE_RESTORE_CONFIRM`, `BRIEVIE_RESTORE_KEEP_DESTINATION`, `BRIEVIE_RESTORE_EMPTY_OK` | each came from a review finding | more surface to document; `docs/SELFHOST.md` does |
| 16 | Part B1 has no SFTP, no `BRIEVIE_MASTER_KEY_FILE`, no schedule, no web card and no installer change | smaller first part | the site must not promise them |
| 17 | The safety rules of 4.10 (a fixed allow-list in code, descriptors, container-only destructive commands, recorders in tests, a fixed project and a proof of ownership) | the incident of 2026-11-17 | a custom storage folder outside the fixed list is refused, and a destructive command run outside the container does nothing |

What the build proved and did not prove is in 4.7. What later parts must follow is 4.10.
