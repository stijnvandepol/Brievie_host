# Wijzigingslog

Alle veranderingen in Brievie (de server) die je als beheerder merkt. De vorm volgt
[Keep a Changelog](https://keepachangelog.com/nl/1.1.0/); de versienummers volgen
[Semantic Versioning](https://semver.org/lang/nl/). Elke versie heeft een tag `vX.Y.Z`.
Lees bij elke versie eerst **Voor je bijwerkt**, als die er staat.

## [Onuitgebracht]

Installeren met alleen een compose-bestand.

### Voor je bijwerkt

- Een bestaande installatie met `./brievie` verandert niet. Het compose-bestand is een tweede
  manier om te installeren (docs/SELFHOST.md, sectie 24); beide gebruiken dezelfde volumes,
  start nooit allebei tegelijk. Verhuizen van de een naar de ander staat in dezelfde sectie.
- De server maakt elke nacht vanzelf een back-up zodra er een bestemming is ingesteld (ook bij
  `./brievie`), en een serverbeheerder ziet de stand onder Account, Back-up.
- Bij het bijwerken komt de tabel `updates_installedversion` erbij (de versie waar de database bij hoort).
- Een geweigerde of mislukte update zet Brievie uit tot je het oude versienummer terugzet: Compose
  heeft de oude diensten dan al gestopt. De gegevens blijven heel.
- `restore_install` maakt geen veiligheidsback-up van wat het vervangt: maak eerst zelf een back-up.
- Zet op een nieuwe machine de hoofdsleutel van je herstelblad in de kluis (`init_secrets --import`)
  vóór de eerste `up`; docs/SELFHOST.md sectie 24 zegt wat te doen als je al hebt gestart.
- Het herstelblad in de webinterface bestaat alleen bij een installatie met alleen een
  compose-bestand; bij `./brievie` maak je het met `./brievie recovery-sheet`.
- De geheimen van een back-upbestemming op afstand (S3-sleutel, REST-wachtwoord) zet je bij
  voorkeur als bestand (`..._FILE`, met `BRIEVIE_BACKUP_SECRETS_DIR`), niet als variabele.

### Toegevoegd

- Het compose-bestand (`deploy/compose/docker-compose.yml`, bij elke release als
  `docker-compose.yml` en als `docker-compose.pinned.yml` met de images op digest) en het
  image `brievie-proxy` (Caddy met onze Caddyfile).
- Sleutels worden bij de eerste start gemaakt en staan als bestanden in volumes (`X_FILE`
  naast elke geheime instelling), per dienst alleen wat die nodig heeft; PostgreSQL staat op een
  intern netwerk.
- Bij elke start controleert `init` of deze versie op deze gegevens mag starten (geen stap
  terug, geen te grote sprong, dezelfde PostgreSQL), maakt vóór een update de back-up
  `pre-update-<versie>`, zet de database tijdens de update op alleen-lezen en legt de versie vast.
- Herstelblad downloaden in de webinterface (serverbeheerder, na opnieuw inloggen), en
  `manage.py restore_install` om een back-up terug te zetten zonder `./brievie`.
- Back-up: elke nacht, een paneel met "Nu back-uppen", en `BRIEVIE_BACKUP_AUTO_INIT`.

## [0.4.0] - 2026-11-21

Wie ziet welke post, een prullenbak, dubbele brieven, "Mijn taken" en de webinterface ook in
het Engels.

### Voor je bijwerkt

- Werk bij met `./brievie update`. De API-sleutel van de AI hoef je niet opnieuw in te
  voeren; dat stond al bij 0.3.0.
- Een bestaande werkruimte verandert niet: iedereen ziet nog alle post, tot een beheerder
  dat onder **Instellingen → Wie ziet wat** uitzet. Een **nieuwe** werkruimte van het soort
  Organisatie start wel met "ieder zijn eigen post".
- Zodra leden privé-post hebben, kan een werkruimte-beheerder niet meer alles met hun
  account doen (een herstellink maken, sessies of apparaten beheren). Dat kan dan alleen de
  serverbeheerder, op de server.
- Een persoon die aan een lid is gekoppeld, maakt alleen dat lid zelf los. Een beheerder
  kan nog wel koppelen.
- Alleen beheerders veranderen afdelingen en personen met een account.
- Een tweede werkruimte maak je op de server met
  `manage.py create_workspace --name <naam> --kind home|business --admin-email <adres>`.

### Toegevoegd

- **Wie ziet wat.** Onder **Instellingen → Wie ziet wat** stel je in wie welke post ziet:
  iedereen alles (Thuis), of ieder zijn eigen post en die van zijn afdeling (Organisatie),
  beheerders die alle post lezen, en wie post zonder naam verdeelt. Elke wijziging laat
  eerst zien wie meer of minder gaat zien; wie meer gaat zien, bevestig je. Ook een
  rolwijziging, een uitnodiging (ook een nieuwe link) en iemand aan een afdeling toevoegen
  vragen dat als iemand er meer door ziet.
- **Afdelingen** (thuis: **Groepen**) met leden en leidinggevenden, labels van een
  afdeling, en in de **Inbox** knoppen om te filteren (Voor mij, je afdeling, Zonder naam).
- **Privé-brieven en delen.** Op een brief: **Privé maken**, **Delen met** en **Wie kan deze
  brief zien?**. Thuis maakt het label Zorg een brief vanzelf privé. Een regel kan een brief
  privé maken.
- **Noodtoegang.** Als de serverbeheerder het aanzet, kan een beheerder met een reden een
  uur lang verborgen post lezen, alleen lezen. Dat blijft in het logboek staan.
- **Wie kan mijn post zien?** Elk lid leest onder zijn account wie zijn post kan zien.
- **Mijn taken.** Elk lid krijgt een eigen takenlijst uit de post die hij mag lezen:
  te laat, binnen 7 dagen, later en zonder datum. Je vinkt taken af, vult een datum in of
  geeft een taak aan iemand die de brief mag zien. In een organisatie staat daaronder wat
  nog door een afdeling of door niemand is opgepakt.
- **Prullenbak voor brieven.** Met **Verwijderen** op een brief gaat die 30 dagen naar de
  **Prullenbak**. Daar kun je hem terugzetten. Na 30 dagen wist Brievie de brief voorgoed,
  ook de bestanden van de pagina's. Je ziet in de prullenbak alleen brieven die je ook
  zonder prullenbak mag zien.
- Een beheerder kan een brief meteen **definitief verwijderen** of de **prullenbak legen**.
  Dat kan niet ongedaan worden gemaakt. Een back-up die eerder is gemaakt, bevat de brief
  nog tot die back-up verloopt. Privépost van een ander blijft bij het legen staan en
  wordt na 30 dagen door Brievie zelf gewist.
- Het logboek noemt verplaatsen naar de prullenbak, terugzetten en definitief wissen.
  Brievie houdt nog geen bewaartermijn per brief bij (zie SELFHOST, deel 23).
- **Dubbele brieven.** Scant iemand een brief die er al is, dan zie je in de **Inbox**
  het blok **Dubbel?**. Op de pagina **Mogelijk dubbel** staan de twee brieven naast
  elkaar, met wat hetzelfde is en wat verschilt. Kies **Het is dezelfde brief**: de brief
  die je houdt krijgt de labels, personen en open taken van allebei, en de andere gaat naar
  de prullenbak (30 dagen terug te zetten). Of kies **Het zijn twee brieven**: dan vraagt
  Brievie het niet meer. Je ziet een paar alleen als je beide brieven mag zien. Brievie
  vergelijkt alleen nieuwe brieven met de post van de laatste 180 dagen; zolang een brief
  een open paar heeft, bevestigt de AI hem niet zelf.
- **Engels naast Nederlands.** De server en de webinterface zijn ook in het Engels. Elk
  lid kiest zijn taal onder zijn account; vóór het inloggen volgt Brievie de browser.
  Een beheerder kiest ook in welke taal de AI de samenvattingen schrijft (werkruimte-
  instellingen); Nederlands blijft de standaard.
- Na de installatie zie je **Zo is Brievie nu ingesteld**, met wat je daarna kunt doen.

### Gewijzigd

- **Te doen** heet nu **Inbox**. **Zakelijk** heet nu **Organisatie**, de rol meekijker heet
  nu **Alleen lezen**.

## [0.3.0] - 2026-11-20

Zoeken in je post, een autopilot die ook het label van een regel kan toepassen, en een
reeks beveiligingsverbeteringen.

### Voor je bijwerkt

- Werk bij met `./brievie update`. Doe daarna onderstaande, in elke werkruimte die het
  raakt.
- **Na het bijwerken: voer de API-sleutel van de AI één keer opnieuw in**, in elke
  werkruimte die een externe AI gebruikt (pagina **AI**). De opgeslagen sleutel hoort nu
  bij één werkruimte en één adres; een sleutel die zo niet opent, geldt als ontbrekend. Typ
  hem dus opnieuw in.
- **Na het bijwerken: tekst die vóór deze versie is opgeslagen, kan nog een BSN
  bevatten** in een van de schrijfwijzen die de oude maskering miste (zie Beveiliging).
  Alleen nieuwe tekst wordt met de sterkere maskering verwerkt. Opnieuw maskeren van
  bestaande tekst is een vervolgstap en zit niet in deze versie.
- **Stond de autopilot in een werkruimte aan?** Een beheerder moet die werkruimte dan
  akkoord laten gaan met de nieuwe tekst (zie Gewijzigd) voordat de autopilot het label van
  een regel toepast.

### Toegevoegd

- Zoeken in de webinterface (zoekbalk bovenin) en in de API: op tekst, afzender,
  samenvatting, labels en personen. Woorden worden over tekst en namen heen gecombineerd.
  Een BSN wordt nooit doorzocht, in welke vorm dan ook. Zoeken werkt op woordbegin; een
  samengesteld woord vind je niet op zijn tweede deel. De zoekopdracht wordt vergeten bij
  uitloggen en bij wisselen van werkruimte.
- De autopilot kan het label van een regel toepassen, als de regel steunt op de afzender of
  het briefhoofd. Het staat standaard uit, net als de rest van de autopilot. In de webinterface
  staat welke labels een regel heeft gezet, en er staat uitleg hoe een regel en de autopilot
  samen labels toepassen.
- Voorbereiding op "wie ziet welke brief": een nieuwe zichtbaarheidslaag in de database
  (extra beveiliging op rijniveau, bijgehouden lijsten van lezers en geadresseerden,
  schakelaars en afdelingen als velden van de werkruimte). Je merkt hier nog niets van:
  er verandert niets aan wie wat ziet.

### Gewijzigd

- De autopilot past een label van een regel alleen toe op vaststaande gegevens (de
  voorgestelde afzender of het geraden type van de AI tellen niet mee) en alleen als de
  beheerder akkoord is met de huidige tekst (versie 2). Een werkruimte die de autopilot aan
  had, meldt dat de tekst verouderd is, tot de beheerder opnieuw akkoord gaat.
- Voorstellen van een lokale AI noemen "local" als bron in plaats van de hostnaam.
- Een database-instelling zonder JIT-compilatie voor de verbindingen van de app, omdat
  die bij lijsten en zoeken langzamer was.

### Beveiliging

- AI-instellingen (modus, adres, sleutel, autopilot) zijn alleen te wijzigen vanuit een
  ingelogde browser; een apparaattoken krijgt een 401. Hetzelfde geldt voor het koppelen
  van een persoon aan een lid.
- De BSN-maskering herkent meer schrijfwijzen: meer scheidingstekens en 8-cijferige
  nummers met een label ervoor.
- Een koppelcode voor een telefoon vraagt opnieuw om je wachtwoord, en elke koppeling
  laat een melding achter.
- Foute wachtwoorden remmen de inlogpogingen af per account, met een begrensde wachttijd.
- Er zijn grenzen aan uploads per lid en per werkruimte, en een pagina-afbeelding met een
  tweede frame-kop wordt geweigerd.
- De sleutel van een externe AI is gebonden aan zijn werkruimte en zijn adres.
- Het aantal door een mens gemaakte personen en labels per werkruimte is begrensd.
- Een remmer houdt zijn staat per thread bij in plaats van gedeeld.

### Opgelost

- Verwijderen, archiveren en herhaald uploaden zien ook verborgen brieven.
- Regels bij bevestigen zien de bevestigde waarden; een herhaalde bevestiging geeft alleen
  de bevestiger een antwoord, en bevestigen voor iemand anders meldt `can_read` onwaar.
- Commando's van `./brievie` die zonder PostgreSQL draaien, doen geen systeemcontroles meer.

## [0.2.0] - 2026-11-19

De eerste versie met een nummer. Een installatie van vóór de versienummers meldt zich
als 0.1.0.

### Voor je bijwerkt

- Een installatie van vóór de versienummers werk je één keer met de hand bij, in de map
  van Brievie:

  ```sh
  git fetch --tags
  git checkout v0.2.0
  ./brievie start
  ```

  Daarna werkt `./brievie update` voor elke volgende versie.
- PostgreSQL is niet meer bereikbaar op poort 5432 van deze computer: Brievie zelf heeft
  die poort niet nodig, en op een NAS botst hij met een database die daar al draait.
  Ontwikkel je aan Brievie en gebruik je `make test` tegen deze installatie? Zet dan
  `BRIEVIE_PUBLISH_POSTGRES=true` in `deploy/.env` en typ `./brievie start`.

### Toegevoegd

- Back-up en herstel: `./brievie backup`, `./brievie restore`, `./brievie status`, met een
  back-up naar een schijf, een NAS-map, S3 of een REST-server, en een herstelblad om af te
  drukken.
- Versies: de server heeft een versienummer, dat `/api/v1/health` laat zien
  (`server_version`), en elke versie staat in dit wijzigingslog.
- `./brievie update`: werkt Brievie bij naar een nieuwere versie, met eerst een back-up en
  bij een fout de weg terug (`./brievie restore pre-update-<versie>`). Zonder
  back-upbestemming is die back-up een noodback-up op deze computer.
- `./brievie status` laat ook zien wanneer Brievie voor het laatst is bijgewerkt.
- De gezondheidscontrole (`/api/v1/health`) controleert nu ook de database.
