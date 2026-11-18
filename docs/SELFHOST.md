# Self-hosting: back-up and restore

For a home user with Brievie on a NAS or a Mac, and for the admin of a small company.
Simple things come first, advanced things last. Run every command in the folder where
you installed Brievie.

## 1. In one minute

1. `./brievie start`. The first start writes the recovery sheet,
   `brievie-herstelblad.html`. Print it, keep it off this machine, and remove the file
   (section 3).
2. Plug in an external disk (or mount a NAS share) and put its path in `deploy/.env`:
   `BRIEVIE_BACKUP_DIR=/path/to/the/disk`. Read section 4 first if the disk is new.
3. `./brievie backup --init` prepares the disk once.
4. `./brievie backup` makes a back-up. Done.

Honest note: in this version a back-up is made when you run `./brievie backup`. The
nightly schedule comes in a later version. Until then, run it yourself (for example
every week) or from your NAS's task scheduler.

## 2. What a back-up holds, and what it does not

A back-up holds:

- the database, as a dump made by a role that can only read;
- the document files (still sealed, as on disk);
- the server's own certificate authority, so phones stay paired after a restore;
- a copy of the settings in `deploy/.env`. That copy **does** hold secrets: the
  application's secret key, the database password of the application role and of the
  back-up role, and the credentials of a remote destination. Like everything in the
  back-up it is encrypted with the back-up password.

It does **not** hold the master key (`BRIEVIE_MASTER_KEY`), nor the passwords of the
database superuser and owner. Without the master key the documents cannot be read, so
it must live somewhere else.

restic encrypts the back-up with the back-up password. By default that password is
derived from the master key. So the recovery sheet, which shows the master key, is the
one thing to keep.

## 3. The recovery sheet

The sheet is one page. It shows the server's name, the date, the master key in groups
of four characters, the fingerprint of the server's certificate authority, where the
back-ups go, and the steps of a restore. With a separate back-up password (section 5)
it also shows that second secret.

The first `./brievie start` writes it once, as `brievie-herstelblad.html`. Print it, or
keep it where this machine is not: a password manager, a safe, a relative. Then remove
the file: the master key is on it. Later starts do not write it again;
`./brievie recovery-sheet` makes a new one whenever you need it.

Without the sheet nobody can open the back-up. That includes the makers of Brievie: we
do not have your key and cannot get it.

The sheet does not hold the user name and password of a remote destination (section 4).
Keep those off this machine as well, or a restore cannot reach the back-up.

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

The name of the folder may not contain a quote or a line break. The folder must never
lie inside Brievie's own data (its Docker volumes): a restore over an installation
empties those first.

### A restic REST server or S3-compatible storage

`BRIEVIE_BACKUP_REPOSITORY` in restic's notation, **without credentials in it**:

- `rest:https://host:8000/brievie`
- `s3:https://host/bucket/brievie`

A value with a user name or password in the address is refused. Credentials go in their
own variables:

- REST: `BRIEVIE_BACKUP_REST_USERNAME`, `BRIEVIE_BACKUP_REST_PASSWORD`
- S3: `BRIEVIE_BACKUP_S3_ACCESS_KEY_ID`, `BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY`

SFTP is not supported yet. A NAS share as a folder, or a REST server, does the same job.

Two servers must never make back-ups to the same place. Their old back-ups would be
cleaned up as if they were one server's.

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
- `./brievie backup --init --separate-password`: the back-ups get a password of their
  own, so the master key alone no longer opens them. If `deploy/.env` already holds a
  `BRIEVIE_BACKUP_PASSWORD` that you chose, that one is used; otherwise Brievie makes
  one. Right after the set-up Brievie writes a new recovery sheet with both secrets on
  it. Print it at once and remove the file. A password with a quote or a line break is
  refused.
- A half-finished set-up can simply be run again.

## 6. Day to day

- `./brievie backup` makes a back-up and waits until it is done.
- `./brievie status` shows the last back-up and its size, the last check, the last
  error and the destination (no credentials).
- `./brievie status --list` lists the back-ups on the destination with their number,
  date and whether each is complete. It works while the back-up service is stopped.
- `./brievie backup --check` checks the repository and reads a sample of the data
  back. Run it now and then.

If a back-up says "gemaakt, maar niet compleet" (made, but not complete), one or more
document files could not be read. The back-up is kept and the database in it is whole,
but those files are missing from it, so a restore from it lacks them too. Look for the
cause (a file with the wrong permissions, a disk error) and run it again.

Old back-ups are removed automatically after each back-up: everything of the last two
days, then one a day for a week, one a week for a month and one a month for a year. A
safety back-up made by a restore (section 7) is kept 30 days. If the clock of the
server or of a back-up is wrong, nothing is removed and `./brievie status` says so. On
a destination that only accepts new data nothing is removed by Brievie (section 10).

If a back-up stops with "De plek voor de back-up is niet klaar", check first that the
disk is plugged in and mounted; only a destination that was never set up needs
`./brievie backup --init`.

## 7. Restoring

Restoring needs a terminal on the machine. On a Synology that means SSH, or the task
scheduler running the command.

**On a new machine**

1. Install Brievie on the new machine. Starting it is fine: an installation without
   users, workspaces or documents counts as empty.
2. Connect the disk (or have the address of the remote back-up, and its user name and
   password, ready).
3. `./brievie restore`. It asks where the back-up is and for the master key from your
   sheet. If the back-up has a separate password, it asks for that too. On an empty
   installation it says that the empty installation will be replaced, and asks you to
   confirm.
4. It asks whether this server should keep making back-ups to the same place. Answer
   "ja" only if the original server will never make back-ups there again (section 4).
5. Wait for "Herstel gelukt."

`./brievie status --list` shows the back-ups and their numbers; `./brievie restore
NUMMER` takes that one instead of the newest. Without a number the newest is taken,
even if it was "gemaakt, maar niet compleet".

A wrong key or password stops at the start, and nothing is written. A back-up from a
newer Brievie than the one installed is refused: update first.

The new machine is a new installation: it gets its own installation number, so it is
never mistaken for the original server.

**Over an installation that holds data**

`./brievie restore --replace`. Without the flag a restore refuses an installation that
already holds data. With it, Brievie:

1. asks you to type WISSEN;
2. makes a safety back-up of what is there (kept 30 days; its number is shown);
3. checks that the chosen back-up is still complete; if not, nothing is replaced and
   Brievie starts again;
4. then empties the database and the document folders and puts the back-up in place.

Your old settings are kept as `deploy/.env.before-restore-<date>`. That file holds
secrets, the master key included: remove it once the restore is fine.

**If a restore stops halfway.** From step 4 on, Brievie keeps a note
(`deploy/.restore-in-progress`). Until the restore is finished, `start`, `backup`,
`sweep` and `status` only show what happened and the two ways forward:

- finish it: `./brievie restore NUMMER --replace --skip-safety-backup`;
- go back to your old data: `./brievie restore <safety number> --replace
  --skip-safety-backup`.

`--skip-safety-backup` is meant for exactly this. Elsewhere it means that what is on
the machine now is gone for good.

**Another host name.** The certificate is valid for the new name, but phones know only
the old one, so they pair again. `--keep-hostname` keeps the old name.

**Passwords.** Brievie generates new database passwords. There is nothing to edit.

**What is checked at the end.** The counts of documents and pages against the back-up's
list, one page per workspace opened with the master key, and the fingerprint of the
certificate authority. Brievie then prints, for example, "Het herstel is
gecontroleerd: 2 werkruimtes, van elk is een pagina geopend." and finally
"Herstel gelukt."

For scripts there are variables: `BRIEVIE_RESTORE_SOURCE` (where the back-up is),
`BRIEVIE_RESTORE_MASTER_KEY`, `BRIEVIE_RESTORE_BACKUP_PASSWORD`,
`BRIEVIE_RESTORE_KEEP_DESTINATION=ja`, and the confirmations
`BRIEVIE_RESTORE_CONFIRM=WISSEN`, `BRIEVIE_RESTORE_EMPTY_OK=ja` and
`BRIEVIE_ADOPT_INSTALLATION=JA`. A key in an environment variable is visible to other
programs on the machine; use them only for tests. Never put the confirmations in a
profile: they would switch off the questions for good.

`BRIEVIE_VERBOSE=1` shows all technical output. Otherwise it is in
`deploy/.brievie-last.log`, and a failed step shows its last lines.

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

Tested on GitHub's Linux runners and on a Mac with Docker Desktop: a restore on an
empty machine, a restore with `--replace`, the refusal without WISSEN, retention with
the real restic over one minute. Not yet proven on real hardware or over time:

- a remote destination (REST server or S3), for back-up and restore;
- a folder on a real external disk or NAS (exFAT, NTFS, Synology, btrfs), including the
  permissions of section 4 and `--init`;
- a large archive, and a restore that takes hours;
- a restore that is interrupted (Ctrl-C, a power cut) after the database was emptied,
  and a real database or file restore that fails at that point;
- retention over days and weeks, and a wrong clock;
- two installations on one destination, and `--replace` with another installation's
  back-up;
- `./brievie sweep --delete` with real loose files, and a back-up that is "gemaakt,
  maar niet compleet";
- S3 with object lock, and a REST server in append-only mode in daily use.
