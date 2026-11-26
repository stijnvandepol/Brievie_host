# Wijzigingslog

Alle veranderingen in Brievie (de server) die je als beheerder merkt. De vorm volgt
[Keep a Changelog](https://keepachangelog.com/nl/1.1.0/); de versienummers volgen
[Semantic Versioning](https://semver.org/lang/nl/). Elke versie heeft een tag `vX.Y.Z`.
Lees bij elke versie eerst **Voor je bijwerkt**, als die er staat.

## [Onuitgebracht]

### Voor je bijwerkt

- **Brievie start geen tunnel meer.** Van buiten bereikbaar is nu je eigen keuze, buiten
  Brievie: een VPN (Tailscale), een eigen Cloudflare Tunnel (een eigen `cloudflared`-container)
  of je eigen reverse proxy. `BRIEVIE_TUNNEL_TOKEN`, `BRIEVIE_TUNNEL_TOKEN_FILE` en
  `COMPOSE_PROFILES=tunnel` doen niets meer. Staat een van de eerste twee nog in `deploy/.env`,
  dan zegt `./brievie start` dat in het Nederlands en start Brievie gewoon door; er wordt niets
  geweigerd en thuis blijft alles werken. Zet je eigen tunnel op (docs/SELFHOST.md, onderdeel 25),
  laat hem doorsturen naar de https-poort van de server, zet `BRIEVIE_PUBLIC_HOSTNAME` op zijn
  publieke naam en haal de tunnelregels uit `deploy/.env`.
- Verzoeken voor `BRIEVIE_PUBLIC_HOSTNAME` blijven publiek: alleen de app, de webinterface dicht
  tenzij `BRIEVIE_PUBLIC_WEB=true` (dan met tweestapsverificatie), koppelen alleen thuis.

### Gewijzigd

- De pagina Van buiten bereikbaar toont alleen nog wat is ingesteld (namen, vertrouwde adressen,
  webinterface aan of uit) en verwijst naar de uitleg op brievie.com.

## [0.5.0] - 2026-11-25

Mensen op één plek, een nieuw menu met Mijn post, een status voor elke brief,
tweestapsverificatie, van buiten bereikbaar, installeren met alleen een compose-bestand, het
Archief met eigen documenten, brieven toevoegen in de browser, en snellere lijsten.

### Voor je bijwerkt

- Werk bij met `./brievie update`, of (installatie met alleen een compose-bestand) zet
  `BRIEVIE_VERSION` op `0.5.0`. De API-sleutel van de AI hoef je niet opnieuw in te voeren.
- De webinterface krijgt een nieuw menu: Inbox, Mijn post, Mijn taken, Archief, Labels, Mensen,
  Afdelingen of Groepen, Automatisering en Prullenbak, met Instellingen onderaan. Personen en
  Leden zijn samen **Mensen** geworden; de oude adressen sturen door.
- Elke brief heeft nu een status, afgeleid van de controle en de taken; je zet hem niet zelf.
- Tweestapsverificatie staat voor niemand verplicht, tot een serverbeheerder het vraagt
  (`BRIEVIE_REQUIRE_SECOND_FACTOR`, standaard `none`). Van buiten inloggen kan alleen met
  tweestapsverificatie (zie hieronder).
- Brieven toevoegen in de browser leest de tekst op de server met Tesseract. Het image is
  daardoor 60 tot 80 MB groter. De ingebouwde tekstlezer voor uploads draait nog niet onder een
  eigen gebruiker en zonder netwerk; dat is een vervolgstap. Hij kan dus nog bestanden lezen
  die de werker ook kan lezen.
- Thuis verandert er niets. Van buiten bereikbaar stel je in met een paar regels in
  `deploy/.env` (docs/SELFHOST.md, sectie 25); een serverbeheerder ziet onder Account, Van buiten
  bereikbaar wat er is ingesteld. Via het publieke adres werkt standaard alleen de app.
- Met Cloudflare Tunnel kan Cloudflare alles lezen wat er langskomt; met een VPN niet. Een VPN is
  daarom het advies.
- `./brievie start` weigert een regel `COMPOSE_PROFILES` in `deploy/.env`: het start de tunnel zelf.
- Van buiten bereikbaar is niet getest met een echte tunnel, een echte reverse proxy of een echte
  VPN; alleen in CI met een nagebootste tunnel.
- Eigen reverse proxy: zet `BRIEVIE_TRUSTED_PROXIES` vóór de proxy van internet bereikbaar is, en
  laat de proxy de oorspronkelijke Host doorgeven (`proxy_set_header Host $host;` bij nginx,
  `ProxyPreserveHost On` bij Apache). Anders kan inloggen op de webinterface van buiten openstaan.
- Koppel de app alleen thuis of via VPN; via het publieke adres kan koppelen niet.
- Met Cloudflare Tunnel kan Cloudflare alles lezen wat er langskomt, ook gescande brieven.
- Met `BRIEVIE_PUBLIC_WEB=true` kan van buiten alleen inloggen wie tweestapsverificatie aan heeft.
  Wie het niet aan heeft, logt thuis of via VPN in en zet het daar aan (Account, Beveiliging).
  Ook installeren, een uitnodiging voor een nieuw account en een herstellink voor een account
  zonder tweestapsverificatie werken dan alleen thuis of via VPN.
- Installeren met alleen een compose-bestand is niet getest op Synology, Unraid of Portainer;
  alleen op gewone Docker met Compose. Docker Swarm en Podman-pods worden niet ondersteund.
- Deze update duurt wat langer om toe te passen: de zoekindex van alle brieven wordt opnieuw
  opgebouwd (bij duizenden brieven een paar minuten).
- "Alle post" heet nu "Archief" (`/w/<id>/archief`); de oude link werkt nog. Het Archief toont
  alleen gecontroleerde brieven: brieven die nog gecontroleerd moeten worden, staan in de Inbox.
- De export heeft versie 2: er zit een PDF per brief of document in, en de herkende tekst (met
  het BSN weggehaald) staat nu ook in het gegevensbestand.
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

- **Mensen.** Eén lijst van iedereen die post kan krijgen, met de stand van het account (Niet
  actief, Uitgenodigd, Actief, Uitgeschakeld). Beheerders nodigen uit, sturen een nieuwe link,
  veranderen een rol of schakelen iemand uit; bij uitschakelen waarschuwt Brievie als er
  privé-post op de naam staat en kun je kiezen wie die post beheert. Elke persoon heeft een
  eigen pagina. Staat er op een brief een naam die nog niet in Brievie staat, dan stelt Brievie
  voor die als persoon toe te voegen.
- **Nieuw menu en Mijn post.** Mijn post is de startpagina: de brieven voor jou, met ongelezen
  brieven vet gedrukt, en de knoppen "Alleen ongelezen" en "Alles gelezen". Op een brief kun je
  "Markeer als ongelezen" kiezen. Onder Instellingen staat wat vroeger bij Account, Apparaten en
  AI stond. Met meer dan één werkruimte wissel je bovenaan het menu.
- **Blijft liggen.** Boven de Inbox staan brieven die te lang wachten (2 dagen) en bevestigde
  brieven die geen geadresseerde heeft geopend (5 dagen), voor wie post verdeelt en voor
  beheerders. Een beheerder stelt het in onder Instellingen.
- **Taken.** "Ik doe dit" staat alleen bij een taak die nog niemand heeft. Een taak van een ander
  geef je alleen door als je die ander bent of beheerder.
- **Status van een brief.** Elke brief is Nieuw, Openstaand, Wacht op reactie of Afgehandeld,
  met de markering "Te laat". Je stelt de status niet zelf in: hij volgt uit de controle en de
  taken. Een taak kan wachten op een reactie (met een datum) of een tijdje worden uitgesteld.
  Met "Brief afhandelen" vink je de open taken in één keer af. In het Archief filter je op
  status, "Alleen te laat" en "Post van <naam>".
- **Tweestapsverificatie** met een authenticator-app. Je stelt het in onder Account, Beveiliging,
  met een QR-code en herstelcodes. Bij inloggen, na een uitnodiging en na een nieuw wachtwoord
  vraagt Brievie om de code. Een serverbeheerder kan het voor beheerders of voor iedereen
  verplicht stellen, met een aantal dagen om het aan te zetten, en kan een verloren code wissen.
  Koppelen van de app vraagt dan ook de code.
- **Brieven toevoegen in de browser.** "Brief toevoegen" op de pagina Archief neemt PDF's, JPEG's
  en PNG's (32 MB en 30 pagina's per bestand). De server leest de tekst zelf, met Tesseract
  (Nederlands en Engels), en de brief gaat naar de Inbox. Elke pagina wordt van haar plaatje
  gelezen; op een kleine NAS duurt dat een paar seconden per pagina.
- **Snellere lijsten.** De lijsten en tellers (Inbox, Mijn taken, menu, Archief per jaar) blijven
  ook bij 100.000 brieven ruim onder een kwart seconde, met nieuwe indexen in de database.

- Van buiten bereikbaar: een tweede naam voor een VPN op de server (`BRIEVIE_VPN_HOSTNAME`),
  Cloudflare Tunnel als optionele container (`BRIEVIE_TUNNEL_TOKEN` of `BRIEVIE_TUNNEL_TOKEN_FILE`)
  en een eigen reverse proxy (`BRIEVIE_TRUSTED_PROXIES`), met de publieke naam in
  `BRIEVIE_PUBLIC_HOSTNAME`. De pagina "Van buiten bereikbaar" voor serverbeheerders, en
  `getServerAddresses` plus `public_urls` bij het koppelen voor de app.
- Archief: zoeken en filteren op soort (brief of eigen document), type, van wie, afzender,
  jaar, label, status en "Loopt af".
- Eigen documenten (contract, diploma, paspoortkopie, garantiebewijs, polis …): via de app
  toe te voegen, met titel, type, datum, persoon, labels, "Geldig tot" en "Bewaren tot". Ze
  slaan de Inbox over, krijgen geen taken, regels of autopilot, en tellen nooit als ongelezen
  of "Blijft liggen". De AI doet nog niets met een eigen document.
- "Geldig tot" wordt een taak in Mijn taken, zoveel dagen vooraf als je kiest (7, 30, 60 of 90).
- "Bewaren tot": naar de prullenbak mag (met een vraag), definitief wissen pas na die datum.
- "Alles exporteren" voor een beheerder: alles wat die mag lezen, zonder privébrieven van
  anderen.
- Inloggen op de webinterface via het publieke adres vraagt altijd om tweestapsverificatie.
  Zonder tweestapsverificatie krijg je daar hetzelfde antwoord als bij een verkeerd wachtwoord;
  een sessie die thuis zonder code begon, telt van buiten niet. De pagina "Van buiten bereikbaar"
  zegt het, en waarschuwt als een serverbeheerder het nog niet aan heeft.

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

### Gewijzigd

- "Alle post" heet nu **Archief**; "Personen" en "Leden" zijn samen **Mensen**.
- Inloggen van buiten (via het publieke adres) vraagt altijd om tweestapsverificatie.

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
