# Self-hosting: back-up, restore, people and updates

For a home user with Brievie on a NAS or a Mac, and for the admin of a small company.
Simple things come first, advanced things last. Run every command in the folder where
you installed Brievie. Sections 1 to 12 are about back-ups, 13 about pairing a phone from
a link, 14 to 19 about people: inviting, roles, removing, sessions, the log and the
export. Section 20 is about the AI autopilot, 21 about updating Brievie, 22 about Docker
itself when it is slow, 23 about the trash and what "definitief verwijderen" means for
back-ups, 24 about installing with only a compose file (Portainer, Synology Container
Manager, Dockge, `docker compose up -d`: no checkout, no `./brievie`).

## 1. In one minute

1. `./brievie start`. The first start writes the recovery sheet,
   `brievie-herstelblad.html`. Print it, keep it off this machine, and remove the file
   (section 3).
2. Plug in an external disk (or mount a NAS share) and put its path in `deploy/.env`:
   `BRIEVIE_BACKUP_DIR=/path/to/the/disk`. Read section 4 first if the disk is new.
3. `./brievie backup --init` prepares the disk once.
4. `./brievie backup` makes a back-up. Done.

Once a destination is set, the keeper also makes a back-up every night (at 02:30 server
time; `BRIEVIE_BACKUP_HOUR` changes the hour, and at most one is made in 20 hours, also
after one you made yourself). `./brievie backup` makes one at once. A server admin sees
the state under Account, Back-up in the web interface, with a button "Nu back-uppen".
With only a compose file (section 24) there is no `./brievie`; everything in this
section is done there from the web interface or with `docker compose ... run --rm admin`.

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
moments can have a file in the back-up without a letter in the database (a page, or the
original of an uploaded file). Those are harmless loose files. `./brievie sweep` lists them; `./brievie sweep --delete` removes
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
known pages and originals as the back-up role, when no file on disk matches the database
at all, or
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

## 11a. Adding letters from a browser

"Brief toevoegen" on the Archief page takes PDFs, JPEGs and PNGs (at most 32 MB and 30
pages each). The worker draws the pages and reads the text with Tesseract (Dutch and
English), both in the image: it is about 60-80 MB larger than before. Every page is read
from its picture, also a PDF page that has its own text. On a small NAS a page takes a few
seconds to read, up to a minute at most; one file gets at most 10 minutes in all
(`BRIEVIE_FILE_PROCESSING_SECONDS`, 600), after which it shows as failed. A member may add
50 files per 10 minutes and a workspace 200 per hour (`BRIEVIE_FILE_UPLOADS_PER_MEMBER`,
`BRIEVIE_FILE_UPLOADS_PER_WORKSPACE`). The original is kept next to the pages, so an
upload takes roughly twice the space of a scan; an upload is refused while less than 1 GB
would stay free on the documents' disk (`BRIEVIE_UPLOAD_FREE_RESERVE_BYTES`).

## 11b. The scan folder ("Scanmap")

An office scanner can put its PDFs in a folder on the NAS; Brievie takes each file in and
removes it from the folder. Off by default. Only the `intake` service sees the folder: it
checks every file, keeps it sealed like an upload and hands it to the worker. Its network
reaches PostgreSQL only, never the internet; its disk is read-only; it has no capabilities.

1. Pick one folder for the whole server, for example `/volume1/scans/brievie`. All
   workspaces that use the scan folder get a folder inside it, so everyone with rights on
   it can put a letter in every workspace: **one folder means one level of trust**. Use a
   separate server, or do not use the scan folder, for workspaces that must stay apart.
2. Give the group `users` (gid 100) read and write on that folder, deleting included. The
   `intake` service runs as uid 10001 with that group added (`BRIEVIE_SCAN_GID` when you
   used another group).
   - **Synology, without SSH:** DSM → Configuratiescherm → Gedeelde map: make a shared folder
     (e.g. `scans`) and a folder `brievie` in it. Make a DSM user `scanner` with read/write on
     `scans` only. File Station → `brievie` → Eigenschappen → Machtiging: the group `users`
     read and write, including "Verwijderen" for subfolders and files. In Container Manager →
     Project → Brievie: put `BRIEVIE_SCAN_DIR=/volume1/scans/brievie` in the `.env` and build
     the project again.
   - **Linux:** `sudo chgrp users /srv/scans/brievie && sudo chmod g+rwxs /srv/scans/brievie`
     (the `s` makes new files keep the group).
3. Set `BRIEVIE_SCAN_DIR` in `deploy/.env` (or in the compose-only file's environment) and
   start Brievie again (`./brievie start`, or `docker compose up -d`).
4. In Brievie, as a workspace admin: **Instellingen → Scanmap**, switch it on and keep or
   change the folder name (lower case, digits and `-`, unique on the server). The page shows
   what the `intake` service sees: "De scanmap werkt.", or which owner and group the folder
   has when it cannot read or write it.
5. In the scanner: "scan naar map" over SMB to `\\<nas>\scans\brievie\<folder name>`, as
   the user `scanner`, **one file per letter**, PDF.

What happens to a file: it is taken once its size and time did not change for a minute;
it becomes a letter on nobody's name (as an upload, but nobody "scanned" it); Brievie reads
the sealed copy back and only then removes the file from the share. A removed file is **not**
in Synology's `#recycle`: the recycle bin only covers deletions through SMB, AFP or File
Station. The letter is in Brievie, with its original downloadable. A file Brievie cannot
read goes to `mislukt/` in the workspace folder, with `<name>.reden.txt` beside it that
says why; Brievie never moves or removes anything in `mislukt/`: put a file back to try
again, or remove it yourself. At most 20 files per workspace per minute.

Brievie uses as a workspace's folder only a folder it makes itself or one that is empty:
an existing folder with files or folders in it is never taken in, whatever name an admin
types (the page says so). The first workspace that reads a folder writes
`.brievie-werkruimte` in it; a folder that
another workspace claimed is never read (the page says so), also after that workspace chose
another name, so its old letters never land elsewhere. Remove that file by hand to give the
folder to another workspace.

**Folders per person and department** ("Mappen per persoon en afdeling", off by default):
Brievie makes a folder for every person with an account and for every department while
departments are on. A letter in such a folder gets that person or department as its
addressee directly (automatic, undoable in its history); who else sees it follows the
workspace's "Wie ziet wat". A person without an account gets a folder only when an admin
makes one on the page. A folder Brievie does not know (made by hand with another name) is
never read and nothing in it is moved: the page lists it with its number of files and lets
an admin link it to a person or department. Brievie never renames or removes a folder.

**SMB1:** many older scanners only speak SMB1. Do not switch SMB1 on in DSM (it is
insecure); such a scanner can often send by FTP(S) to DSM instead, into the same folder.

**Not verified yet:** that a DSM ACL granting `users` read and write is honoured for the
container (supplementary gid 100), and that files the scanner makes can be removed by that
group. When the page says it cannot read or write, note the owner and group it shows and
the Machtiging screen; the fix is the folder's rights or `BRIEVIE_SCAN_GID`.

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

A second workspace (another household, an organisation next to your own) is made only on
the server. `--kind` is `home` (Thuis: everyone sees all post) or `business`
(Organisatie: everyone sees their own post and that of their departments); the workspace
starts with that kind's values, as at installation:

```sh
docker compose --project-directory deploy -f deploy/docker-compose.yml exec api \
  python manage.py create_workspace --name "Bakkerij Jansen" --kind business \
  --admin-email naam@voorbeeld.nl
```

When that address has an account, it becomes the workspace's admin. When it has none,
the command prints an invitation link for that address, once: send it to the person, who
becomes the first admin (the link works 7 days, and only while the workspace has no admin).
The server log gets `workspace_created`.

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

### Two-step verification

Everyone can switch it on under **Account → Beveiliging**: after the password Brievie asks
a code from an authenticator app (Microsoft Authenticator, Google Authenticator, the
Passwords app on an iPhone). Setting it up gives ten recovery codes, shown once: keep them
on paper or in a password manager. Paired phones keep working; pairing a new phone then
needs a sign-in with the code.

- **Require it** for the server admins or for everyone in `deploy/.env` (with only a
  compose file: in `.env` next to it), then run `./brievie start` again:

  ```sh
  BRIEVIE_REQUIRE_SECOND_FACTOR=server_admins   # none (default), server_admins, everyone
  BRIEVIE_SECOND_FACTOR_GRACE_DAYS=7            # 0-30 days to switch it on
  ```

  From then on whoever must have it gets a notice with the last day; after that day,
  signing in leads straight to setting it up, and sessions without the code end within an
  hour. The server's start warns (`brievie.W004`) while no server admin has it on.
- **The server's clock must be right**: codes are checked against it (half a minute either
  side). Synology and most NAS boxes keep it right with NTP by default; check it on a
  plain Linux server (`timedatectl`). A server admin sees a warning in the web interface
  when the clocks differ more than 30 seconds.
- **A lost phone.** With a recovery code: sign in with it, then **Beveiliging → Nieuwe
  telefoon**. Without one: an admin clears it under **Mensen → the member → Apparaten en
  sessies → Tweestapsverificatie wissen** (the same rule as a reset link: a server admin
  for anyone, a workspace admin only for a member of that workspace alone who reads no
  private post there). That ends the member's sessions, by default unpairs their phones,
  and leaves a notice. A server admin who is locked out clears it on the server:

  ```sh
  docker compose --project-directory deploy -f deploy/docker-compose.yml exec api \
    python manage.py clear_second_factor --email naam@voorbeeld.nl
  ```

  Add `--keep-devices` only when the lost phone had no paired app. The log records it
  with "Brievie" as the actor.
- **What it does not stop.** Someone who knows your password can keep your second step
  locked by typing wrong codes (from 15 minutes up to a day at a time). You see a notice
  after you sign in ("Iemand heeft ... met jouw wachtwoord geprobeerd in te loggen"):
  change your password then; until the lock ends a recovery code does not help either,
  so an admin can clear the factor for you. And after a grace period, signing in with
  only the password leads to setting a factor up: whoever has the password could enrol
  their own phone before you do. So keep the grace short, and tell people to switch it
  on during it.
- **A paired phone after the grace.** While the rule asks it and the account has no
  factor after the grace period, the account's phones answer "Stel eerst
  tweestapsverificatie in op de webinterface." (they are not unpaired: once it is set up
  on the web they work again), and no new phone can be paired.
- **From outside it is required.** With `BRIEVIE_PUBLIC_WEB=true` (section 25), signing in
  to the web interface through the public address always needs the code. An account
  without two-step verification cannot sign in there: it gets the same answer as a wrong
  password ("... of tweestapsverificatie staat nog niet aan ..."), counted the same, so the
  answer never tells whether the password was right. Switch it on at home or through the
  VPN first. The same holds for setting up the server, accepting an invitation and a reset
  link: for an account without two-step verification they work only at home or through the
  VPN (the link stays usable), and a web session that began at home without the code does
  not count outside. Home and VPN are unchanged.

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

## 20. The AI autopilot

An administrator of a workspace can let the AI put letters under persons and labels, and
confirm them, without a person checking first (the page **AI**, "Automatisch verwerken").
It is off by default in every workspace. `BRIEVIE_AI_AUTOPILOT_ALLOWED` in `deploy/.env`
is the server's permission; its default is `true`. Set it to `false` to forbid the autopilot on this
server: no workspace can switch it on (the API answers 409 `ai_autopilot_forbidden`), and
the pipeline applies nothing automatically, also where a switch is stored as on. Letters
then wait for a person, as before. Run `./brievie start` again after changing it.
It is a setting for the whole server: a single workspace cannot be forbidden yet.

While it is `false`, an administrator still sees a switch that is stored as on and can
switch it off (not on). When you set it back to `true`, switches that were stored as on
work again at once, without a new agreement. If that is not what you want, switch them off
in each workspace first (page **AI**), while the server still forbids the autopilot.

## 21. Updating

### Versions

Brievie has version numbers (`0.2.0`, `0.3.0`, …), one git tag `vX.Y.Z` per release.
What changed is in `CHANGELOG.md`, in Dutch. Read it before you update, and the part
**Voor je bijwerkt** first when a version has one: that is where a release asks
something of you. `/api/v1/health` shows the version that runs (`server_version`), and
`deploy/.env` pins it (`BRIEVIE_VERSION`). Nothing updates by itself: an update only
happens when you type it.

### One command

```sh
./brievie update          # to the newest version
./brievie update 0.3.0    # to that version
```

It asks you to type `BIJWERKEN` before it stops anything (from a script: set
`BRIEVIE_UPDATE_CONFIRM=BIJWERKEN`; piped input never counts). In this order, stopping at
the first problem with a sentence that says what to type:

1. **Check.** The version you typed, your folder (no local changes, no commits of your
   own), that Brievie runs, the versions on GitHub (`git fetch`), whether this jump is
   allowed, and free disk space. Nothing has changed yet; every refusal ends with
   "Er is niets veranderd."
2. **Build** the new version from its tag. Brievie keeps running meanwhile.
3. **Stop** the web interface, the API and the worker.
4. **Back up** (below), then stop the back-up service.
5. **Switch** the folder to the new version, update the database, start, and check that
   the new version answers.
6. **Record** the update: `./brievie status` shows "Laatst bijgewerkt".

Brievie is away from step 3 until step 5 is done: for as long as the back-up and the
database update take. After the first back-up, restic only sends what is new, so that
is usually short.

### The back-up before an update

- **With a back-up destination** (section 4), the update makes a safety back-up there,
  named `pre-update-<version>`, kept 30 days like the safety back-up of a restore.
- **Without one**, it makes an **emergency copy** (noodback-up) in the Docker volume
  `<project>_update-backup` (`brievie_update-backup` for a default install). It holds only
  the newest one (each update replaces it), takes about as much space as one compressed
  copy of your data, and **lies on the same machine**: it is a way back from a failed
  update, not a back-up. It does not count in `./brievie status`. To free the space,
  remove it when no update is unfinished: `docker volume rm brievie_update-backup`.
  `docker compose down -v` (or `--volumes`) removes it too, together with all your data.
- The safety back-up on a destination is kept 30 days. After that, the way back from
  that update is an ordinary back-up of before it, if you have one.

### A jump that is refused

Each release says from which version it can be reached (`deploy/release.json`). When
your version is too old for a direct jump, the update names the version to go to first,
for example "Werk eerst bij naar 0.3.1: ./brievie update 0.3.1". Going back to an older
version is never an update; the way back is a restore (below).

### When an update fails: the way back

Before step 5 nothing changed: the old version is started again and you see "Er is
niets bijgewerkt". If the database update, the start or the check of the new version
fails, Brievie stays stopped and prints the way back, for example:

```
Het bijwerken naar versie 0.3.0 is niet gelukt. Brievie is gestopt.
Terug naar versie 0.2.0 (houd je herstelblad bij de hand):
  git checkout v0.2.0
  ./brievie restore pre-update-0.3.0 --replace --skip-safety-backup
Of probeer het opnieuw: ./brievie update 0.3.0
```

Type those two lines. The restore asks for the master key from your recovery sheet and
for `WISSEN`, as every restore over a running server does (section 7). Without a
destination it finds the emergency copy by itself; with one it asks where the back-up
is. Migrations are never reversed: the way back is always the back-up from before the
update. Anything that arrived after it is not in it, which is why the update stops
Brievie before it backs up.

### An update that was interrupted

A closed terminal or a power cut during an update leaves `deploy/.update/in-progress`.
`./brievie status` then says what to type. Only when the update stopped before the
switch, with the folder exactly as it was, did nothing change: `./brievie start` (or the
next `./brievie update`) starts the old version again. In every other case (the folder
was switched, files are half switched, or the database update began, even if you put the
folder back by hand) `./brievie start`, `status`, `backup`, `sweep --delete` and
`recovery-sheet` refuse and print the way back, until you either finish the update with
`./brievie update` (without a version, or with the same one; a database update can
safely run again) or take the way back. A restore while an update is unfinished only
takes that update's own back-up, after `git checkout` of the old version. When files
were half switched, the way back says `git checkout --force`: the update only starts on
a folder without changes of your own, so that discards nothing of yours. As a second
guard, `./brievie start` refuses code that is older than its database (the database has
steps the code does not know). On a development machine this also happens when you switch
to an older branch after a newer one migrated the database: switch back, or start that
branch on a database of its own (another `COMPOSE_PROJECT_NAME`).

If the terminal closes during an update (an SSH connection that drops), the update goes
on as far as it can and writes the rest to `deploy/.brievie-last.log`; read that file,
or `./brievie status`, when you are back. Over SSH, run a long update inside `tmux` or
`screen` so a dropped connection does not interrupt it. If the machine stops or
`./brievie` is killed, the next `./brievie` sees that the one holding the lock no longer
runs and takes it over (it says so). On Linux a restart of the machine counts too; on a
Mac only a process that is gone does (after a restart the old process number may belong to
another program: then the lock waits for the twelve hours or `JA`, as an older lock
without that information always does).

After the way back, the folder is on a tag, not on a branch (git calls it a "detached
HEAD"). That is fine: `./brievie update` works from there.

If the recovery sheet is lost, the master key is also in `deploy/.env`
(`BRIEVIE_MASTER_KEY`) as long as this machine still has it.

### Not (yet) done by `./brievie update`

- **A new PostgreSQL major version.** A release that moves to one says so in
  "Voor je bijwerkt"; `./brievie update` refuses it until the dump-and-load path exists.
- **An installation from before versions** (it reports 0.1.0) moves once by hand:

  ```sh
  git fetch --tags
  git checkout v0.2.0
  ./brievie start
  ```

  After that, `./brievie update` works.
- **Your own image** (`BRIEVIE_IMAGE` in `deploy/.env`): update by hand.
- **Images from the registry.** Each release is published as
  `ghcr.io/stijnvandepol/brievie-backend:<version>` and `brievie-proxy:<version>` for
  amd64 and arm64. A `./brievie` installation still builds its image from the tag; an
  installation with only a compose file (section 24) uses the published images and
  updates by changing the version number. Both can run on the same volumes, never at the
  same time.

### On a NAS

- PostgreSQL is not published on the machine any more (port 5432 is usually taken by the
  NAS's own database). A developer who runs `make test` against an installation started
  with `./brievie` sets `BRIEVIE_PUBLISH_POSTGRES=true` in `deploy/.env` and runs
  `./brievie start` once.
- Choosing other HTTP and HTTPS ports (`BRIEVIE_HTTP_PORT`, `BRIEVIE_HTTPS_PORT`) is needed
  on almost every NAS; an update keeps them. Having the installer ask for them is not
  done yet.
- The documents live in Docker volumes (section 11); an update does not move them.

### Not verified yet

Proven on GitHub's Linux runners by the CI job `update` (green on pull request 23: an
update, a failing update, and the printed way back typed literally, after which the pages,
the paired device and the certificate authority were back) and with a fake Docker in the
test suite (the interruptions, a dropped terminal, the lock). Not yet proven:
an update on a real NAS or Raspberry Pi (building takes long there, and needs about
4 GB free), an update interrupted by a power cut on real hardware, an update with a
remote destination, and two releases in a row over months.

## 22. When Docker is slow or seems stuck

**`./brievie start` (or `update`, or `restore`) prints "Docker is al 10 minuten bezig
zonder klaar te zijn" and nothing else happens.** `./brievie` waits for Docker and never
stops it by itself; after ten minutes it says so once. The usual cause is Docker, not
Brievie: on a Mac with Docker Desktop the credential helper (`docker-credential-desktop`)
can stop answering when Docker looks up the base image, and the build then waits without
end. What to do:

1. Press Ctrl-C. During `./brievie start` nothing is lost; during `./brievie update` the
   build happens before Brievie is stopped, so Brievie keeps running.
2. Restart Docker Desktop (or sign out and in again in Docker Desktop), or run
   `docker pull python:3.13-slim` to see whether Docker itself answers.
3. Type the command again.

`BRIEVIE_SLOW_SECONDS` sets after how many seconds the line appears (default 600).

**Every `./brievie start` restarted all services, although nothing changed.** That was a
build attestation with the build time in it, which gave every build a new image id with
Docker's containerd image store. `./brievie` now builds without the default attestation
(`BUILDX_NO_DEFAULT_ATTESTATIONS=1`), so a start without changes leaves the services
running. If you build with `docker compose build` yourself, set that variable too.

## 23. The trash, and what "definitief verwijderen" means for back-ups

A member moves a letter to the trash with **Verwijderen** on the letter. It stays in the
**Prullenbak** for 30 days and can be put back (**Terugzetten**); after that Brievie removes
it for good, within the hour (a job at 41 minutes past each hour). An admin can do it
earlier: **Definitief verwijderen** for one letter or **Prullenbak legen** for all letters
in the trash that the admin can see. A private letter the admin may not read stays until
its 30 days are over. Purging only works in the web, not with the app.

"Definitief verwijderen" removes the letter from Brievie for good: its scanned pages, its
text and its tasks. **A back-up made earlier still holds it** until that back-up expires.
With the current retention (section 6) that is up to a year, for the monthly snapshots.
Restoring such a back-up brings the letter back. If a letter must really be gone from
every copy, that is only certain once the last back-up that holds it has expired.

Brievie does not watch a retention term yet, for example the fiscal duty to keep invoices
for seven years. Mind that yourself before you purge a letter early. The check for it will
hook in at one place (`apps.documents.retention.check_removal`).

The purge waits while a back-up or restore runs and goes on at the next hour. A letter
whose files cannot be removed (for example a permissions problem on the document volume)
stays hidden and is tried again every hour; the worker's log names its id.

## 24. Installing with only a compose file

One document, no git checkout, no script, no terminal beyond what your Docker tool already
gives you: paste the file into Portainer (Stacks, Web editor), Synology Container Manager
(Project), Dockge or Unraid's Compose plugin, or save it as `docker-compose.yml` and type
`docker compose up -d`. The file is `deploy/compose/docker-compose.yml`, attached to every
release as `docker-compose.yml` (and as `docker-compose.pinned.yml`, with both images named
by digest). It pulls two images from ghcr: `brievie-backend` and `brievie-proxy` (Caddy with
Brievie's Caddyfile). There is no `docker run` variant: Brievie is several containers.

Needs: Docker with Compose v2 (the file is tested on 2.20.2 and on the version of a
GitHub runner), about 4 GB free, and a fixed name or IP address for the server. The images
are `amd64` and `arm64`. One server per Docker host: the project is called `brievie`, as with
`./brievie`; never start both at once on the same volumes.

### What you change

Only the lines marked `<--`: the **name or IP address** your iPhone uses for the server
(`BRIEVIE_HOSTNAME`; the example name `verander-mij.local` is refused on purpose), the
**ports** when 80 and 443 are taken (a Synology: `BRIEVIE_HTTP_PORT=8080`,
`BRIEVIE_HTTPS_PORT=8443`; the https port becomes part of the address in the QR code) and the
**version** (`BRIEVIE_VERSION`, to update). Put them in the file or, easier, as environment
variables of the stack (Portainer) or in a `.env` next to the file (`deploy/compose/.env.example`
lists them all).

### First start

1. Save or paste the file, set `BRIEVIE_HOSTNAME`, start it (Deploy / Build / `docker compose up -d`).
2. Open `https://<your name>` (the certificate warning is expected, section 5 of the README).
   The installation code is printed once in the log of `api`
   (`docker compose logs api`); or set your own `BRIEVIE_SETUP_TOKEN` (16+ characters).
3. Set up the account, then **Account, Herstelblad**: download the recovery sheet, print it,
   keep it off the server (section 3). On the first start Brievie made the master key itself
   and keeps it in a Docker volume; the sheet is the only other copy.
4. Pair the iPhone (Apparaten), and set up a back-up (below).

What happened at the first start: `init-secrets` (no network) made the keys in volumes: one
vault (`secrets`) and a volume per service (`secrets-postgres`, `secrets-init`, `secrets-api`,
`secrets-keeper`, `secrets-intake`), each with only the files that service needs: `init` has every
password but no master key (no migration or back-up step decrypts a document), the keeper has no
master key, the api no superuser or owner password, the scan folder's `intake` no back-up
password, and PostgreSQL is on a network without a way out. `intake` has a network of its own
that reaches PostgreSQL only. `init` then made the database roles, checked that this version may start on this
data, and ran the migrations. No secret is in the compose file, in `docker compose config`,
in `docker inspect`, in a log or in a process list (the CI job `compose-stack` checks it).

### Ports, volumes, variables

| Port | What |
| --- | --- |
| 80 | forwards to https |
| 443 | the app, the web interface and the iPhone (PostgreSQL is never published) |

Volumes: `pgdata` (database), `documents` (sealed pages), `secrets` and the four
`secrets-*` (the keys; keep the master key on the recovery sheet too), `backup-config` (back-up
settings and the installation id), `caddy-data` (the certificate authority the iPhone trusts:
**never remove it**), `caddy-ca`, `caddy-authority`, `caddy-config`, `update-backup` (the
back-up before an update when you have no back-up folder) and `backup-unset`.
`docker compose down` keeps them; **`down -v` removes all of it**.

The variables are in `deploy/compose/.env.example`: `BRIEVIE_HOSTNAME`, `BRIEVIE_HTTP_PORT`,
`BRIEVIE_HTTPS_PORT`, `BRIEVIE_VERSION`, `BRIEVIE_SETUP_TOKEN`, `BRIEVIE_BACKUP_DIR` or
`BRIEVIE_BACKUP_REPOSITORY` (with the `_S3_` or `_REST_` credentials), `BRIEVIE_BACKUP_AUTO_INIT`,
`BRIEVIE_BACKUP_HOUR`, `BRIEVIE_ADOPT_VERSION`, and the limits of `deploy/.env.example`.
The two secrets of a remote destination, `BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY` and
`BRIEVIE_BACKUP_REST_PASSWORD`, also come as files: put each in a folder on the host (mode 0400, owner
uid 10001), set `BRIEVIE_BACKUP_SECRETS_DIR` to that folder and
`BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY_FILE=/run/backup-secrets/<file name>` (or
`BRIEVIE_BACKUP_REST_PASSWORD_FILE`). Then no secret is in `docker inspect`. The plain variables
still work (and win), but anyone who may use Portainer or `docker inspect` can read them, as with
`deploy/.env` before. The access key id and the REST user name are no secrets.

### Back-ups without a terminal

Set `BRIEVIE_BACKUP_DIR` to a folder (owner uid 10001, on another disk than the data; on a
Synology a shared folder) and `BRIEVIE_BACKUP_AUTO_INIT=true`: the next start prepares an
empty folder once (never one that holds anything, never one on the same disk). Or set
`BRIEVIE_BACKUP_REPOSITORY` for a remote place and run
`docker compose --profile admin run --rm admin python manage.py backup_init` once. From then on the
keeper backs up every night, and a server admin sees the state and asks for one under
Account, Back-up.

### Updating

**Read this first: a refused or failed update takes Brievie down until you set the old version
number back.** Your data stay as they were (a refusal changes nothing), but Compose has already
stopped the old services to make the new ones, so nothing answers until the old number is back and
you start again. Change the version (`BRIEVIE_VERSION`, or the default in the two image lines) and
`up -d` / Deploy again. The first container to start, `init`, does what `./brievie update` does
before it changes anything: it refuses a **step back** or data from a newer version, a jump
past `min_upgrade_from` ("Werk eerst bij naar ..."), another PostgreSQL major, a compose
file of another revision ("Dit compose-bestand is te oud") and too little free space. A
refusal changes no data: `init` stops with exit 1 and `docker compose logs init` has the
sentence (Portainer and Synology show `init` as "Exited (1)"). **The service is down until you
fix it:** Compose has already taken the old api, worker, intake, keeper and caddy down to make the new
ones (they show as "Created"), and they wait for `init`. Put the old version number back and
start again, and the same server comes back (CI proves this with a refused jump and a refused
step back). This is different from `./brievie update`, which checks before it stops anything.

**An update that was interrupted** (a power cut, `docker compose stop`): if it stopped during
the back-up, no migration ran, and the next start makes the back-up again (or, with the old
version number back, lifts the freeze). If it stopped while migrating, the next start finishes
the update when the back-up `pre-update-<version>` exists and otherwise asks for a restore.

When it may go on, `init` makes the database read-only for every session but its own (so
that nothing else can write while it backs up and migrates), makes the back-up
`pre-update-<version>` to your back-up folder or, without one, to the volume
`update-backup`, has restic read a part of it back (a check; a back-up that cannot be read stops the
update before anything changes), migrates, records the version and lifts the freeze. A failure before any migration
lifts the freeze and changes nothing. A failure after one stays read-only and prints the way back:
put the old version number in the file, then type, one by one (these are the commands
`init` prints; a test keeps them equal):

```
docker compose stop api worker intake keeper caddy
BRIEVIE_BACKUP_DIR=update-backup docker compose --profile admin run --rm admin python manage.py restore_install pre-update-<version> --replace
docker compose up -d
```

(`update-backup` is the volume of the back-up before an update; use your own back-up folder
there when the back-up went to it.) Only putting the old number back, without a restore,
also lifts a freeze that an interrupted update left before any migration ran.
When the file itself changes ("Compose-bestand gewijzigd" in the release notes), take the new
file of the release.

### Restoring

Stop `api worker intake keeper caddy`, then
`docker compose --profile admin run --rm admin python manage.py restore_install [<run id or pre-update-X.Y.Z>] [--replace]`.
It refuses while any of those still holds a database session, and `--replace` is needed when
there is data. The back-up is opened with the master key of the vault. **On a new machine
put the key of your recovery sheet in the vault first**: write a file with the line
`BRIEVIE_MASTER_KEY=<the 64 characters, without spaces>` and run, before the first `up`,
`docker compose run --rm -v "$PWD/key.env:/import/env:ro" init-secrets python -m apps.installer.init_secrets --import /import/env`
(then `docker compose up -d postgres`, the restore, `docker compose up -d`). **If you started the stack before importing the key** (the first `up` made new keys and an empty
database; the import now stops with "niet gelijk aan de sleutel in de kluis"), start over. This is
safe only while this machine holds nothing you want to keep: nobody has set up Brievie here and the
back-up folder is not one of these volumes. Then, one by one:

```
docker compose down
docker volume rm brievie_secrets brievie_secrets-postgres brievie_secrets-api brievie_secrets-keeper brievie_secrets-init brievie_secrets-intake brievie_pgdata brievie_documents brievie_backup-config
```

(the project is called `brievie`; check with `docker volume ls`). Your back-up folder, the compose file
and the sheet's key are untouched. Now import the key as above, start only `postgres`, restore, and
start everything. Never remove `brievie_caddy-data` once phones are paired. Where a database
already exists in the volume `pgdata`, the key alone is refused: give the whole `deploy/.env` of
the installation it came from (the database keeps its own superuser password). An unreadable data
folder (a network disk with `root_squash`) also stops `init-secrets`: it never makes new keys
without knowing there is no data. That is also the
way back when the volume `secrets` is lost. `restore_install` makes no safety back-up of what it
replaces: make a back-up first if the data is worth anything.

### Moving from a `./brievie` install

1. `./brievie update` to the version of the compose file (0.5.0 or later), `./brievie backup`,
   `./brievie status`.
2. Stop the stack without removing volumes (`docker compose -p brievie --project-directory deploy -f deploy/docker-compose.yml stop`).
3. Save the compose file next to `deploy/.env` and import the keys from that file (never
   from the environment):
   `docker compose -f docker-compose.yml run --rm -v "$PWD/deploy/.env:/import/env:ro" init-secrets python -m apps.installer.init_secrets --import /import/env`
4. `BRIEVIE_ADOPT_VERSION=<the version that runs now> docker compose --env-file deploy/.env -f docker-compose.yml up -d`
   (`--env-file` feeds only the settings: name, ports, back-up folder).
5. Check the recovery sheet, then move `deploy/.env` out of the checkout.

Volumes, the certificate authority, paired phones and the back-up destination stay. Back to the
wrapper is not supported; restoring a back-up onto a wrapper install is.

### What still needs a terminal

| What | Why | How |
| --- | --- | --- |
| Restoring (`restore_install`) | stops nothing itself, replaces data | `docker compose run` (Synology: Taakplanner as root; Portainer: console of an `admin` container) |
| Cleaning loose files (`sweep --delete`) | deletes files, asks for a typed confirmation | the same |
| A back-up destination with its own password (`--separate-password`) | writes a new sheet with two secrets | the same |
| Installing Docker, the first `up`, a fixed IP or `.local` name | outside Brievie | your Docker tool and router |

### Not verified yet

Proven on GitHub's Linux runners (CI jobs `compose-stack`, `compose-oldest` on Compose 2.20.2,
`adopt`, `compose-update`, `compose-restore`): a fresh start from the file alone, the modes
and owners of the vault, the secrets leaking nowhere, a restart that keeps every secret, an
update, a refused jump and step back that change no data and come back with the old number, a failing
migration that leaves the database read-only, the way back, a restore onto an empty machine
and over a running one, and moving from `./brievie`. **Not tried:** Synology Container
Manager, Unraid, Portainer, Dockge, Podman (not supported), Docker Desktop on Windows and a
Raspberry Pi. Treat every platform step above as untested until someone has run it.

## 25. Van buiten bereikbaar (your own VPN, tunnel or proxy)

At home the phone reaches Brievie at `BRIEVIE_HOSTNAME` and trusts Caddy's own certificate
authority. **Away from home, you set up the way yourself, outside Brievie:** a VPN, a tunnel
of your own (a `cloudflared` container, Tailscale Funnel) or your own reverse proxy. Brievie
ships and manages none of them (owner decision of 26-11, see `docs/DECISIONS.md`): it only
keeps four hooks so it can tell outside traffic from home traffic. A server admin sees what
is set, and what does not work, under **Account, Van buiten bereikbaar** in the web
interface. That page only shows: nothing there changes a setting.

| Way | Privacy | Ease | What is public | Cost |
| --- | --- | --- | --- | --- |
| **VPN** (Tailscale, WireGuard): **recommended** | encrypted from the phone to this server; nobody in between can read it | a VPN app on every phone | nothing | free for a household |
| **A tunnel of your own** (for example Cloudflare Tunnel) | **Cloudflare ends the encryption on its own servers: everything that passes (scanned pages, texts, the device token) can be read there**, and Cloudflare's terms apply | easiest: no port on the router | one name, through the provider | free |
| **Own reverse proxy** (Nginx Proxy Manager, Traefik, Synology) | as safe as your proxy and its certificate; a proxy at a hosting party can read along | you already run one | port 443 of your proxy | your proxy |
| **Home network only** | nothing leaves the house | nothing to do | nothing | free |

The four hooks, all in `deploy/.env` (or the variables of the compose-only install):

| Hook | What it does |
| --- | --- |
| `BRIEVIE_VPN_HOSTNAME` | the server's own VPN name; Caddy serves it with the private CA (private route) |
| `BRIEVIE_PUBLIC_HOSTNAME` | the public name of your tunnel or proxy; a request for it is **always** the public route |
| `BRIEVIE_TRUSTED_PROXIES` | the address(es) your tunnel or proxy connects from; only then is its `X-Forwarded-For` believed |
| `BRIEVIE_PUBLIC_WEB` | the web interface on the public route (off: only the app works) |

**Never forward a router port, or a plain TCP forwarder, to Brievie.** It adds no proxy header, so
Brievie counts such a request as coming from home (private route: web sign-in without the
second step, pairing). Use a VPN, a tunnel, or a reverse proxy that adds its headers, with
`BRIEVIE_PUBLIC_HOSTNAME` set.

Whatever you use forwards to Caddy's normal **https port** (`BRIEVIE_HTTPS_PORT`). There is no
other listener. Through the public name **only the app works** by default: the web
interface and its password sign-in stay closed there (403 "Op dit adres werkt alleen de
Brievie-app"). `BRIEVIE_PUBLIC_WEB=true` opens them, and then a password sign-in there
always needs two-step verification (section "Two-step verification"): whoever has not
switched it on signs in at home or through the VPN only. The page says so, and warns
while a server admin has not switched it on. The VPN stays the better way.

**Upgrading from a version that started a tunnel itself.** `BRIEVIE_TUNNEL_TOKEN`,
`BRIEVIE_TUNNEL_TOKEN_FILE` and `COMPOSE_PROFILES=tunnel` no longer do anything. `./brievie
start` tells you so in Dutch and goes on (it never refuses to start, and the home network keeps
working). Set up your own tunnel as below, set `BRIEVIE_PUBLIC_HOSTNAME` to its public name and
remove the old lines. Take the compose file of this release together with the new
`BRIEVIE_VERSION` (a pasted older file passes the old variable, which the new proxy still
understands, but it also keeps the old `cloudflared` service). A compose file of an older release that still has a `cloudflared` service
only runs it with `COMPOSE_PROFILES=tunnel`, and it forwards to a listener that no longer
exists: use the file of this release.

### VPN (into the home network)

- **VPN into your home network** (WireGuard on the router, a Tailscale subnet router): set
  nothing. `BRIEVIE_HOSTNAME` must be a fixed IP address (or a name your VPN's DNS knows),
  never a `.local` name: those do not resolve over a VPN (the page warns about it).
- **VPN on the server itself** (the Tailscale package on the NAS): see "Tailscale on the NAS".

### Your own Cloudflare Tunnel (a `cloudflared` container next to Brievie)

Brievie does not start it; you run it, in its own compose project or container, wherever you
like (the same NAS, another machine).

1. In the Cloudflare dashboard (Zero Trust, Networks, Tunnels): create a tunnel of type
   *cloudflared* and copy its token into **your own** cloudflared container (not into
   Brievie's `deploy/.env`).
2. Add a public hostname to the tunnel (for example `brievie.example.nl`) with these settings:
   - **Service:** `https://<server-ip>:<BRIEVIE_HTTPS_PORT>` (for example `https://192.168.1.20:443`).
   - **TLS:** *No TLS Verify* (`noTLSVerify: true`), or, better, *Origin Server Name* set to
     your `BRIEVIE_HOSTNAME` and the CA from `GET /api/v1/server/ca-certificate` as the
     origin CA pool.
   - **HTTP Host Header:** keep the original (the public name). Never rewrite it to the
     upstream's address.
3. Set in Brievie:

```
BRIEVIE_HOSTNAME=192.168.1.20
BRIEVIE_PUBLIC_HOSTNAME=brievie.example.nl
# Optional: the address your cloudflared connects from, as Brievie sees it. Never Docker's
# own gateway address (see "Own reverse proxy"). Leave it out when unsure: the public name
# is public anyway, only the visitors then share cloudflared's address for sign-in limits.
# BRIEVIE_TRUSTED_PROXIES=
# Only when you know what it means (see above):
# BRIEVIE_PUBLIC_WEB=true
```

As a `config.yml` of your own cloudflared:

```
ingress:
  - hostname: brievie.example.nl
    service: https://192.168.1.20:443
    originRequest:
      noTLSVerify: true
  - service: http_status:404
```

**Cloudflare ends the encryption on its own servers: everything that passes (scanned pages,
texts, the device token) can be read there**, and Cloudflare's terms apply. Leave these
Cloudflare features **off** for the hostname: Rocket Loader, Email Obfuscation, automatic Web
Analytics (they rewrite pages and break the strict CSP), Bot Fight Mode and "I'm Under Attack"
(their challenges break the app). Cloudflare's free plan accepts uploads up to 100 MB
(Brievie's default is 32 MB: keep `BRIEVIE_MAX_UPLOAD_BYTES` at 100000000 or less) and cuts an
answer after 100 seconds (no Brievie request waits that long). **Cloudflare Access** in front
of the whole hostname blocks the app (an app cannot do Access's browser login); an Access
application that leaves `/api/v1/` out also leaves the web sign-in out, so it is no
replacement for two-step sign-in.

Even before `BRIEVIE_TRUSTED_PROXIES` names your cloudflared, a request for the public name
(or one with Cloudflare's or a proxy's headers) is the public route: app only. The only thing
that differs is the visitor's address for the sign-in limits, which is then cloudflared's own.

### Tailscale on the NAS

Install Tailscale on the NAS itself, switch on MagicDNS in the Tailscale admin console and
set the NAS's MagicDNS name:

```
BRIEVIE_HOSTNAME=192.168.1.20
BRIEVIE_VPN_HOSTNAME=nas.tail1234.ts.net
```

Caddy serves that name with the same certificate authority, so the paired app needs nothing
new. Prefer the MagicDNS name over the `100.x.y.z` address: an iPhone checks the name in the
certificate. This is the private route (like home); nothing is public.

### Own reverse proxy

Your proxy ends TLS with its own public certificate and forwards to
`https://<server>:<BRIEVIE_HTTPS_PORT>` (Caddy's normal port; it may skip verifying Caddy's
certificate, or trust the CA from `GET /api/v1/server/ca-certificate`). It allows uploads of at
least 32 MB; websockets are not needed.

**Two security requirements, before the proxy is reachable from the internet:**

1. **`BRIEVIE_TRUSTED_PROXIES` is set to the proxy's address first.** Until Brievie trusts the
   proxy, it cannot tell the proxy's requests from those of a device in your home. Brievie treats
   a request as public (only the app) when it asks for the public name or carries a proxy's
   headers (`X-Forwarded-For`, `X-Forwarded-Host`, `X-Forwarded-Proto`, `X-Real-IP`,
   `Forwarded`, `Via`) from an address it does not trust, but a proxy that sends none of them and
   rewrites the Host to the home name looks exactly like a home device: the web sign-in would
   then be open to the internet.
2. **The proxy passes the original Host** (`brievie.example.nl`), never the upstream's name or
   address. Nginx Proxy Manager, Traefik and Synology do this by default; plain nginx and
   Apache's `proxy_pass` do not: add `proxy_set_header Host $host;` (nginx) or
   `ProxyPreserveHost On` (Apache).

```
BRIEVIE_HOSTNAME=192.168.1.20
BRIEVIE_PUBLIC_HOSTNAME=brievie.example.nl
BRIEVIE_TRUSTED_PROXIES=192.168.1.5/32
```

**Finding the proxy's address, from the home network only** (the proxy not yet forwarded to from
the internet, no port open on the router):

1. The address is normally the proxy machine's own address in your network (a NAS at
   `192.168.1.5`: `192.168.1.5/32`), or, for a proxy container on the same Docker host, that
   container's address on the network it shares with Caddy.
2. To check it: set `BRIEVIE_PUBLIC_HOSTNAME`, start Brievie, and from a computer at home send
   one request through the proxy (`curl -k https://<proxy>/api/v1/health` with the public name,
   for example `curl --resolve brievie.example.nl:443:192.168.1.5 https://brievie.example.nl/api/v1/health`).
   Then open **Account, Van buiten bereikbaar** at home: under "Eigen reverse proxy" it names
   the local address that request came from. Use it only when it is your proxy's: any device in
   your network can send such a request.
3. Set `BRIEVIE_TRUSTED_PROXIES`, start Brievie again, and only then let the proxy be reached
   from the internet.

**Never put Docker's own gateway address there** (often `172.x.0.1`, or the `.1` of a custom
network): with Docker's port forwarding every device in your network arrives from that address,
and could then pick its own address for the sign-in limits. The api warns about the gateway of
any of its own networks (`brievie.W006`) and refuses to start with `0.0.0.0/0`, `::/0`, an
invalid entry, or a range wider than a /24 (IPv4) or /64 (IPv6) unless
`BRIEVIE_TRUSTED_PROXIES_WIDE=true` (`brievie.E003`).

A request for the public name is **always** treated as public (only the app works there while
`BRIEVIE_PUBLIC_WEB` is off), whatever address it comes from; so is a request with a proxy's
headers from an untrusted address. After the proxy's address changed, nothing opens; the
visitors then only share the proxy's address for the sign-in limits, and the page says so. A
request that carries Cloudflare's headers (as your own cloudflared forwards them) is public
too, and gets one year of HSTS.

### What the page can know

You open the page at home (a web session never counts on the public address). It shows what is
set, what your own request looks like, and what the server **saw on the public route in the
last seven days**: a request for the public name, or with a proxy's headers, from an address
that is not a trusted proxy (with that address, when it is one of your local network), a
trusted proxy that named no client, and a proxy that did not pass the original Host. Only the kind and the time are kept, never a page or a
person. Before any request came that way, those lines are simply absent. The same mistakes in
the settings themselves are also reported when Brievie starts (`./brievie start` and the
api's log: `brievie.W005` for a public name without a trusted proxy).

### How it works (for the curious)

Caddy is the only door. It writes three headers itself on every request to the api and
overwrites what a client sent: `X-Brievie-Route` (`private` for the home and VPN names,
`public` for the public name, a proxy's headers and your trusted proxy), `X-Brievie-Client-IP`
(the client's address after Caddy's own trust rules: `X-Forwarded-For` only from
`BRIEVIE_TRUSTED_PROXIES`; nobody reads `CF-Connecting-IP`) and `X-Brievie-Peer-IP`. The api reads
the address and the route only from those, so the sign-in limits count every visitor by their
own address in every way, and a forged header changes nothing. HSTS is Caddy's: 0 by default
on the home and VPN names (`BRIEVIE_HSTS_SECONDS`), one year on the public routes. The api
derives `ALLOWED_HOSTS` and `BRIEVIE_PUBLIC_URLS` from the names; invitation and reset links
use the public address only while the web interface answers there.

**Pair the app at home or through the VPN**, never through the public address (it answers
403 "Koppel de app thuis of via VPN"): the first pairing, and every change of the app's
addresses, go over a connection pinned to Brievie's own certificate authority, so the party
that ends TLS for the public name (Cloudflare, a proxy) cannot hand the app addresses of its
choosing. The QR code marks which addresses carry a public certificate.

The iPhone app needs a version that trusts each address the way it is served (the private CA
at home, the public certificate of your tunnel or proxy) and learns new addresses from the
server without pairing again. Older apps keep working at home; while a public way is on, the page asks to update every
paired app until the first app version with this support is known.

### Problems

- **400 Bad Request:** the name you used is not one of the three names. Check
  `BRIEVIE_PUBLIC_HOSTNAME`, and leave `ALLOWED_HOSTS` and `BRIEVIE_PUBLIC_URLS` out.
- **The app says "niet geverifieerd" away from home:** the app is too old, or
  `BRIEVIE_PUBLIC_HOSTNAME` is not set.
- **Signing in keeps returning to the sign-in page through your proxy:** the request is not
  seen as https or the Host is not passed on; check `BRIEVIE_TRUSTED_PROXIES` and the proxy's
  Host header. The page shows a proxy that names no client and a Host that is not the
  public name once such a request came through (it keeps what it saw for seven days).
- **413:** a scan above the upload limit; behind Cloudflare at most 100 MB.
- **524 from Cloudflare:** an answer took longer than 100 seconds; try again.
- **"BRIEVIE_TUNNEL_TOKEN staat nog in deploy/.env":** Brievie no longer starts a tunnel; see
  "Upgrading from a version that started a tunnel itself".
- **Your own cloudflared says "502" or a certificate error:** its service must be `https://`
  with TLS verification off or the CA from `/api/v1/server/ca-certificate`, never `http://`.

### Every variable of `deploy/.env.example` and `deploy/compose/.env.example`

"Made" = filled in by `init-env.sh`, `./brievie` or `init-secrets`. Examples per way: H home,
V VPN, T your own tunnel, P own proxy (empty = default or not used).

| Variable | Meaning | Default | H | V | T | P |
| --- | --- | --- | --- | --- | --- | --- |
| `POSTGRES_PASSWORD` | PostgreSQL superuser; only creates the roles | made | | | | |
| `BRIEVIE_OWNER_PASSWORD` | schema owner (migrations) | made | | | | |
| `BRIEVIE_APP_PASSWORD` | application role | made | | | | |
| `POSTGRES_PORT` | host port for PostgreSQL, development only | `5432` | | | | |
| `BRIEVIE_PUBLISH_POSTGRES` | publish PostgreSQL on 127.0.0.1 (development) | off | | | | |
| `DATABASE_URL`, `DATABASE_OWNER_URL` | host-side URLs for `make test` / `make migrate` | made | | | | |
| `SECRET_KEY` | Django secret, at least 50 characters | made | | | | |
| `DEBUG` | never true on a server | `false` | | | | |
| `CSRF_TRUSTED_ORIGINS` | extra https origins; normally not needed | empty | | | | |
| `BRIEVIE_HTTP_PORT` | host port for http (redirect only) | `80` | | | | |
| `BRIEVIE_HTTPS_PORT` | host port for https; part of the home address | `443` | | | | |
| `BRIEVIE_HOSTNAME` | the name or IP the phone uses at home | `localhost` (`./brievie`), `verander-mij.local` (compose) | `192.168.1.20` | `192.168.1.20` | `192.168.1.20` | `192.168.1.20` |
| `BRIEVIE_VPN_HOSTNAME` | the server's own VPN name (VPN on the server) | empty | | `nas.tail1234.ts.net` | | |
| `BRIEVIE_PUBLIC_HOSTNAME` | the public name of your own tunnel or proxy | empty | | | `brievie.example.nl` | `brievie.example.nl` |
| `BRIEVIE_TRUSTED_PROXIES` | the address(es) your own tunnel or reverse proxy connects from, CIDR, space separated | empty (none) | | | `192.168.1.20/32` | `192.168.1.5/32` |
| `BRIEVIE_TRUSTED_PROXIES_WIDE` | allow a trusted range wider than a /24 or /64 (never everything) | `false` | | | | |
| `BRIEVIE_PUBLIC_WEB` | web interface and web sign-in on the public address (sign-in there needs two-step verification) | `false` | | | `false` | `false` |
| `BRIEVIE_HSTS_SECONDS` | HSTS for the home and VPN names; keep 0 with the private CA | `0` | | | | |
| `BRIEVIE_MAX_BODY_BYTES` | largest request body | `2621440` | | | | |
| `BRIEVIE_MAX_UPLOAD_BYTES` | largest scan upload; at most 100 MB behind Cloudflare | `33554432` | | | | |
| `BRIEVIE_MASTER_KEY` | encrypts documents; keep a copy | made | | | | |
| `BRIEVIE_BACKUP_DB_PASSWORD`, `DATABASE_BACKUP_URL` | read-only back-up role | made | | | | |
| `BRIEVIE_BACKUP_PASSWORD` | back-up encryption (derived by default) | derived | | | | |
| `BRIEVIE_BACKUP_DIR` / `BRIEVIE_BACKUP_REPOSITORY` | where back-ups go | empty | | | | |
| `BRIEVIE_BACKUP_S3_ACCESS_KEY_ID`, `BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY`, `BRIEVIE_BACKUP_REST_USERNAME`, `BRIEVIE_BACKUP_REST_PASSWORD` | credentials of a remote back-up | empty | | | | |
| `BRIEVIE_BACKUP_APPEND_ONLY` | the destination accepts only new data | `false` | | | | |
| `BRIEVIE_BACKUP_AUTO_INIT` | compose: prepare an empty back-up folder once | `false` | | | | |
| `BRIEVIE_BACKUP_HOUR` | hour of the nightly back-up | `2` | | | | |
| `BRIEVIE_ADOPT_VERSION` | compose: moving from `./brievie`, the version that runs | empty | | | | |
| `BRIEVIE_VERSION` | the version (compose: change it to update; `./brievie` writes it) | the release | | | | |
| `BRIEVIE_SCAN_DIR` | the scan folder on this server (only the `intake` service sees it) | empty: none | | | | |
| `BRIEVIE_SCAN_GID` | the group that may read and write the scan folder | `100` | | | | |
| `BRIEVIE_AUDIT_RETENTION_DAYS` | days the audit log keeps rows (at least 90) | `365` | | | | |
| `BRIEVIE_SESSION_IDLE_DAYS` / `BRIEVIE_SESSION_MAX_DAYS` | web session limits | `14` / `90` | | | | |
| `BRIEVIE_AI_AUTOPILOT_ALLOWED` | may workspaces switch on the autopilot | `true` | | | | |
| `BRIEVIE_REQUIRE_SECOND_FACTOR` / `BRIEVIE_SECOND_FACTOR_GRACE_DAYS` | who must use two-step sign-in (`none`, `server_admins`, `everyone`), and the days (0-30) to switch it on | `none` / `7` | | | | |
| `BRIEVIE_SETUP_TOKEN` | installation code for the first setup | empty (printed once) | | | | |
| `ALLOWED_HOSTS`, `BRIEVIE_PUBLIC_URLS` | overrides; normally derived from the names above | derived | | | | |
| `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` | only for local http development | `true` | | | | |

Leave alone: `BRIEVIE_INSTALLATION_ID` (written by `./brievie`), `COMPOSE_PROJECT_NAME` and
`BRIEVIE_IMAGE`. Internal values of the compose files (`BRIEVIE_PUBLIC_HOST_MATCH`,
`BRIEVIE_BEHIND_CADDY`, `BRIEVIE_CADDY_CONFIG`) are no settings.

### Not verified yet

Proven in CI on GitHub's Linux runners: the Caddyfile adapts with and without the extra names
and has one listener that reads no `CF-Connecting-IP`; a request as your own cloudflared sends
it (the https port, the public name as Host, an `X-Forwarded-For` from an untrusted address) is
the public route: app only, the web closed unless `BRIEVIE_PUBLIC_WEB`, pairing 403
`pair_at_home`, a forged route header changes nothing, an unknown Host never reaches the api,
the VPN name is served by the private CA and a request without SNI gets a certificate.
**Not tried: a real Cloudflare tunnel, a real reverse proxy, Tailscale and a real VPN.** The
website page (`/nl/install/#publieke-toegang`, `/en/install/#public-access`) follows this
section.
