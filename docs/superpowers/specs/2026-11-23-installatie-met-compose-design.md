# Installeren met alleen een compose-bestand

Datum: 2026-11-23. Branch `docs/compose-install-design` (host). Alleen ontwerp; geen productcode.
Plan: `docs/superpowers/plans/2026-11-23-host-installatie-met-compose.md`.
Sluit aan op `docs/SELFHOST.md` (secties 5, 11, 21), `deploy/` (compose, Caddyfile, `init-env.sh`),
de releaseworkflow (image `ghcr.io/stijnvandepol/brievie-backend:<versie>`, sinds 0.2.0) en het
ontwerp "Van buiten bereikbaar" (branch `docs/external-access-design`, `2026-11-21-externe-toegang-design.md`).

## 1. Aanleiding

Stijn: "Maak de Docker Compose-installatie niet via de terminal maar gewoon via een docker
compose-document. Kijk naar uptimekuma.co/install-uptime-kuma-docker en
adguard-dns.io/kb/adguard-home/docker: neem zoiets over, met die installatieopties." Eerder
vandaag: "Voor Synology staat dat je Linux-stappen gebruikt, terwijl een Synology dit juist via
Docker en Docker Compose doet."

Wat er vandaag nodig is (gelezen in de code, niet aangenomen):

| Stap | Waarom het nu een checkout en een script vraagt |
|---|---|
| `git clone` | `deploy/docker-compose.yml` koppelt `./Caddyfile`, `./caddy-publish-root.sh` en `./postgres-init` als bestanden van de checkout |
| `sh deploy/init-env.sh` | maakt `deploy/.env` met `SECRET_KEY`, drie databasewachtwoorden en `BRIEVIE_MASTER_KEY`; compose weigert te starten zonder (`${VAR:?}`) |
| `./brievie start` | bouwt het image lokaal, controleert eigenaarschap van de volumes (`claim_installation`, installatie-id), start in volgorde, maakt de back-uprol, leidt het back-upwachtwoord af, schrijft het herstelblad en de env-kopie voor de back-up |
| `./brievie update` | haalt een git-tag op, leest `deploy/release.json` van die tag, maakt een noodback-up, bouwt, stopt, zet de map op de tag |
| `./brievie backup/restore/status/sweep/recovery-sheet` | `docker compose run` van `admin`-commando's, met invoer en bevestigingen |

Het image staat sinds 0.2.0 op ghcr (amd64 en arm64), maar niets gebruikt het: compose bouwt zelf
(`pull_policy: never`). Dat is de ontbrekende schakel. De site (`docs/install-page-notes.md`)
schrijft dat Synology "ready-made images" nodig heeft "that do not exist yet": dit ontwerp is wat
dat omdraait.

## 2. Wat de voorbeeldpagina's goed doen

Beide pagina's gelezen (gehaald zonder blokkade). Concreet, per patroon, met wat wij ermee doen.

**Uptime Kuma (uptimekuma.co/install-uptime-kuma-docker)**

1. *Eén blok om te plakken.* Het compose-bestand staat volledig op de pagina, met een
   commentaar achter elke regel die de lezer mag veranderen (`# Set the timezone`,
   `/path/to/data`). De lezer hoeft niets te begrijpen om te starten. **Wij:** een volledig
   bestand met precies drie aan te passen regels (naam of IP van de server, poorten, versie),
   elk met commentaar.
2. *"Kopieer, bewaar, `docker compose up -d`" in drie regels*, zonder git of script. **Wij:**
   hetzelfde, en dezelfde tekst werkt in de GUI's (Portainer, Synology, Dockge).
3. *Eerst "Requirements", dan stappen, dan "Useful commands".* Drie vaste blokken met kopjes
   die in de inhoudsopgave staan. **Wij:** zelfde indeling.
4. *Een korte `docker run`-variant* voor wie geen compose wil. **Wij niet:** Brievie bestaat uit
   meerdere containers (database, api, worker, back-up, proxy); een eerlijke `docker run` bestaat
   niet. We zeggen dat in één zin, in plaats van een onvolledige variant te tonen.
5. *Handige commando's* (`logs -f`, `restart`, `down`, volume verwijderen) als aparte lijst met
   één regel uitleg per commando. **Wij:** dezelfde lijst, met de waarschuwing dat `down -v`
   de documenten en sleutels wist.
6. *Log-rotatie en healthcheck in het voorbeeld zelf* (`json-file`, `max-size 10m`). **Wij:**
   overnemen (logs groeien anders onbeperkt op een NAS).

Wat wij niet overnemen: `container_name` (botst met meerdere installaties), `restart: always`
(wij gebruiken `unless-stopped`, zoals nu), de tag `:2` of `latest` (wij pinnen op een versie), de
`/path/to/data`-map (wij gebruiken named volumes: geen rechtenproblemen op een NAS, behalve bij de
back-upmap, zie 4.5), en de opmerking dat "docker-compose" (v1) volstaat: wij vereisen Compose v2.

**AdGuard Home (adguard-dns.io/kb/adguard-home/docker)**

7. *Snelstart als genummerde stappen:* image ophalen, mappen maken, container starten, daarna
   "open http://127.0.0.1:3000". Elke stap eindigt in iets wat je ziet. **Wij:** de laatste stap
   is "open `https://<naam>` en typ de installatiecode uit de logs".
8. *Lijst "Welke poorten heb je nodig" met per poort waarvoor.* Je neemt alleen over wat je
   gebruikt. **Wij:** een tabel poorten (80, 443) met waarom en wat te doen als de NAS ze al
   gebruikt.
9. *Aparte kopjes voor wat bijzonder is:* "Client IPs" (`--network host`), "DHCP server",
   "`resolved` daemon" (poortconflict op Ubuntu). Eén probleem, één kopje, één oplossing.
   **Wij:** kopjes "Poort 443 is al bezet (Synology, Unraid)", "De iPhone kan de server niet
   bereiken (`localhost`)", "Het certificaat (waarom het zelf-ondertekend is)".
10. *"Update to a newer version" als drie stappen* (pull, stop en verwijder, opnieuw maken). Voor
    compose volstaat één stap; **wij:** "verander het versienummer, `up -d`", plus wat er dan
    vanzelf gebeurt (back-up, controle, weigering van een te oude of te nieuwe stap).
11. *"Additional configuration":* de configuratie staat in één bestand dat je alleen bewerkt
    terwijl de container stopt. **Wij:** alle instellingen staan in het compose-bestand of een
    `.env`; er is geen tweede plek.
12. *Ontwikkelbuilds (`edge`, `beta`) als aparte, duidelijk afgeraden sectie.* **Wij:** geen
    `latest` of `edge`; wel een zin dat er alleen vaste versies zijn.

Wat AdGuard niet doet en Uptime Kuma wel: platformspecifieke secties (Portainer, Synology,
Unraid) komen in geen van beide voor; die vullen wij zelf aan (hoofdstuk 5), omdat Stijn ze
vraagt. De variabelen-, poorten- en volumetabellen zijn ons eigen (betere) antwoord op wat beide
pagina's als losse opsomming doen.

## 3. Uitgangspunten

1. **Één bestand, geen checkout, geen script.** Het bestand staat op de website en in de
   GitHub-release, hoort bij een versie, en werkt met `docker compose up -d` en in de GUI's.
2. **Niets zwakker dan nu.** Geen standaardwachtwoorden, geen geheimen in het bestand,
   PostgreSQL op een intern netwerk, dezelfde Caddy-headers, dezelfde rollen (api ziet geen
   superuser of owner, keeper ziet geen hoofdsleutel).
3. **De veiligheid van `./brievie update` verhuist naar de container.** Er is geen wrapper meer
   die eerst controleert; dus controleert de eerste container die start.
4. **De wrapper blijft naast het compose-bestand bestaan** (zelfde projectnaam `brievie`, dezelfde
   volumes). Fase 2 (niet nu): de wrapper gebruikt zelf dit bestand, zodat er één definitie is.
5. **Eerlijk over de terminal.** Wat zonder terminal niet kan, staat in 4.12 met de reden.
6. Teksten in de afbeelding en bij fouten: Nederlands B1 en Engels, zoals overal.

## 4. Het ontwerp

### 4.1 Images

| Image | Wat | Nieuw? |
|---|---|---|
| `ghcr.io/stijnvandepol/brievie-backend:<versie>` | api, worker, keeper, `init-secrets`, `init`, `admin` | bestaat, krijgt `release.json` en meer commando's mee |
| `ghcr.io/stijnvandepol/brievie-proxy:<versie>` | Caddy 2.11 (op digest gepind) met `Caddyfile` en `caddy-publish-root.sh` erin gebakken | **nieuw**, zelfde bouw-, scan- en attestatiestappen als backend |
| `postgres:18` | database (de major staat in `deploy/release.json` als `postgres_major`) | bestaat |

Waarom een proxy-image: zonder checkout zijn er geen bestanden om aan Caddy te koppelen, en
Portainer en Synology kunnen geen bestanden naast het compose-bestand leggen. Alternatief: inline
`configs: content:` in het compose-bestand; dat vraagt Compose 2.23.1 of nieuwer, en wat een
Synology of Unraid heeft is niet gecontroleerd. De bron blijft één bestand, `deploy/Caddyfile`: de
checkout koppelt het zoals nu, het proxy-image kopieert het. Het label `brievie.caddy-config` (de
vingerafdruk die caddy herstart als het bestand wijzigt) is in het compose-bestand niet nodig: een
andere Caddyfile is een ander image, dus een andere tag.

De versie staat op één plek: `${BRIEVIE_VERSION:-0.5.0}` in beide image-regels. Verander het getal
in het bestand, of zet `BRIEVIE_VERSION` in het `.env` of in Portainer. (Let op bij overstap
van de wrapper: die schrijft `BRIEVIE_VERSION` in `deploy/.env`; zie 4.11.)

### 4.2 Services en volgorde

```
init-secrets  ->  postgres  ->  init  ->  api  ->  caddy
 (geen netwerk)    (healthy)   (klaar)    worker (na init)
                                          keeper (na init)
admin  (profiel "admin", nooit gestart door `up`)
cloudflared  (profiel "tunnel", uit het ontwerp "van buiten bereikbaar", zelfde variabelen)
```

| Service | Image | Doet | Mag | Poort |
|---|---|---|---|---|
| `init-secrets` | backend | maakt ontbrekende geheimen, zet ze per rol klaar, weigert bij gegevens zonder sleutels (4.4) | geen netwerk (`network_mode: none`), root | – |
| `postgres` | postgres:18 | database; wachtwoord via `POSTGRES_PASSWORD_FILE` | leest alleen `secrets-postgres` | geen |
| `init` | backend | rollen en database, versiepoort, noodback-up, migraties, versie vastleggen (4.5) | superuser, owner en hoofdsleutel; uid 10001; stopt na afloop | – |
| `api` | backend | zoals nu | app-rol, hoofdsleutel | geen (alleen via caddy) |
| `worker` | backend | zoals nu | app-rol, hoofdsleutel | geen |
| `keeper` | backend | zoals nu | app- en back-uprol, geen hoofdsleutel | geen |
| `caddy` | proxy | TLS met eigen CA, headers, limieten | publiceert `${BRIEVIE_HTTP_PORT:-80}` en `${BRIEVIE_HTTPS_PORT:-443}` | 80, 443 |
| `admin` | backend | eenmalige commando's (restore, herstelblad, sweep) | alles, root | – |

`migrate` uit het huidige bestand gaat op in `init`: één eenmalige container die de volgorde
rollen, poort, back-up, migratie bewaakt. De `healthcheck` van postgres wordt `pg_isready -d
postgres` (de database `brievie` bestaat pas na `init`).

### 4.3 Netwerken

Twee netwerken, sterker dan vandaag (nu zit alles op één netwerk):

- `db` met `internal: true`: `postgres`, `init`, `api`, `worker`, `keeper`, `admin`. PostgreSQL
  kan niets naar buiten en caddy bereikt de database niet.
- `edge` (gewoon): `caddy`, `api`, `worker`, `keeper`, `admin`, `cloudflared`. Worker, keeper
  en admin hebben uitgaand verkeer nodig (externe AI, S3 of REST-back-up).

Extra aan api, worker en keeper: `security_opt: [no-new-privileges:true]` en `cap_drop: [ALL]`
(strenger dan nu; het CI-pakket bewijst dat alles blijft werken, 4.13).

### 4.4 Geheimen

Het uitgangspunt: **geheimen zijn bestanden in volumes, geen omgevingsvariabelen**. Ze staan niet
in het compose-bestand, niet in `docker inspect` en niet in `compose config`. Instellingen die al
een omgevingsvariabele zijn blijven dat; elke geheime variabele `X` krijgt er een `X_FILE` bij
(`SECRET_KEY_FILE`, `BRIEVIE_MASTER_KEY_FILE`, `BRIEVIE_BACKUP_PASSWORD_FILE`,
`DATABASE_PASSWORD_FILE`, `DATABASE_BACKUP_PASSWORD_FILE`, enzovoort). De variabele wint als hij
gezet is: een wrapper-installatie verandert daardoor niet.

Per geheim (alle bestanden `0400`, in de volumes hieronder):

| Geheim | Hoe gemaakt | Kluis (`secrets`, alleen `init-secrets`, `init`, `admin`) | Per-rolvolume en wie dat leest |
|---|---|---|---|
| `postgres_password` | 24 bytes hex | ja | `secrets-postgres` (uid 999): `postgres` |
| `owner_password` | 24 bytes hex | ja | alleen `init` (kluis) en `admin` |
| `app_password` | 24 bytes hex | ja | `secrets-api` (uid 10001): `api`, `worker`; `secrets-keeper`: `keeper` |
| `backup_db_password` | 24 bytes hex | ja | `secrets-keeper`: `keeper`; kluis: `init`, `admin` |
| `secret_key` | 48 bytes base64 | ja | `secrets-api`, `secrets-keeper`, kluis |
| `master_key` | 32 bytes hex (64 tekens) | ja | `secrets-api`: `api`, `worker`; kluis: `init`, `admin`. **De keeper krijgt hem niet** |
| `backup_password` | afgeleid van de hoofdsleutel (`crypto.derive_backup_password`, geen database nodig), of eigen wachtwoord | ja | `secrets-keeper`: `keeper`; kluis |
| `installation_id` | 16 bytes hex | ja, én `backup-config/installation-id` | beide moeten gelijk zijn (zoals `check_installation_id`) |

Waarom vier kleine volumes in plaats van één: één volume dat iedereen leest is zwakker dan nu (de
keeper zou de hoofdsleutel zien, de api het superuser-wachtwoord). `init-secrets` kopieert bij
elke start de juiste bestanden naar elk rolvolume, met de goede eigenaar; hij overschrijft nooit
een bestaand geheim in de kluis.

**Eerste start, en wanneer hij weigert.** `init-secrets` maakt alleen geheimen als er nog niets
is. Bestaan er al gegevens (een `PG_VERSION` in `pgdata`, bestanden in `documents` of een
`installation-id` in `backup-config`) en is de kluis leeg, dan maakt hij niets en stopt met een
Nederlandse zin: "Er zijn al gegevens, maar de sleutels ontbreken. Zet het volume `secrets`
terug of de sleutels van je herstelblad (`init-secrets import`)." Dat is de vervanger van
`check_settings` en voorkomt het ergste: een nieuwe hoofdsleutel naast bestaande documenten.
De werker controleert daarnaast al met `check_master_key`.

**Importeren (alleen uit een bestand, nooit uit de omgeving).** `--env-file` voedt alleen de
`${}`-invulling van het compose-bestand; een geheim in de omgeving van `init-secrets` zou bovendien
in `docker inspect` van die container blijven staan. Daarom leest de import een *bestand*:
`docker compose run --rm -v "$PWD/deploy/.env:/import/env:ro" init-secrets python manage.py init_secrets --import /import/env`
(`run --rm` laat geen container achter). Het bestand mag een `deploy/.env` van de wrapper zijn
(`BRIEVIE_MASTER_KEY`, `SECRET_KEY`, `POSTGRES_PASSWORD`, `BRIEVIE_OWNER_PASSWORD`,
`BRIEVIE_APP_PASSWORD`, `BRIEVIE_BACKUP_DB_PASSWORD`, `BRIEVIE_INSTALLATION_ID`) of een bestand met
alleen de hoofdsleutel van het herstelblad. De import schrijft eenmalig naar de kluis en werkt alleen
bij een lege kluis; verschilt een waarde van wat de kluis al heeft, dan weigert hij (nooit stil een
andere sleutel). Wie wrapper-gegevens overzet had een terminal (hij draaide `./brievie`), dus dit is
eerlijk. Dit is ook het herstelpad na verlies van het volume `secrets`.

**Wat nooit in een log komt:** alleen de eenmalige installatiecode, zoals nu. Wie de log niet
wil lezen zet zelf `BRIEVIE_SETUP_TOKEN` (minstens 16 tekens) in het bestand.

**Wat de gebruiker zelf bewaart.** De hoofdsleutel (en eventueel het eigen back-upwachtwoord)
staat op het herstelblad, en alleen daar naast de kluis. De back-up bevat de hoofdsleutel
bewust niet (`env_copy`). Verlies van `secrets` én van het blad betekent verlies van de
documenten. Dat is nu ook zo; het verschil is dat de sleutel niet meer in een bestand staat
dat de gebruiker zelf aanmaakte en zag. Daarom is het herstelblad hier belangrijker en krijgt het
een eigen pad zonder terminal (4.7).

### 4.5 `init`: wat er bij elke start gebeurt

Eén commando, `python manage.py prepare_server`, in deze volgorde; elke stap is idempotent:

1. **Naam.** `BRIEVIE_HOSTNAME` mag niet het voorbeeld (`verander-mij.local`) zijn. Loopback
   (`localhost`) mag, met een waarschuwing, zoals nu.
2. **Rollen en database** (wat `postgres-init/01-roles.sh`, `database.psql`, `backup-role.psql` en
   `ensure-backup-role.sh` samen doen): `CREATE ROLE ... WHERE NOT EXISTS`, `ALTER ROLE ... PASSWORD`
   uit de kluis, `CREATE DATABASE brievie OWNER brievie_owner` indien nodig, dezelfde `REVOKE` en
   `GRANT`s. Het eerste initdb-script van de postgres-image wordt dus niet meer gebruikt;
   ook een bestaande database van de wrapper loopt hier doorheen zonder wijziging.
3. **Versiepoort** (4.6). Eerste weigering stopt alles, vóór er iets verandert.
4. **Noodback-up** als dit een update is (nieuwe versie hoger dan de vastgelegde): naar
   `BRIEVIE_BACKUP_DIR` of `BRIEVIE_BACKUP_REPOSITORY` als dat is ingesteld, anders naar het
   volume `update-backup` (zoals `update_backup()` in de wrapper). Label `pre-update-<versie>`.
5. **Migraties** als owner, `createcachetable`.
6. **Vastleggen:** `InstalledVersion` (nieuw, 4.6) en `record_update` (bestaat) met het nummer van
   de noodback-up.
7. **Kopie van de instellingen voor de back-up.** De wrapper bewaart bij elke start een kopie van
   `deploy/.env` zonder geheimen (`store_env_copy`; zonder die kopie weigert een back-up met
   `env_copy_missing`). Zonder `.env` schrijft `init` die kopie zelf: de niet-geheime instellingen uit
   zijn omgeving, door dezelfde bewaker (`apps.backup.envcopy`). Op een nieuwe machine is
   "compose-bestand + herstelblad (hoofdsleutel via de import)" de vervanger van "het `.env`
   opnieuw opbouwen uit die kopie".
8. Uitvoer: alleen Nederlandse zinnen; geen geheim; geen documentinhoud.

### 4.6 Update zonder wrapper: de versiepoort

`./brievie update` doet vandaag vier dingen vóór er iets verandert: kan deze sprong
(`min_upgrade_from`), zelfde PostgreSQL-major, genoeg schijfruimte, en een back-up. Dat staat al in
zuivere code: `apps.updates.release.decide`, `update_plan`, `update_preflight`. De poort roept
hetzelfde aan, in `init`, met `deploy/release.json` dat nu **in het image** komt.

- **Wat is "geïnstalleerd"?** Een nieuwe tabel `InstalledVersion` (één rij: versie, tijdstip). Is
  er geen rij maar wel een database met migraties, dan geldt de laatste `ServerUpdate.to_version`;
  bestaat ook die niet, dan weigert de poort met "Zet `BRIEVIE_ADOPT_VERSION=<versie> `" (de
  gebruiker bevestigt de versie die nu draait, hij moet gelijk zijn aan die van het image). Een
  verse database krijgt de rij zonder vragen.
- **Regels, in deze volgorde** (alle weigeringen: exit 1, een Nederlandse zin, "Er is niets veranderd",
  en de bestaande containers blijven draaien omdat `up` stopt vóór het de afhankelijke
  services opnieuw maakt; *dit gedrag van compose bewijst de CI-stap "weigering laat de oude
  versie draaien" en is nu niet getest*):
  1. image-versie lager dan vastgelegd, of de database heeft migraties die het image niet kent
     (`unknown_migrations`): "Je zet een oudere versie op nieuwere gegevens. Zet versie X terug,
     of herstel de back-up `pre-update-X`.";
  2. sprong groter dan `min_upgrade_from` toelaat (`decide`): "Werk eerst bij naar X";
  3. `postgres_major` van het image is niet de major van de draaiende database;
  4. `BRIEVIE_COMPOSE_REVISION` van het bestand valt buiten wat het image kent: "Dit compose-bestand
     is te oud voor deze versie. Haal het nieuwe bestand op" (zie hieronder);
  5. te weinig schijfruimte (`preflight`).
- **Stoppen van schrijvers.** De wrapper stopt api en worker vóór de back-up. In compose draait de oude
  api nog tot `init` klaar is. Daarom zet `init` vóór de noodback-up
  `ALTER DATABASE brievie SET default_transaction_read_only = on` en sluit bestaande sessies van de
  app-rol (`pg_terminate_backend`). Elke versie van de api, ook de oude, kan dan niet meer
  schrijven. Elke verbinding die `init` zelf opent (superuser, owner, app, back-up) zet
  `default_transaction_read_only=off` in haar eigen opties; de bevriezing geldt voor alle anderen.
  Na afloop gaat het weer uit. Zichtbaar voor de gebruiker: tijdens een update beantwoordt de oude api
  nog leesverzoeken, maar schrijven mislukt tot `init` klaar is (de wrapper stopt de api helemaal);
  anders, niet slechter. Bij een fout vóór de eerste migratie zet `init` het uit en de
  oude versie werkt door; bij een fout ná een migratie blijft het aan en staat in het log de weg terug.
  *Dit is nieuw gedrag en niet eerder getest; het plan heeft er eigen taken voor (T6).*
- **Compose-bestand versus image.** Een update is meestal alleen het versienummer. Verandert de
  structuur (een nieuwe service, nieuw volume), dan staat dat in de releasenotes en gaat
  `BRIEVIE_COMPOSE_REVISION` omhoog (een `ENV` in het backend-image en een regel in het bestand;
  `deploy/release.json` blijft ongemoeid, want installaties die al draaien lezen die van een
  nieuwe tag en weigeren onbekende sleutels). Een bestand van een oudere revisie weigert dan met de
  zin hierboven. Een CI-test houdt bestand en image gelijk.
- **Wat een geweigerde update eruitziet.** `docker compose up -d` eindigt met een fout op `init`
  ("service init didn't complete successfully"); in Portainer en Synology staat `init` op "Exited
  (1)"; `docker compose logs init` toont de zin. De oude versie draait door.
- **De weg terug** na een mislukte migratie: tag terug, dan de back-up `pre-update-<versie>` terugzetten
  met `docker compose run --rm admin restore pre-update-<versie>` (terminal; 4.12). Dit
  vervangt het afgedrukte "weg terug" van de wrapper en het typen van `BIJWERKEN`: er is geen
  bevestigingsstap, de bevestiging is dat de gebruiker het getal heeft veranderd.

### 4.7 Wat de wrapper-commando's worden

| Vandaag | Compose-only | Zonder terminal? |
|---|---|---|
| `start` | `docker compose up -d` (of "Deploy the stack") | ja |
| `update` | versienummer veranderen, `up -d`; poort en noodback-up in `init` | ja |
| `recovery-sheet` | **Nieuw:** pagina "Herstelblad" voor serverbeheerders, na `reauthenticate` (15 min), `Cache-Control: no-store`, auditregel `recovery_sheet_viewed`; ook als laatste stap van de eerste inrichting. Terugval: `docker compose run --rm admin python manage.py recovery_sheet > herstelblad.html` | ja |
| `backup` (nu) | **Nieuw:** nachtelijke back-up door de keeper zodra een bestemming is ingesteld (de periodieke taak bestaat al voor `retry_stalled`; "nightly komt later" staat in SELFHOST 1) en knop "Nu back-uppen" (zet dezelfde job klaar als `cmd_backup`). Terugval: `run --rm admin python manage.py backup` | ja, met besluit D2 |
| `backup --init` | `BRIEVIE_BACKUP_AUTO_INIT=true` laat `init` de bestemming eenmalig klaarzetten als de map er is, het markeerbestand ontbreekt en de schijf niet de datadisk is; zonder die variabele: terugval `run --rm admin python manage.py backup_init` | alleen met de variabele |
| `backup --check`, `status` | **Nieuw:** read-only paneel "Back-up" (laatste run, bestemming bereikbaar, leesbaarheid) voor serverbeheerders | ja, met besluit D2 |
| `restore` | `docker compose run --rm admin restore ...` | **nee** (4.12) |
| `sweep [--delete]` | `docker compose run --rm admin sweep` | nee |
| `status --list` | in het paneel | ja, met besluit D2 |

### 4.8 Het bestand (schets; het echte bestand en zijn tests staan in plan T8)

Alleen het bovenste blok hoeft de gebruiker te lezen. Let op: dit is bewust geen volledige
tekst van elke omgevingsvariabele; de rest staat in de tabel van de website.

```yaml
# Brievie 0.5.0, compose-bestand. Plak dit in Portainer / Synology Container Manager / Dockge,
# of bewaar het als docker-compose.yml en typ: docker compose up -d
# Pas hieronder alleen de regels met  <-- aan.
name: brievie

x-backend: &backend
  image: ghcr.io/stijnvandepol/brievie-backend:${BRIEVIE_VERSION:-0.5.0}   # <-- versie (alleen bij bijwerken)
  restart: unless-stopped
  logging: { driver: json-file, options: { max-size: "10m", max-file: "3" } }

x-settings: &settings
  BRIEVIE_HOSTNAME: ${BRIEVIE_HOSTNAME:-verander-mij.local}   # <-- naam of IP van deze server, zoals je iPhone hem ziet
  BRIEVIE_COMPOSE_REVISION: "1"

services:
  init-secrets:
    <<: *backend
    restart: "no"
    user: "0"
    network_mode: none
    command: ["python", "manage.py", "init_secrets"]
    volumes:
      - secrets:/vault
      - secrets-postgres:/out/postgres
      - secrets-api:/out/api
      - secrets-keeper:/out/keeper
      - backup-config:/data/backup-config
      - documents:/data/documents:ro
      - pgdata:/var/lib/postgresql:ro

  postgres:
    image: postgres:18
    restart: unless-stopped
    depends_on: { init-secrets: { condition: service_completed_successfully } }
    environment:
      POSTGRES_PASSWORD_FILE: /run/brievie/postgres_password
    volumes: [ "pgdata:/var/lib/postgresql", "secrets-postgres:/run/brievie:ro" ]
    networks: [db]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d postgres"]
      interval: 2s
      timeout: 5s
      retries: 30

  init:
    <<: *backend
    restart: "no"
    user: "10001"
    depends_on: { postgres: { condition: service_healthy } }
    command: ["python", "manage.py", "prepare_server"]
    environment:
      <<: *settings
      BRIEVIE_SECRETS_DIR: /run/brievie
      BRIEVIE_ADOPT_VERSION: ${BRIEVIE_ADOPT_VERSION:-}
      BRIEVIE_BACKUP_DIR_SET: ${BRIEVIE_BACKUP_DIR:+true}
      BRIEVIE_BACKUP_REPOSITORY: ${BRIEVIE_BACKUP_REPOSITORY:-}
    volumes: [ "secrets:/run/brievie:ro", "documents:/data/documents:ro", "backup-config:/data/backup-config",
               "caddy-authority:/data/authority:ro", "update-backup:/backup" ]
    networks: [db, edge]

  api:
    <<: *backend
    depends_on: { init: { condition: service_completed_successfully } }
    environment:
      <<: *settings
      SECRET_KEY_FILE: /run/brievie/secret_key
      BRIEVIE_MASTER_KEY_FILE: /run/brievie/master_key
      DATABASE_URL: postgres://brievie_app@postgres:5432/brievie
      DATABASE_PASSWORD_FILE: /run/brievie/app_password
      BRIEVIE_SETUP_TOKEN: ${BRIEVIE_SETUP_TOKEN:-}
      # ...de overige instellingen, zoals in deploy/docker-compose.yml
    volumes: [ "secrets-api:/run/brievie:ro", "documents:/data/documents", "caddy-ca:/caddy-data/caddy/pki/authorities/local:ro" ]
    networks: [db, edge]
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]

  # worker en keeper: zoals nu, met *_FILE in plaats van waarden; keeper mount secrets-keeper
  # en geen hoofdsleutel.

  caddy:
    image: ghcr.io/stijnvandepol/brievie-proxy:${BRIEVIE_VERSION:-0.5.0}
    restart: unless-stopped
    depends_on: { api: { condition: service_healthy } }
    environment: *settings
    ports:
      - "${BRIEVIE_HTTP_PORT:-80}:80"      # <-- Synology: kies bijvoorbeeld 8080
      - "${BRIEVIE_HTTPS_PORT:-443}:443"   # <-- Synology: kies bijvoorbeeld 8443
    volumes: [ "caddy-data:/data", "caddy-ca:/ca", "caddy-authority:/authority", "caddy-config:/config" ]
    networks: [edge]

  # admin (profiel "admin") en cloudflared (profiel "tunnel") zoals in 4.2.

networks:
  db: { internal: true }
  edge: {}

volumes:
  pgdata:
  documents:
  secrets:
  secrets-postgres:
  secrets-api:
  secrets-keeper:
  backup-config:
  caddy-data:
  caddy-config:
  caddy-ca:
  caddy-authority:
  update-backup:
```

Bekende punten van dit bestand, eerlijk:

- *Veel compose-functies in één bestand* (`<<` samenvoegen, ankers, `depends_on.condition`,
  profielen): bewezen in CI op een gepinde, oude Compose (4.13), niet op een Synology of Unraid.
  `depends_on` met `service_completed_successfully` vraagt Compose 2.x; Podman Compose kent het
  niet altijd: **Podman: niet ondersteund tot het is geprobeerd**.
- *Back-upmap van een NAS* (`BRIEVIE_BACKUP_DIR=/volume1/brievie-backup`) is een gewone koppeling
  van een hostmap: de enige plek waar de gebruiker een pad en rechten (uid 10001, SELFHOST 4) moet
  kennen. Het staat als uitgecommentarieerd regelpaar met uitleg in het bestand.
- *Geheime instellingen van de gebruiker* (`BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY`,
  `BRIEVIE_TUNNEL_TOKEN`) zijn omgevingsvariabelen van de stack en dus zichtbaar voor wie Portainer of
  `docker inspect` mag gebruiken. Dat is vandaag niet anders (`deploy/.env`); de pagina zegt het.

### 4.9 De variabelen die een gebruiker zet

Alleen deze staan in het bestand met `<--` of in de tabel op de pagina; alles andere heeft een
standaardwaarde. De volledige lijst volgt de tabel van het ontwerp "van buiten bereikbaar" (11.1),
met dezelfde namen.

| Variabele | Betekenis | Standaard | Verplicht |
|---|---|---|---|
| `BRIEVIE_HOSTNAME` | naam of IP van de server zoals de iPhone hem ziet | `verander-mij.local` (wordt geweigerd) | ja |
| `BRIEVIE_HTTP_PORT`, `BRIEVIE_HTTPS_PORT` | gepubliceerde poorten; de https-poort hoort bij het adres in de QR-code | 80, 443 | nee (op Synology ja) |
| `BRIEVIE_VERSION` | versie van de images | de versie van dit bestand | nee |
| `BRIEVIE_SETUP_TOKEN` | eigen installatiecode (≥ 16 tekens) | leeg: één keer in het log | nee |
| `BRIEVIE_BACKUP_DIR` of `BRIEVIE_BACKUP_REPOSITORY` en de `_S3_`/`_REST_`-gegevens | waar back-ups heen gaan | leeg | nee, sterk aangeraden |
| `BRIEVIE_BACKUP_AUTO_INIT` | bestemming eenmalig klaarzetten | `false` | nee |
| `BRIEVIE_VPN_HOSTNAME`, `BRIEVIE_PUBLIC_HOSTNAME`, `BRIEVIE_TUNNEL_TOKEN`, `COMPOSE_PROFILES=tunnel`, `BRIEVIE_TRUSTED_PROXIES`, `BRIEVIE_PUBLIC_WEB` | van buiten bereikbaar | leeg | nee |
| `BRIEVIE_AUDIT_RETENTION_DAYS`, `_SESSION_IDLE_DAYS`, `_SESSION_MAX_DAYS`, `_AI_AUTOPILOT_ALLOWED`, `_MAX_BODY_BYTES`, `_MAX_UPLOAD_BYTES`, `_HSTS_SECONDS` | zoals in `deploy/.env.example` | idem | nee |

Poorten (website, in het AdGuard-formaat): 80 (doorverwijzing naar https), 443 (de app, het
web en de iPhone); PostgreSQL wordt nooit gepubliceerd.

Volumes (website): `pgdata` (database), `documents` (versleutelde bladzijden), `secrets` en de drie
`secrets-*` (de sleutels; **bewaar de hoofdsleutel ook op het herstelblad**), `backup-config`
(back-upinstellingen en installatie-id), `caddy-data` (de eigen CA waar de iPhone op vertrouwt:
**niet verwijderen**), `caddy-ca`, `caddy-authority`, `caddy-config`, `update-backup`.

### 4.10 Veiligheid: niets zwakker, wat sterker

| Eis | Vandaag | Compose-only |
|---|---|---|
| Geen standaardwachtwoord | `init-env.sh` genereert; placeholder wordt geweigerd | `init-secrets` genereert; het compose-bestand bevat geen wachtwoord |
| Geheim niet in omgeving, `inspect`, `config` | alles in `deploy/.env` en als omgevingsvariabele | geheimen als bestanden; CI-stap bewijst dat geen geheim in `compose config` of `inspect` staat |
| api ziet geen owner of superuser | zo | zo (kluis alleen voor `init`, `init-secrets`, `admin`) |
| keeper heeft geen hoofdsleutel | zo | zo (apart volume) |
| PostgreSQL niet bereikbaar van buiten | niet gepubliceerd | niet gepubliceerd én op intern netwerk |
| Dezelfde Caddy-headers | `deploy/Caddyfile` | hetzelfde bestand, in het image; test vergelijkt bytes |
| Eigen CA en pin | `caddy-data` | zelfde volume, zelfde CA; migratietest bewijst dat de pin blijft kloppen |
| Geen secret in een proces-argument | `init-env.sh` en wrapper | geen argumenten; bestanden en stdin |
| Uitgaand verkeer van de database | mogelijk | niet mogelijk |
| Image herkomst | eigen build | gescand en geattesteerd op ghcr; `docker buildx imagetools inspect` voor de digest staat op de pagina |

Nieuw risico, benoemd: het compose-bestand wordt gekopieerd van een website. Een aanvaller die de
site kan wijzigen kan een andere image-naam zetten. Maatregelen: het bestand staat ook als asset
van de GitHub-release (met de digest van elk image erin in de asset-variant), de pagina noemt de
controle (`gh attestation verify`), en `release-check.py` laat de release mislukken als het bestand niet
bij de versie hoort.

### 4.11 Naast de wrapper, en overstappen

- **Beide blijven.** Wrapper en compose-only gebruiken projectnaam `brievie` en dezelfde
  volumenamen; wie ze op één machine draait, kiest er één. De wrapper-controle
  `check_ownership` (geen container van een andere map met dezelfde projectnaam) kan een
  container zonder `docker.sock` niet doen, en die mounten we bewust niet. We nemen haar dus niet
  over; de pagina zegt dat je nooit beide tegelijk start, en de installatie-id-controle in
  `init-secrets` vangt het gevaarlijkste geval (andere gegevens onder dezelfde naam).
- **Overstappen, wrapper naar compose-only** (stappen op de pagina "Verhuizen"):
  1. `./brievie update` naar de versie die het compose-bestand heeft (minstens 0.5.0);
  2. `./brievie backup`, controleer met `./brievie status`;
  3. `./brievie` stoppen: `docker compose -p brievie ... stop` (staat in de uitleg);
  4. bestand opslaan naast `deploy/.env`, dan eerst de import (4.4):
     `docker compose -f brievie-compose.yml run --rm -v "$PWD/deploy/.env:/import/env:ro" init-secrets python manage.py init_secrets --import /import/env`,
     die de geheimen naar de kluis schrijft en controleert dat ze overeenkomen met het
     `installation-id` in `backup-config`; daarna
     `docker compose --env-file deploy/.env -f brievie-compose.yml up -d` (het `--env-file` voedt
     alleen de niet-geheime instellingen: naam, poorten, back-upmap, versie);
  5. na een gezonde start: hoofdsleutel op het herstelblad controleren, `deploy/.env` bewaren
     buiten de map en uit de checkout verwijderen.
  Volumes, CA, gekoppelde iPhones en de back-upbestemming blijven. CI-job `adopt` (T11) doet dit
  letterlijk.
- **Terug, compose-only naar wrapper:** niet ondersteund in v1 (de kluis staat dan in volumes die de
  wrapper niet kent); wel: restore van een back-up op een wrapper-installatie.

### 4.12 Wat niet zonder terminal kan, en waarom

| Wat | Waarom | Mildering |
|---|---|---|
| Terugzetten (`restore`, `--replace`) | stopt de stack, schrijft root in elk volume, bevestigt met typen (WISSEN) | `run --rm admin restore`; op een Synology via Taakplanner "Door gebruiker gedefinieerd script" (root, geen SSH); in Portainer via de console van een gestarte `admin`-container. **Niet getest** |
| Bestanden opruimen (`sweep --delete`) | verwijdert bestanden, vraagt bevestiging | idem; `sweep` zonder `--delete` kan in het paneel (besluit D2) |
| De weg terug na een mislukte update | = restore | idem |
| Een back-upbestemming met eigen wachtwoord (`--separate-password`) | schrijft een nieuw herstelblad met twee geheimen | `run --rm admin ...`; sheet in de web-pagina |
| Docker zelf installeren en de eerste `up` | het bestand moet ergens draaien | Portainer, Synology Container Manager en Dockge doen de eerste `up` met de knop "Deploy" |
| Een `.local`-naam of vast IP regelen | netwerkinstelling | uitleg op de pagina |
| Controle van de weigering van een update | alleen in de log van `init` | Portainer en Synology tonen die log |

Rest van de lijst uit de huidige wrapper die we **niet** bouwen: de vergrendeling tegen twee
gelijktijdige commando's (compose zelf serialiseert `up`), de `.update`-markeerbestanden (er is geen
halve update die door een script moet worden afgemaakt: `init` is idempotent en begint opnieuw),
de bevestiging `BIJWERKEN` en de watchdog van tien minuten (Docker GUI's tonen voortgang).

### 4.13 CI: hoe het compose-bestand getest wordt

Images van een pull request staan niet op ghcr. Daarom een CI-alleen override
(`deploy/compose/ci.override.yml`) die `image:` op de lokaal gebouwde tags zet
(`brievie/backend:ci`, `brievie/proxy:ci`). Het bestand zelf wordt niet gewijzigd.

| Job | Wat | Bestaat? |
|---|---|---|
| `compose-stack` | zoals `stack`, maar uit het compose-bestand: geen `.env`; eerste start; installatiecode precies één keer; kluisbestanden bestaan met `0400` en de goede eigenaar; geen geheim in `compose config`, `inspect` of een log; api, worker, keeper draaien; `init` eindigt 0; Caddy-headers gelijk aan de Caddyfile | nieuw |
| `compose-oldest` | `docker compose config` en `up` op de **oudste** Compose die we ondersteunen (vast versienummer via `docker/setup-compose-action`); de Synology-gerelateerde risico's (ankers, `<<`, `depends_on.condition`) | nieuw; bewijst oude Compose, niet Synology |
| `update` (uitgebreid) | zelfde throwaway-releases, maar via het compose-bestand: versie omhoog (geslaagd, noodback-up en `record_update`), versie omlaag (geweigerd, oude data intact), sprong te groot (geweigerd, **oude containers draaien nog**), geweigerde `postgres_major` | uitbreiden |
| `restore` (uitgebreid) | een compose-only installatie: back-up, leeg maken, `admin restore`, pagina's en CA terug | uitbreiden |
| `adopt` | wrapper-installatie met data, stoppen, compose-only erop, CA-vingerafdruk gelijk, documenten leesbaar, geheimen geïmporteerd | nieuw |
| `compose-parity` (testbestand, geen job) | `deploy/docker-compose.yml` en `deploy/compose/docker-compose.yml` hebben dezelfde services en dezelfde omgevingssleutels per service (behalve de `_FILE`-paren), dezelfde poorten, dezelfde `depends_on`-ketens; de Caddyfile in het proxy-image is byte voor byte `deploy/Caddyfile` | nieuw |
| release (uitgebreid) | `release-check.py`: standaardversie in het compose-bestand en `BRIEVIE_COMPOSE_REVISION` kloppen met de tag; proxy-image gebouwd, gescand, geattesteerd; bestand als asset aan de release | uitbreiden |
| `dast` | blijft op de oude stack; fase 2 zet hem op het compose-bestand | blijft |

Niet getest, en dat staat zo op de pagina: Synology Container Manager, Unraid, Portainer, Dockge,
Podman, Docker Desktop op Windows, een Raspberry Pi.

### 4.14 Past het bij "van buiten bereikbaar"?

Ja, door deze regels: (1) `cloudflared` en de `profiles: ["tunnel"]` staan in beide bestanden;
beide ontwerpen noemen hem in hun plan, wie later landt voegt toe, `compose-parity` dwingt gelijk
af. (2) Dezelfde variabelen (`BRIEVIE_HOSTNAME`, `BRIEVIE_VPN_HOSTNAME`, `BRIEVIE_PUBLIC_HOSTNAME`,
`BRIEVIE_TUNNEL_TOKEN`, `BRIEVIE_TRUSTED_PROXIES`, `BRIEVIE_PUBLIC_WEB`); het tunneltoken is een door de
gebruiker gegeven geheim in de omgeving van de stack, niet in de kluis. (3) De Caddyfile blijft
één bestand en wordt in het proxy-image gebakken; zijn extra luisteraar `:8080` is niet
gepubliceerd. (4) `COMPOSE_PROFILES=tunnel` in een `.env` naast het bestand is voor Portainer en
Synology wat `./brievie` doet voor wie de wrapper gebruikt. (5) De afleiding in
`apps/access/addresses.py` verandert niet.

## 5. De installatiepagina op de website

De site heeft al `nl/install` en `en/install`, met per platform een kopje (Linux, Docker op elk
ander apparaat, Windows, macOS, Synology als virtuele machine). **Geen nieuwe pagina: deze pagina
wordt herschreven** zodat Docker Compose de hoofdroute is en de andere secties hem aanvullen. Wat
hier staat bestaat pas na de bouw; wat niet getest is staat zo gemarkeerd (label "Niet getest").
`install.sh` blijft bestaan als tweede route voor wie een git-checkout met `./brievie` wil
(Linux, macOS).

1. **Wat je nodig hebt.** Docker met Compose v2 (één regel per platform: Docker Desktop, Linux
   `get.docker.com`, Synology Container Manager, Unraid); een vaste naam of vast IP voor de
   server (met uitleg waarom, in één zin); ± 4 GB vrij; poort 443 vrij of een andere poort.
2. **Installeren met Docker Compose** (aanbevolen). Het blok uit 4.8, versie ingevuld door
   `tools/releases/generate.py`; kopieerknop; drie genummerde stappen die elk eindigen in iets
   zichtbaars ("opslaan als `docker-compose.yml`", `docker compose up -d`, "open
   `https://<jouw-naam>`; typ de installatiecode uit `docker compose logs api`"). Eronder: waar de
   regels met `<--` voor zijn.
3. **Instellingen**: de tabellen *Variabelen*, *Poorten*, *Volumes* uit 4.9. Een
   uitklapregel "Alle variabelen" naar de volledige tabel.
4. **Zonder compose-bestand?** Eén zin waarom een enkele `docker run` niet kan (meerdere
   containers), met verwijzing naar de wrapper-route.
5. **Per platform** (elk: wat je nodig hebt, stappen met wat je ziet, goed om te weten, label
   Getest of Niet getest):
   - *Synology Container Manager* (DSM 7.2): Project aanmaken, pad, bestand plakken, poorten
     wijzigen omdat DSM 80 en 443 gebruikt (en dat de poort in de QR-code staat), "Build",
     logs bekijken voor de installatiecode, back-upmap als gedeelde map met uid 10001. *Niet
     getest.* Vervangt de huidige VM-uitleg (dat vergde een regel in `install-page-notes.md`: de
     ruling van 2026-11-20 "nooit via SSH" blijft waar: dit is geen SSH).
   - *Portainer*: Stacks, Web editor, plakken, omgevingsvariabelen, Deploy. *Niet getest.*
   - *Dockge*: zelfde als Portainer, korter. *Niet getest.*
   - *Unraid*: compose-plugin (Compose Manager), of de Docker-pagina met compose. *Niet getest.*
   - *Linux*: `curl -fsSL https://get.docker.com | sh`, map, bestand, `up -d`.
     Raspberry Pi als noot. Getest op een GitHub-runner, niet op een Pi.
   - *Windows en macOS (Docker Desktop)*: alleen om te proberen; `.local`-namen en poort 443.
     macOS getest met de wrapper, niet met dit bestand; Windows niet.
   - *Podman*: **niet ondersteund**; zegt waarom.
6. **De eerste keer**: ongewijzigd (account, iPhone koppelen, herstelblad afdrukken **via de
   pagina**, back-up instellen).
7. **Bijwerken**: "verander het versienummer, `up -d`", plus wat er gebeurt (back-up, controle,
   weigering), wat een geweigerde update eruitziet en wat te doen; wanneer het bestand zelf nieuw
   moet ("Compose-bestand gewijzigd" in de releasenotes).
8. **Back-up en terugzetten**: instellen (map of externe plek), automatisch 's nachts, controleren
   in het paneel; terugzetten met het `run`-commando en de Synology-taakplanner (*Niet getest*).
9. **Handige commando's** (zoals Uptime Kuma): `logs -f api`, `restart`, `ps`, `down` (zonder `-v`!),
   `config`.
10. **Problemen oplossen** (zoals AdGuard, één probleem per kopje): `init` stopt met "naam" of
    "versie"; poort al bezet; iPhone bereikt de server niet (`localhost`, `.local` over VPN);
    certificaatwaarschuwing; installatiecode kwijt (`run --rm admin python manage.py
    prepare_setup_token --new`); "er zijn gegevens maar de sleutels ontbreken".
11. **Verhuizen vanaf de wrapper** (4.11) en **Verwijderen** (`down -v` wist alles; export eerst).
12. **Van buiten bereikbaar**: de secties van dat ontwerp, met dezelfde variabelen.

## 6. Niet in deze ronde

- Het compose-bestand als enige bron voor de wrapper (fase 2).
- Podman, Kubernetes, Helm.
- Updatemelding in de app ("er is een nieuwere versie"): vraagt uitgaand verkeer naar een
  versieadres; past slecht bij "geen call home" en staat niet in SPEC. De pagina verwijst naar de
  releasepagina.
- Herstellen via de webinterface (een lege installatie biedt "herstel uit back-up" aan): groot,
  gevaarlijk, eigen ontwerp.
- Automatisch bijwerken (Watchtower e.d.): afgeraden op de pagina met de reden dat de poort
  bewust een eigen stap is.

## 7. Besluiten voor Stijn

Alleen vragen waarvan het antwoord verandert wat er gebouwd wordt.

- **D1. Proxy-image of ingebakken `configs:`?** Advies: een eigen proxy-image
  (`brievie-proxy`), omdat het op elke Compose werkt, ook op de oudste in een NAS. Alternatief
  `configs: content:` scheelt een image in de release, maar vraagt Compose ≥ 2.23.1 en wat
  een Synology heeft is onbekend. Verandert taak T7 en de release-workflow.
- **D2. Back-up zonder terminal: nachtelijk schema, paneel en knop erbij?** Advies: ja, in
  deze ronde: de keeper maakt 's nachts een back-up zodra er een bestemming is, het paneel
  toont de status en "Nu back-uppen". Zonder dit is "geen terminal" een belofte die voor
  back-ups niet klopt (SELFHOST 1 zegt nu zelf: "de back-up maak je zelf"). Alternatief: nu
  alleen het `run`-commando en het schema later. Verandert T10 (ongeveer een derde van het
  werk).
- **D3. Herstelblad via de webinterface?** Advies: ja, voor serverbeheerders na opnieuw
  inloggen, met auditregel en `no-store`; ook als verplichte laatste stap in de eerste
  inrichting. Het blad bevat de hoofdsleutel, dus de sleutel gaat over https naar de browser van
  de beheerder: dezelfde blootstelling als het HTML-bestand dat de wrapper nu naast de
  checkout zet. Alternatief: alleen `run ... > blad.html`; dat is voor een Synology-gebruiker
  zonder SSH geen pad. Verandert T9 en de inrichtingswizard.
- **D4. Eén kluis of vier volumes?** Advies: vier (kluis en drie rolvolumes), zodat de
  keeper geen hoofdsleutel en de api geen superuser ziet, zoals nu. Eén volume is eenvoudiger
  op een NAS (minder volumes in de lijst) maar zwakker dan vandaag. Verandert T4 en de
  volumelijst op de pagina.
- **D5. Een update zonder schrijfstop?** Advies: de database één keer op "alleen lezen" zetten
  tijdens de noodback-up en migratie (4.6), zodat ook de oude api niet kan schrijven. Alternatief: een
  "warme" back-up zoals een gewone `pg_dump` (de enige garantie is een consistente dump; een brief die in die
  paar minuten wordt gescand kan bij terugzetten verloren gaan). Verandert T6 (de
  schrijfstop is ongeveer 30 regels met tests).
- **D6. Wat zeggen we over Synology, Unraid en Portainer zolang niets daarvan is geprobeerd?**
  Advies: de secties staan op de pagina met het label "Niet getest", en één van ons probeert elke
  stap op een echte Synology voor de release (de pagina verandert daarna in "Getest"). Alternatief:
  de secties pas publiceren na de test. Verandert alleen wanneer de site-taken live gaan, niet de
  bouw.
- **D7. Volgorde met "van buiten bereikbaar".** Advies: dit ontwerp eerst of tegelijk laten
  landen; `compose-parity` voorkomt uiteenlopen. Beide plannen wijzigen `deploy/Caddyfile` en
  het compose-bestand; wie als tweede komt rebaset. Verandert de volgorde, niet de bouw.
