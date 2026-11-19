# Self-hosting: back-up, restore and people

For a home user with Brievie on a NAS or a Mac, and for the admin of a small company.
Simple things come first, advanced things last. Run every command in the folder where
you installed Brievie. Sections 1 to 12 are about back-ups, 13 about pairing a phone from
a link, 14 to 19 about people: inviting, roles, removing, sessions, the log and the
export.

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
derived from the master key. So keep the recovery sheet, which shows the master key; with
a REST server or S3 storage as the destination, also keep its user name and password
(or access keys), which are not on the sheet (section 3).

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

Brievie's back-up is **not enough on its own**. Also make a copy that Brievie does not
make: a snapshot or copy of the whole machine or NAS volume (with the system's own tool),
and keep the printed recovery sheet somewhere else than that machine and that disk. Then
try a restore at least once a year, on a spare machine or after a clean install (section
7), so you know it works before you need it.

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
  it. Print it at once and remove the file. A separate password can only be chosen at
  this first set-up: a destination that is already set up keeps the password it has. A password with a quote or a line break is
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
safety back-up made by a restore (section 7) is kept 30 days. If a back-up is dated more
than a day in the future (the server's clock was wrong when it was made), nothing is
removed and `./brievie status` says so. A clock that runs behind is not noticed. On
a destination that only accepts new data nothing is removed by Brievie (section 10).

If a back-up stops with "De plek voor de back-up is niet klaar", check first that the
disk is plugged in and mounted; only a destination that was never set up needs
`./brievie backup --init`.

## 7. Restoring

Restoring needs a terminal on the machine. On a Synology that means SSH, or the task
scheduler running the command.

**On a new machine**

1. Put Brievie on the new machine (`git clone`), but **do not start it**:
   `./brievie restore` builds and starts it itself, with the settings and the master
   key from the back-up. A first `./brievie start` would make a new master key and a
   recovery sheet for it, which the restore makes worthless. Did you start it anyway? A
   running installation without users, workspaces or documents counts as empty and is
   replaced after you confirm; the restore removes the sheet that start wrote and tells
   you that any sheet printed from it is void. (Brievie can only see that an
   installation is empty while it runs: a stopped one is treated as one with data.)
2. Connect the disk (or have the address of the remote back-up, and its user name and
   password, ready).
3. `./brievie restore`. It asks where the back-up is and for the master key from your
   sheet. If the back-up has a separate password, it asks for that too. On an empty
   installation it says that the empty installation will be replaced, and asks you to
   confirm.
4. It asks whether this server should keep making back-ups to the same place. Answer
   "ja" only if the original server will never make back-ups there again (section 4).
5. Wait for "Herstel gelukt." Then make a recovery sheet of this server with
   `./brievie recovery-sheet` (it shows the restored master key), print it and remove
   the file.

`./brievie status --list` shows the back-ups and their numbers; `./brievie restore
NUMMER` takes that one instead of the newest. Without a number the newest is taken,
even if it was "gemaakt, maar niet compleet".

A wrong key or password stops at the start, and nothing is written. A back-up from a
newer Brievie than the one installed is refused: update first.

Brievie checks the key against the back-up. With a separate back-up password and a
back-up that holds no workspace yet, there is nothing to check it against: then it says
so and asks you to type the key a second time. Only when both are the same does the
restore go on, so a typo never becomes the server's master key.

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
`BRIEVIE_RESTORE_MASTER_KEY` (and `BRIEVIE_RESTORE_MASTER_KEY_AGAIN` for the second
time), `BRIEVIE_RESTORE_BACKUP_PASSWORD`,
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

A restore also brings back **who was signed in and which phones worked** at the moment
of the back-up. A phone revoked after that back-up works again, and so does a web session
that was ended, a reset link or an invitation that was used or revoked since (as long as
it has not expired). So after a restore:

- Open **Apparaten** and, as an admin, **Leden → Account → Apparaten en sessies** for
  each member, and revoke again every phone that should not work (a lost or stolen one).
- When in doubt, end the sessions: a member does that on **Waar je bent ingelogd**, an
  admin with **Leden → Account → Apparaten en sessies → Alle sessies beëindigen**.
- For an account you do not trust any more, run
  `docker compose ... exec api python manage.py reset_password --email <address>`: it
  sets a new password, ends every session and revokes every phone and open reset link
  of that account.
- Check the open invitations on **Leden** and revoke the ones nobody needs.

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

## 13. Koppelen via een link

The page in the web interface where you pair the app gives a pairing code and a QR code,
and a link that opens the app (`brievie://server#…`). The link holds the
server's addresses and the fingerprint of its certificate authority, never the code.

What the app does with a link:

1. It asks first. Nothing is contacted until you tap "Server zoeken".
2. It then shows the address it found and asks for the eight-character code, which you
   type from the web page.
3. It proves it knows the code without sending it. The proof is bound to the
   certificate authority in the link or, when the link has no fingerprint, to the host
   name of a certificate that a public authority vouches for.

Without a fingerprint in the link the app keeps only the one address it just proved, not
the others in the link. Pairing by QR code works as before and is not affected by any of
this.

A phone that reaches the server through an address that is not in `BRIEVIE_PUBLIC_URLS`
and is not covered by the server's own certificate authority cannot pair from a link
("Open de Brievie-app"); five failed attempts make that code useless and a new one must
be made. A `.local` name, a `.lan` name or a private address binds only through the
server's own certificate authority: no public authority can vouch for those names.

## 14. Inviting someone

An admin opens **Leden** in the web interface and chooses **Iemand uitnodigen**. Nothing
needs configuring: no mail server, no settings. Brievie makes a link and a QR code. Send
the link yourself (a message, a mail you write) or show the QR code. The person opens it,
chooses a name, an e-mail address and a password, and is in. Someone who already has an
account on this server signs in with it instead.

- The link works once, for 7 days by default (1 or 30 days can be chosen), and only
  while the admin who made it is still an admin. **Open uitnodigingen** lists them; an
  admin can withdraw or renew one. Every link that does not work gives the same sentence
  ("Deze uitnodiging werkt niet meer. Vraag een nieuwe."): it never tells why.
- Brievie only stores a fingerprint of the link, so it cannot show it again: renewing
  makes a new link and the old one stops.
- An admin can fill in an e-mail address on an invitation. Then the link only works for
  that address. Brievie sends no mail and does not check that the person owns that
  mailbox: it only checks that the address typed equals the one on the invitation. Treat
  it as a typing check, not as proof of who somebody is.
- The page the person opens also says where their letters go, in one line: whether AI is
  off, on this network or on an outside service (and which, in which country). It also
  says whether an API key is stored there, never the key.

## 15. Roles, in three sentences

An **admin** (beheerder) does everything: invites and removes people, changes roles, sets
AI and exports, writes the rules and reads the log. A **member** (lid) scans, checks and
orders post and manages labels and persons. A **viewer** (meekijker) can only read. A
workspace always keeps at least one admin.

Until private post arrives (a later version), every member and viewer sees all post of
the workspace.

## 16. Removing someone, and what happens to their account and phone

An admin removes a member on **Leden**, or the member leaves a workspace (**Werkruimte
verlaten**). The post stays in the workspace for the others; their person is no longer
linked to an account. The member can export their own post first (section 19); after
leaving they can no longer.

If it was their last workspace, the account is switched off: their web sessions end,
their phones stop working, open pairing codes and reset links are removed, and the account
is no longer a server admin. Somebody who is invited again and
chooses "Ik heb al een account" with the account's own password gets it back, with the
role of the new invitation. A server admin must be appointed again (section 17).
When an account lives in more than one workspace, a removal only affects that workspace;
the phone keeps working for the others.

An admin who loses the admin role also loses what they made: their open invitations and
reset links stop.

**Reset link for a member.** On **Leden**, **Apparaten en sessies** an admin can make a
reset link for a member who forgot the password. The link works once, for 24 hours. It
ends every session of that account and unpairs their phones: they pair again. The member
sees a notice about it. Nobody makes a reset link for their own account. An admin can do
this only for an account that exists in no other workspace and is no server admin; for
those, a server admin does it.

## 17. The server admin

The server admin may read the server's log, and act on accounts that live in more than
one workspace. The person who sets up the server is the first one. The server always
keeps at least one active server admin: removing the last one is refused.

Appoint or remove one on the server (the account must exist and be active):

```sh
docker compose --project-directory deploy -f deploy/docker-compose.yml exec api \
  python manage.py set_server_admin --email naam@voorbeeld.nl
docker compose --project-directory deploy -f deploy/docker-compose.yml exec api \
  python manage.py set_server_admin --email naam@voorbeeld.nl --remove
```

If a server admin forgot their password and nobody else can reset it, choose a new one on
the server. It asks twice and never takes the password as an argument:

```sh
docker compose --project-directory deploy -f deploy/docker-compose.yml exec api \
  python manage.py reset_password --email naam@voorbeeld.nl
```

This ends every session and unpairs every phone of that account, and leaves a notice.
Both commands write the log (the actor is "Brievie").

## 18. Sessions, devices and how long they last

- A web session ends after **14 days without use**, and after **90 days** whatever
  happens. Change that in `deploy/.env` with `BRIEVIE_SESSION_IDLE_DAYS` and
  `BRIEVIE_SESSION_MAX_DAYS` (whole days, at least 1; the first may not be larger than
  the second), then run `./brievie start` again. The new limits apply to a session
  from its next use.
- A paired phone stops working **180 days after it was last used** and answers like a
  revoked one: the app asks to pair again. Nobody loses a phone on the day of the update:
  the 180 days start then.
- **Waar je bent ingelogd** shows a member their sessions (browser, address, last use)
  and phones (with the app version) and lets them end any. An admin sees the phones of a
  member (to revoke one) and can end all their sessions, not read them.
- Changing your own password ends your other sessions and keeps your phone paired.

## 19. The log and the member export

**The log (Logboek).** Brievie records who did what: sign-ins and failures, password
changes, devices paired, revoked and expired, invitations, roles, removals, settings,
exports. It holds names of people and devices and the address a request came from, never
the content of a letter, a label or rule name, a token or a password. Admins read their
workspace's log under **Logboek** (with filters and a CSV file); server admins read the
server's. The rows cannot be changed or deleted by Brievie itself. They are removed
at night once older than `BRIEVIE_AUDIT_RETENTION_DAYS` (default 365, at least 90; a lower
number stops Brievie from starting). A back-up holds the log like the rest of the database.
The CSV file has the newest 10,000 rows at most and says so when it is cut short.

**The export (Mijn post exporteren).** A member can download a ZIP with their own post:
a photo of every page, a data file and a short readme. It is made on request, kept for 24
hours, one per member and workspace. It is sealed on the server and not in the back-up;
once downloaded it is an ordinary unencrypted file, and the BSN is visible on the page
photos. An admin can switch exporting off for the whole workspace (**Instellingen**; off
by default in a business workspace). The export needs the document volume at
`/data/documents`, which is the default; an install with another storage folder cannot
export.
