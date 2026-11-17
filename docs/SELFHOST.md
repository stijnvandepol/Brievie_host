# Self-hosting: back-up and restore

For a home user with Brievie on a NAS or a Mac, and for the admin of a small company.
Simple things come first, advanced things last. Run every command in the folder where
you installed Brievie.

## 1. In one minute

1. Plug in an external disk (or mount a NAS share) and put its path in `deploy/.env`:
   `BRIEVIE_BACKUP_DIR=/path/to/the/disk`. Read section 4 first if the disk is new.
2. `./brievie start`, then `./brievie backup --init`.
3. `./brievie recovery-sheet`. Print the sheet, or keep it somewhere that is not this
   machine (section 3).
4. `./brievie backup` makes a back-up. Done.

Honest note: in this version a back-up is made when you run `./brievie backup`. The
nightly schedule comes in a later version. Until then, run it yourself (for example
every week) or from your NAS's task scheduler.

## 2. What a back-up holds, and what it does not

A back-up holds:

- the database, as a dump made by a role that can only read;
- the document files (still sealed, as on disk);
- the server's own certificate authority, so phones stay paired after a restore;
- the settings from `deploy/.env` that open everything, without secrets.

It does **not** hold the master key (`BRIEVIE_MASTER_KEY`). Without that key the
documents cannot be read, so it must live somewhere else.

restic encrypts the back-up with the back-up password. By default that password is
derived from the master key. So the recovery sheet, which shows the master key, is the
one thing to keep.

## 3. The recovery sheet

The sheet is one page. It shows the server's name, the date, the master key in groups
of four characters, the fingerprint of the server's certificate authority, where the
back-ups go, and the steps of a restore. With `--separate-password` (below) it also
shows a second secret.

Print it, or keep it where this machine is not: a password manager, a safe, a relative.
Without the sheet nobody can open the back-up. That includes the makers of Brievie: we
do not have your key and cannot get it. `./brievie recovery-sheet` makes the sheet
again whenever you need it.

Organisations can use two secrets: the master key and a separate back-up password. Set
this up before the first back-up (section 5). The master key
alone then no longer opens the back-ups; a restore asks for both.

## 4. Choosing a destination

Set exactly one of the two. Setting both is refused.

### An external disk or a NAS share

`BRIEVIE_BACKUP_DIR` in `deploy/.env`. The disk must be mounted before Brievie starts,
and the back-up service (user id 10001) must be able to write to it.

- A Linux disk (ext4): `sudo chown 10001:10001 /path/to/the/disk`
- An exFAT or FAT disk cannot be given an owner. Mount it as that user instead:
  `sudo mount -o uid=10001,gid=10001 /dev/sdX1 /path/to/the/disk` (use your own
  device name).
- On a Synology: use a shared folder and give user id 10001 write permission. Not yet
  verified on a Synology.

The name of the folder may not contain a quote or a line break.

### A restic REST server or S3-compatible storage

`BRIEVIE_BACKUP_REPOSITORY` in restic's notation, **without credentials in it**:

- `rest:https://host:8000/brievie`
- `s3:https://host/bucket/brievie`

A value with a user name or password in the address is refused. Credentials go in their
own variables:

- REST: `BRIEVIE_BACKUP_REST_USERNAME`, `BRIEVIE_BACKUP_REST_PASSWORD`
- S3: `BRIEVIE_BACKUP_S3_ACCESS_KEY_ID`, `BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY`

SFTP is not supported yet. A NAS share as a folder, or a REST server, does the same job.

### The 3-2-1 advice in plain words

Keep one copy off the machine, and one out of the house. A disk next to the server
protects you against a broken disk, not against a fire or a burglar. A second disk
that you swap and take elsewhere, or a remote destination, covers that.

## 5. First set-up

`./brievie backup --init`

This prepares the destination once. For a folder it also leaves a marker file there,
and every later back-up refuses a folder without it. That is on purpose: a disk that
is not plugged in would otherwise leave an empty folder on the system disk, and the
"back-up" would sit next to the data it should protect.

- "De back-up staat op dezelfde schijf als de gegevens" (the same-disk refusal): the
  folder is on the same disk as the documents. Choose another disk. Only if you know what you are doing,
  for a test for example: `./brievie backup --init --allow-same-disk`. The check is
  best effort; it can miss some setups, such as Docker Desktop.
- `--separate-password`: use your own back-up password instead of the derived one.
  Put it in `deploy/.env` as `BRIEVIE_BACKUP_PASSWORD` first, and keep a copy. A
  password with a quote or a line break is refused.
- A half-finished set-up can simply be run again.

## 6. Day to day

- `./brievie backup` makes a back-up and waits until it is done.
- `./brievie status` shows the last back-up and its size, the last check, the last
  error and the destination (no credentials).
- `./brievie backup --check` checks the repository and reads a sample of the data
  back. Run it now and then.

If a back-up says "gemaakt, maar niet compleet" (made, but not complete), one or more
files could not be read. The back-up is kept, and the database in it is whole. Look for
the cause (a file with the wrong permissions, a disk error) and run it again.

Old back-ups are removed automatically: everything of the last 7 days, one a week for a
month, one a month for a year. On a destination that only accepts new data nothing is
removed by Brievie (section 10).

## 7. Restoring

Restoring needs a terminal on the machine. On a Synology that means SSH, or the task
scheduler running the command.

**On a new machine**

1. Install Brievie on the new machine.
2. Connect the disk (or have the address of the remote back-up ready).
3. `./brievie restore`. It asks where the back-up is and for the master key from your
   sheet (and the separate back-up password, if you have one).
4. Wait for "Herstel gelukt".

`./brievie restore RUN` takes a specific back-up (its run id) instead of the newest.

A wrong key stops at the start, and nothing is written. A back-up from a newer Brievie
than the one installed is refused: update first.

**Over an existing installation**

`./brievie restore --replace`. Without the flag a restore refuses to touch an
installation that already holds data. With it, Brievie first makes a safety back-up of
what is there. With `--skip-safety-backup` that step is left out, and what is on the machine now is
gone for good.

**Another host name.** The certificate is valid for the new name, but phones know only
the old one, so they pair again. `--keep-hostname` keeps the old name.

**Passwords.** Brievie generates new database passwords. There is nothing to edit.

**What is checked at the end.** The counts of documents and pages against the back-up's
list, one page per workspace opened with the master key, and the fingerprint of the
certificate authority. It ends with "Het herstel is gecontroleerd. Alles klopt."

**If a step fails**, run `./brievie restore` again. The steps are built to be run again.

For scripts there are three variables: `BRIEVIE_RESTORE_SOURCE` (the destination),
`BRIEVIE_RESTORE_MASTER_KEY` and `BRIEVIE_RESTORE_BACKUP_PASSWORD`. A key in an
environment variable is visible to other programs on the machine; use this only for
tests.

## 8. After a restore

The database is copied first, the files after. A letter scanned between those two
moments can have a file in the back-up without a letter in the database. Those are
harmless loose files. `./brievie sweep` lists them; `./brievie sweep --delete` removes
them (section 9).

## 9. Cleaning up loose files

- `./brievie sweep` only lists: a count and a total size.
- `./brievie sweep --delete` removes them.

Files younger than 24 hours are never touched: a letter may just be arriving. It
refuses, and deletes nothing, when it is in doubt: when it cannot read the list of
known pages as the back-up role, when no file on disk matches the database at all, or
when a path has a form it does not know. It never follows a link. An error then means
"nothing was deleted".

## 10. For organisations

- **Back-ups that cannot be overwritten.** If the server is hacked, the back-ups
  should survive. Use a restic REST server started with `rest-server --append-only` on
  another machine, or S3-compatible storage with object lock, and set
  `BRIEVIE_BACKUP_APPEND_ONLY=true`. Brievie then never removes anything there. Old
  back-ups are pruned from another machine, with credentials the server does not have.
- **A separate back-up password** (section 3), so one stolen key is not enough.
- **The master key with two people.** Keep it in the company's secret store, known to
  two people. A sheet in one drawer is a single point of failure.

Planned for a later version: retention as a setting, a scheduled restore test, a
warning in the web interface, and the master key from a Docker secret instead of
`deploy/.env`.

## 11. Where the document files live

For now in a Docker volume on the machine. A setting to place them on a NAS share comes
later. Until then a NAS's own back-up covers the volume only if it reaches it; the
database is not in it either, so `./brievie backup` is still needed.

## 12. Not verified yet

- A real Synology or QNAP (the permissions in section 4).
- exFAT and FAT disks mounted with `uid=10001`.
- A Mac with an external disk through Docker Desktop.
- S3 providers with object lock, and a REST server in append-only mode in daily use.
