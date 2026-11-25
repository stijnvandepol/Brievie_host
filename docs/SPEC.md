# Brievie: bouwprompt

Versie 3 · 13 november 2026 · twee repos (host + iOS), twee modi (Thuis / Zakelijk)

**Wat is nieuw in versie 3**

- Twee repos in plaats van één monorepo: `Brievie_host` en `Brievie_ios` (sectie 11).
- De techniekkeuze is opnieuw gemaakt na een brede vergelijking, met onderbouwing en tegenargumenten (sectie 18).
- Code guidelines voor leesbaarheid en robuustheid (sectie 19).
- De concurrentieanalyse is bijgewerkt: Paperless-ngx heeft sinds versie 3 ingebouwde AI (sectie 5).
- Zakelijke kansen en subsidies zijn toegevoegd als hypotheses voor fase 0 (sectie 20).
- Open beslissingen voor de eigenaar staan apart (sectie 21). Tot die genomen zijn, geldt wat in dit document staat.

Cijfers en versies komen uit onderzoek van 13 november 2026. Wat niet bevestigd kon worden, is gemarkeerd met *(niet geverifieerd)*. Controleer versienummers opnieuw bij de start van elke fase.

---

## 1. Gebruik en opdracht

Dit document is één complete prompt voor beide repos. De bron staat in `Brievie_host/docs/SPEC.md`. De iOS-repo bevat geen kopie, maar verwijst ernaar en legt de gebruikte API-versie vast (sectie 11).

**Rol:** Je bent een senior full-stack engineer en productontwerper. Je bouwt *Brievie*: een app waarmee een huishouden of een klein bedrijf al zijn post, facturen en administratie op één plek bewaart, begrijpt en bijhoudt. De software draait bij de gebruiker thuis of op kantoor (NAS, mini-pc, Proxmox), niet in onze cloud. Je werkt in fases (sectie 16). Lever per fase werkende code, migraties, tests en een README. Kies bij twijfel de eenvoudigste oplossing en noteer die in `docs/DECISIONS.md`.

**Harde regels:**

- De app betaalt, verstuurt of beantwoordt nooit zelf iets. AI stelt voor, de gebruiker bevestigt. Eén uitzondering, alleen als een beheerder die per werkruimte aanzet: de autopilot (sectie 10) zet brieven zelf bij personen en labels en bevestigt ze zelf. Hij is standaard uit, alles wat hij doet is gemarkeerd en terug te draaien, en hij betaalt, verstuurt of beantwoordt ook dan niets.
- Elke door AI gevonden datum of bedrag heeft een betrouwbaarheidsscore en een bron (de letterlijke tekst uit het document). Onder de drempel: *te controleren*.
- Documentinhoud verlaat de server van de gebruiker nooit, tenzij de gebruiker expliciet een externe AI-dienst aanzet.
- Alle UI-teksten in het Nederlands, taalniveau B1. Engels voorbereid (i18n vanaf dag 1).
- Eén codebase per repo, twee modi. Geen fork tussen Thuis en Zakelijk.
- De code volgt de guidelines uit sectie 19. CI is een harde poort.

---

## 2. Naam en positionering

**Naam:** Brievie (brief + -ie, zoals Tikkie). Werkwoord: "even in Brievie zetten".

**Tagline Thuis:** *De post van je huishouden en je ouders: begrijpen, verdelen, op tijd regelen.*
**Tagline Zakelijk:** *Facturen, bonnetjes en post. Netjes geordend, op je eigen server.*

**Positionering:** Brievie is de privé-postkamer die bij jou thuis draait. Scan een brief of factuur, en Brievie vertelt wat het is, voor wie, wat je moet doen en voor wanneer, en zet hem in de juiste map. Je documenten komen nooit bij ons.

Het onderscheid zit niet in "AI over je documenten". Dat is inmiddels gratis te krijgen (sectie 5). Het zit in drie dingen:

1. **Nederlandse handelingskennis:** wat moet ik doen, voor wanneer, wat is de bezwaartermijn.
2. **Gedeelde post:** één overzicht voor een huishouden of voor iemand die de post van zijn ouders bijhoudt.
3. **Gebruiksgemak:** een wizard, een echte app, koppelen met een QR-code.

**Naamstatus (13-11-2026):**

- Geen app met de naam Brievie gevonden in App Store of Google Play.
- brievie.nl is geregistreerd sinds 4 augustus 2025 (via Hostnet, met DNSSEC). Het wordt actief beheerd en is dus waarschijnlijk niet goedkoop over te nemen.
- brievie.com en brievie.eu zijn volgens whois vrij. Direct vastleggen. brievie.app kon niet worden vastgesteld.
- Nog doen: KVK/BOIP Naamchecker en TMview, daarna Benelux-merkdepot (± € 244, ± 3 maanden).

---

## 3. Twee modi: Thuis en Zakelijk

Bij de installatie kiest de gebruiker wat hij inricht. Eén Brievie-server kan meerdere **werkruimtes** hebben; elke werkruimte heeft een type. Zo kan een zzp'er een werkruimte "Thuis" én een werkruimte "Mijn bedrijf" naast elkaar hebben, met aparte leden en rechten.

| | Thuis | Zakelijk |
|---|---|---|
| Voor wie | Huishoudens, stellen, mensen die de post van hun ouders bijhouden | Zzp'ers, eenmanszaken, kleine bedrijven (1–10 mensen) |
| Wie zit erin | Leden + personen zonder account (kinderen, ouders) | Medewerkers + boekhouder/accountant als meekijker |
| Typische documenten | Belastingdienst, toeslagen, gemeente, zorg, verzekering, APK, energie | Inkoopfacturen, bonnetjes, contracten, offertes, post van KVK en Belastingdienst |
| Geld | Huishoudboekje: vaste lasten, wie betaalt wat, aangiftemap | Inkoopadministratie: btw-bedragen, btw-overzicht per kwartaal, export naar boekhoudpakket |
| Standaardlabels | Belastingdienst, Toeslagen, Gemeente, Zorg, Auto, Wonen, Energie, Verzekering | Inkoopfactuur, Bonnetje, Contract, Btw, Belastingdienst, KVK, Leverancier, Klant |
| Standaardmappen | Per persoon + per onderwerp | Per jaar / kwartaal / leverancier |
| Toon | Warm, je-vorm, uitleg in B1 | Zakelijk, kort, je-vorm |

**Regels voor de bouwer:**

- Het type bepaalt alleen **standaardinstellingen, labels, mappen, schermteksten en welke modules aan staan**. De onderliggende objecten (document, map, regel, taak, post) zijn hetzelfde.
- Modules zijn feature-flags per werkruimte: `household_budget`, `vat`, `bookkeeping_export`, `persons`, `relations`.
- Wisselen van type achteraf mag, met een duidelijke waarschuwing welke modules aan- of uitgaan. Er gaat nooit data verloren.

---

## 4. Probleem en bewijs

- Minstens 40% van de Nederlanders worstelt met de financiële administratie, omdat post via verschillende kanalen binnenkomt (Nibud via NOS).
- De Belastingdienst verstuurt nog ongeveer 125 miljoen brieven per jaar en stapt geleidelijk over op digitaal (Accountancy Vanmorgen, aug 2026; niet opnieuw gecontroleerd).
- Berichten in de Berichtenbox gelden als bezorgd, ook als je ze nooit opent; deadlines lopen door. Een e-mailnotificatie is een service en verandert daar niets aan.
- 2,5 miljoen Nederlanders van 16+ zijn laaggeletterd (Odido / stichting Lees Simpel).
- PostNL Mijn Post toont sinds een privacywijziging alleen nog post op je eigen naam, niet die van huisgenoten (iCulture, 2026).
- Meekijken in de Berichtenbox van een ander kan alleen per persoon, alleen-lezen, via DigiD-machtigen. Wie de post van een ouder bijhoudt, heeft daardoor geen gedeeld overzicht met taken.
- Zakelijk: zzp'ers en kleine ondernemers krijgen facturen via mail, papier en portalen door elkaar; bonnetjes raken kwijt; kwartaalaangifte btw wordt een zoektocht.

---

## 5. Concurrentie (eerlijk)

**Belangrijkste vergelijking voor self-hosted: Paperless-ngx.** Open source, gratis, volwassen. Heeft OCR, auto-tagging, gebruikers en groepen, en workflows. **Sinds versie 3.0 (juli 2026) heeft het ook ingebouwde AI:** suggesties voor titel, tags, correspondent en documenttype, en een chat over je documenten, volgens secundaire bronnen ook lokaal via Ollama. "Begrijpen" is dus geen uniek punt meer.

Brievie moet winnen op wat Paperless-ngx níet doet:

1. **Handelen in plaats van archiveren:** "wat moet je doen en voor wanneer", deadlines met bron en herinneringen, berekende termijnen.
2. **Nederlandse kennis:** afzenders (Belastingdienst, CJIB, CAK, toeslagen, RDW, KVK), bezwaartermijnen, btw-velden op Nederlandse facturen.
3. **Gedeelde post:** personen zonder account, "op naam van", meekijkers, privé-documenten.
4. **Geld aan documenten gekoppeld:** huishoudboekje (Thuis) en btw/inkoop-overzicht (Zakelijk).
5. **Installatie en app voor gewone mensen.**

Overige spelers:

| Wie | Wat | Verschil met Brievie |
|---|---|---|
| Papra | AGPL-documentarchief met eigen cloud (± $ 7,50–9 / mnd) | Generiek, geen NL-kennis, geen taken |
| Docspell | Self-hosted archief | Technisch, geen uitleg of deadlines |
| Doccle (BE, breidt uit in NL) | Documenten rechtstreeks van bedrijven ontvangen, betalen, cloud | Cloud, weinig NL-afzenders, geen AI-uitleg |
| Docutain, fileee (DE) | Scanner + archief + termijnen | Duitse fiscale logica, cloud of alleen lokaal op telefoon |
| Lees Simpel (NL) | Brief → begrijpelijke taal, gratis, 38.000 gebruikers (eigen opgave), gebruikt OpenAI | Geen archief, deadlines of geld; inhoud gaat naar een Amerikaanse dienst |
| WelkomNL, ExplainThis | Brief uitgelegd voor nieuwkomers | Geen archief of taken |
| SameNest | Gedeelde uitgaven, boodschappen, documenten voor stellen/huisgenoten | Cloud, geen post-intelligentie |
| Jortt, Moneybird (zakelijk) | Boekhouding met AI-bonscan, eigen mailinbox en Peppol | Brievie is geen boekhoudpakket. Zzp'ers hebben zo'n pakket al; Brievie levert er schoon aan |

**Gevolg voor Zakelijk:** de boekhoudpakketten doen de factuurherkenning zelf steeds beter. De Zakelijk-modus moet daarom klein blijven en vooral doorsturen (sectie 21, beslissing 2).

---

## 6. Doelgroep (start)

Begin bij mensen die al een NAS of homeserver hebben of er makkelijk een neerzetten:

- **Techy huishoudens** (Synology/Unraid/Proxmox-eigenaren) die Paperless te technisch of te kaal vinden.
- **De "regelaar" in de familie** die ook de post van ouders bijhoudt (Thuis-werkruimte voor de ouders, zelf meekijker).
- **Zzp'ers en kleine bedrijven** die hun administratie niet bij een Amerikaanse cloud willen.

**Spanning om eerlijk over te zijn:** wie uitleg op B1-niveau het hardst nodig heeft (ouderen, laaggeletterden, expats, cliënten van bewindvoerders), heeft geen NAS. De techy regelaar is de brug: hij installeert Brievie en gebruikt het voor mensen die het zelf niet kunnen. Of dat genoeg is, of dat er een gehoste variant naast moet komen, is beslissing 1 in sectie 21. De architectuur houdt die weg open (sectie 11).

---

## 7. Kernconcepten

- **Server:** één Brievie-installatie. Heeft een beheerder.
- **Werkruimte:** type `thuis` of `zakelijk`. Alles hoort bij precies één werkruimte.
- **Lid:** gebruiker met account in een werkruimte. Rollen: `beheerder`, `lid`, `meekijker` (alleen lezen + notities; bijv. boekhouder of zoon die ouders helpt).
- **Persoon** (Thuis): iemand zonder account op wiens naam post komt.
- **Relatie** (Zakelijk): leverancier of klant, met KVK-nummer, btw-nummer en standaardcategorie.
- **Document:** 1..n pagina's, met *op naam van* (Thuis) of *relatie* (Zakelijk), *gescand door*, *zichtbaarheid*, *afzender*, *type*, *datum*, *samenvatting*, *labels*, *map*.
- **Map:** hiërarchisch (`/2026/Q4/Inkoop`, `/Auto/APK`). Een document zit in precies één map en kan meerdere labels hebben.
- **Regel:** als-dan-automatisering (sectie 9).
- **Taak:** iets dat vóór een datum moet: `betalen`, `reageren`, `bezwaar`, `aanvragen`, `keuring`, `opzeggen`, `verlengen`, `aangifte`, `overig`. Bron van de deadline: `in document`, `berekend`, `handmatig`.
- **Post (geld):** bedrag gekoppeld aan een document. Zakelijk met extra velden: bedrag excl. btw, btw-bedrag, btw-tarief, factuurnummer, vervaldatum.

**Voorbeelden die moeten werken:**

- *Thuis:* APK-brief op naam van partner → label Auto, map `/Auto`, taak `keuring` met vervaldatum, kenteken herkend.
- *Thuis:* brief CAK voor moeder, gescand door zoon → op naam van moeder, zichtbaar voor moeder en zoon.
- *Thuis:* e-mailnotificatie van de Berichtenbox → taak "open je Berichtenbox, bericht van X", zonder document tot de gebruiker de PDF zelf toevoegt.
- *Zakelijk:* factuur van leverancier per mail → relatie herkend, btw 21% uitgesplitst, map `/2026/Q4/Inkoop`, taak `betalen` op vervaldatum.
- *Zakelijk:* voorlopige aanslag of btw-herinnering → label Belastingdienst, taak `aangifte`/`betalen`.

---

## 8. Functies

### Kern (beide modi)

1. **Installatiewizard:** werkruimte aanmaken, type kiezen (Thuis/Zakelijk), eerste beheerder, AI-instelling (lokaal / extern / uit), opslaglocatie.
2. **Invoer:**
   - Scannen met de app (meerdere pagina's, randdetectie). De app stuurt de op het toestel herkende tekst mee.
   - Delen vanuit andere apps (PDF uit Berichtenbox, mail, WhatsApp).
   - Webupload (slepen) en een **watch-map** op de NAS (scanner schrijft naar `/brievie/inbox`).
   - Eigen IMAP-mailbox laten uitlezen. Later: mailadres per werkruimte via Brievie Connect.
   - **Berichtenbox-notificaties:** de notificatiemails van MijnOverheid in de IMAP-mailbox herkennen en er een taak van maken. Er bestaat geen API voor burgers; de gebruiker haalt de PDF zelf op en deelt hem met de app.
3. **AI-verwerking** met controlescherm (sectie 10). Elk AI-resultaat is herkenbaar als AI-uitleg (sectie 14).
4. **Uitlegtaal per lid:** de samenvatting en "wat moet je doen" in het Nederlands (B1) of in het Engels. Het document zelf blijft ongewijzigd.
5. **Inbox:** nieuw, te controleren, zonder eigenaar.
6. **Mappen, labels en regels** (sectie 9), beheerd via de webinterface.
7. **Taken en deadlines:** tijdlijn, toewijzen, meldingen (7 dagen, 2 dagen, op de dag; instelbaar), ICS-feed per lid.
8. **Zoeken:** in de tekst, afzender, samenvatting, labels en personen van de eigen werkruimte, met filters (zie "Zoeken in post" hieronder). Nog niet: filters op map en bedrag.
9. **Export:** alles als ZIP (PDF's + JSON/CSV).
10. **Prullenbak** (Thuis en Zakelijk): een lid (niet een meekijker) zet een brief in de prullenbak en kan hem 30 dagen terugzetten; daarna wist Brievie hem voorgoed, met de gescande pagina's. Een beheerder kan eerder definitief verwijderen of de prullenbak legen. Een back-up die eerder is gemaakt, houdt de brief tot die back-up verloopt. Een bewaartermijn per brief (zie Zakelijk) komt later.

### Zoeken in post

Eén zoekveld in de balk van de webinterface (`/` zet de cursor erin) en één eindpunt, `POST searchDocuments`; de zoekterm staat in de body, nooit in een adres. Alleen binnen de eigen werkruimte en alleen post die het lid mag zien.

- **Wat er doorzocht wordt:** de (BSN-gemaskeerde) tekst van de brief, de afzender, de samenvatting, de namen van de labels en de namen en naamvarianten van de personen. Elk woord moet gevonden worden, maar niet op dezelfde plek: in de afzender, de samenvatting of de tekst, of in de naam van een label of persoon van de brief ("aanslag papa" vindt de aanslag die voor Papa is). Het begin van een woord is genoeg ("belast" vindt "belastingdienst"; "dienst" vindt "belastingdienst" niet) en Nederlandse woordvormen tellen mee ("betaling" vindt "betalen"). Bij labels en personen is elk deel van de naam genoeg.
- **Volgorde:** eerst de beste treffer (meer gevonden woorden weegt zwaarder; afzender zwaarder dan samenvatting, die zwaarder dan de tekst; elk woord in een naam van een label of persoon telt mee), dan de nieuwste.
- **Typefouten:** alleen als er niets exact gevonden is, zoekt Brievie op gelijkende woorden in de afzender en de samenvatting. Het antwoord zegt dan "Geen exacte treffer. Dit lijkt erop." Een typefout in een woord dat alleen in de tekst van de brief staat vindt niets.
- **Filters:** soort brief, label, persoon, status (in de web: controleren of naam kiezen; de API kent ook nieuw) en periode op dagtekening (zonder dagtekening geldt de uploaddatum). Ze beperken altijd; samen gelden ze allemaal.
- **Fragment:** bij elke treffer een kort stuk tekst (hoogstens 240 tekens) met de gevonden woorden gemarkeerd; platte tekst, nooit HTML.
- **Grenzen:** hoogstens 8 woorden, 200 tekens, minstens 2 tekens; resultaten tot 1000 diep (bladeren stopt daar); een zeer algemeen woord rangschikt de nieuwste 1000 treffers, niet alle; 30 zoekopdrachten per minuut.
- **Een BSN is niet te zoeken:** een BSN (ook met andere scheidingstekens, zoals een schuine streep of komma), het woord `[BSN]` zelf en de woorden "bsn" en "burgerservicenummer" worden uit de zoekterm gehaald; het antwoord en de pagina zeggen dat.
- Brievie zet de zoekterm niet in zijn logboek, auditlog of een foutmelding. PostgreSQL logt wel een opdracht die mislukt (standaard), en daarin staat dan de zoekterm; dat geldt al voor elke mislukte opdracht met brieftekst. De webinterface houdt de term alleen in het geheugen en vergeet hem bij uitloggen of een andere werkruimte. Zoeken in de app volgt apart.

### Thuis

- Personen zonder account, "op naam van" met naammatching.
- Privé-documenten (zorg standaard alleen voor de persoon zelf).
- Huishoudboekje: vaste lasten, maandoverzicht, wie-betaalt-wat.
- Aangiftemap per belastingjaar (jaaropgaven, WOZ, hypotheek, zorg, giften).
- Bankafschrift-import (CSV/CAMT.053) om betalingen af te vinken.

### Zakelijk

- Relaties (leveranciers/klanten) met automatische herkenning.
- Factuurvelden: factuurnummer, datum, vervaldatum, bedrag excl./incl., btw per tarief, IBAN.
- E-facturen inlezen als bestand (UBL-XML) naast PDF. Geen eigen Peppol-ontvangst: dat loopt altijd via een betaald access point en zit al in boekhoudpakketten.
- Btw-overzicht per kwartaal (voorbelasting) als hulp bij de aangifte, met duidelijke melding dat het geen aangifte is.
- Doorsturen naar het boekhoudpakket: naar het inbox-mailadres van Moneybird, Jortt en vergelijkbare pakketten, en export als CSV.
- Bewaartermijn per document (fiscale bewaarplicht; standaardwaarde laten controleren door een boekhouder) met waarschuwing vóór verwijderen. Komt later; de prullenbak vraagt het nu al aan één haakje (`retention.check_removal`) voordat een brief erin gaat of gewist wordt.
- Meekijker-rol voor boekhouder, eventueel beperkt tot bepaalde mappen.

### Later

Conceptbrieven (reactie/bezwaar/opzegging), import uit Paperless-ngx, gehoste variant, kant-en-klaar kastje, directe koppelingen met boekhoudpakketten, semantisch zoeken.

---

## 9. Mappen en regels-engine

Regels worden van boven naar beneden uitgevoerd op elk nieuw of gewijzigd document. Eerste match met `stop: true` beëindigt de verwerking.

**Voorwaarden** (AND/OR combineerbaar): afzender/relatie, documenttype, label, tekst bevat, bedrag (>, <, tussen), op naam van, datumbereik, bron (scan/mail/watch-map), AI-zekerheid.

**Acties:** naar map verplaatsen, label toevoegen/verwijderen, op naam zetten, zichtbaarheid wijzigen, taak aanmaken (type + deadline-regel), toewijzen aan lid, post aanmaken met categorie, melding sturen.

**Mapsjablonen** met variabelen: `/{jaar}/{kwartaal}/Inkoop/{relatie}`, `/{persoon}/{label}`.

**Voorbeelden:**

```yaml
- naam: Facturen naar kwartaalmap
  als: { type: factuur }
  dan:
    - map: "/{jaar}/{kwartaal}/Inkoop/{relatie}"
    - label: Inkoopfactuur
    - taak: { type: betalen, deadline: "{vervaldatum}" }

- naam: Zorg altijd privé
  als: { label: Zorg }
  dan:
    - zichtbaarheid: alleen_op_naam_van

- naam: Grote bedragen melden
  als: { bedrag: { groter_dan: 500 } }
  dan:
    - melding: { aan: beheerder }
```

**UI:** regel-bouwer in de webinterface (geen YAML nodig voor gebruikers), met een "test op bestaande documenten"-knop die laat zien welke documenten zouden matchen. Elke wijziging door een regel komt in de geschiedenis van het document.

**Leren:** als een gebruiker drie keer dezelfde correctie doet (bijv. afzender X → map Y), stelt Brievie een regel voor.

**Bouwregel:** de engine is een pure functie: document + regels erin, lijst met acties eruit. Het uitvoeren van die acties is een aparte stap. Zo is "test op bestaande documenten" dezelfde code als de echte uitvoering, alleen zonder de laatste stap.

---

## 10. Scan- en AI-pipeline

Alles draait standaard op de server van de gebruiker.

1. **Vastleggen:** documentscanner van iOS in de app (VisionKit), of PDF/afbeelding via upload, mail of watch-map.
2. **OCR:**
   - In de app: Apple Vision op het toestel. De herkende tekst gaat mee met de upload, zodat de server voor scans geen OCR hoeft te doen. Dat scheelt het meeste rekenwerk op een lichte NAS.
   - Op de server, voor upload, mail en watch-map: OCRmyPDF met Tesseract (Nederlands). Optioneel per hardware-profiel: PaddleOCR (*Normaal*) of granite-docling (*Sterk*).
3. **E-factuur:** is er UBL-XML, dan die gebruiken in plaats van OCR + AI.
4. **Privacy-voorfilter:** BSN herkennen (9 cijfers + elfproef) en maskeren; IBAN/kenmerken apart versleuteld opslaan.
5. **Extractie** via een `LLMProvider`-interface die de OpenAI-compatibele API spreekt. Daardoor zijn Ollama, llama.cpp en EU-gehoste diensten onderling uitwisselbaar zonder extra code.
   - `local`: Ollama op de eigen server (klein model; werkt goed op een mini-pc met voldoende RAM, traag op een instap-NAS).
   - `external`: een EU-gehoste dienst zonder opslag of training, alleen de gemaskeerde tekst. Eerst met een eigen sleutel van de gebruiker, later via Brievie Connect AI.
   - `off`: alleen OCR + regels, geen AI.
   Temperatuur 0, strikt JSON-schema, uitvoer gevalideerd met Pydantic.
6. **Validatie** (deterministisch): elke waarde moet letterlijk terug te vinden zijn in de tekst (anders zekerheid 0); datums plausibel; bedragen als decimaal; IBAN mod-97; btw-rekensom klopt (excl. + btw = incl.).
7. **Nederlandse regels** (`rules/nl/*.yaml`, versie + tests + bronvermelding): afzenderherkenning, berekende termijnen (bijv. bezwaar 6 weken na dagtekening, Awb art. 6:7, status `berekend`). Geen regel live zonder menselijke controle.
8. **Koppelen:** persoon (Thuis) of relatie (Zakelijk) matchen.
9. **Regels-engine** draaien (sectie 9).
10. **Resultaat:** samenvatting B1, "wat moet je doen", taken, posten, map, labels.

**Autopilot (opt-in per werkruimte).** Standaard blijft het resultaat een voorstel: de gebruiker controleert en bevestigt. Een beheerder kan twee schakelaars aanzetten, pas nadat hij de waarschuwing heeft gelezen en akkoord heeft gegeven; elke wijziging staat in het logboek. Ze kunnen alleen aan terwijl AI aan staat en de serverbeheerder het toestaat (`BRIEVIE_AI_AUTOPILOT_ALLOWED`, standaard waar). Gaat AI uit, wisselt de modus (extern of lokaal) of komt er toestemming voor een ander extern adres, dan gaan beide schakelaars uit (met een regel in het logboek). Een ander model in dezelfde modus op hetzelfde adres laat ze aan. De autopilot werkt alleen op een document dat nog `processing` is, in dezelfde transactie als de stappen hierboven; er gaat geen extra tekst naar de AI-dienst.

- **(a) Zelf organiseren** (`auto_organise`): een nog open voorstel voor een persoon of label wordt uitgevoerd als het van de AI komt met een zekerheid van 0,800 of hoger (het model zelf, de bovengrens), of een naamherkenning is met een sterke match (de bestaande voorselectie). Een voorstel van een regel om een brief op naam te zetten wordt nooit automatisch uitgevoerd: de tekst van de brief kan zo'n regel hebben gestuurd. Een label dat een regel voorstelt wel, als (1) de regel klopt op vaststaande feiten alleen: het briefhoofd (het begin van de tekst tot de eerste lege regel of de aanhef, hoogstens 8 regels en 400 tekens), een afzender of soort die een persoon bevestigde, en labels en personen die al op de brief staan; niet op een open voorstel, de gok van de AI of een woord verderop in de brief, en met minstens één voorwaarde die iets vindt (alleen "bevat niet" telt niet); of (2) de AI zelf hetzelfde label kiest met een zekerheid van 0,800 of hoger. Niet als de regels het oneens zijn (een andere regel die bij de brief past, stelde een label voor dat hier is afgewezen), als meer dan 3 labels van regels in aanmerking komen, of als de beheerder alleen akkoord gaf op de uitleg van vóór deze regel (versie 1; hij krijgt de nieuwe uitleg te zien). Zo'n label krijgt in de geschiedenis de oorsprong `rule_autopilot`, het kenmerk "Automatisch door regel", en is terug te draaien tot een persoon de indeling bevestigt. Een regel op "Automatisch toepassen" voert altijd zelf uit, ook zonder autopilot. Naamherkenning wordt toegepast als naamherkenning (geschiedenis: `name_match`), niet als AI. Een keuze van de AI voor een persoon wordt alleen uitgevoerd als het citaat bij die keuze de naam van die persoon bevat (weergavenaam of een andere schrijfwijze, zonder verschil in hoofdletters of spaties); anders blijft het een voorstel. Een gebruiker die iets terugdraait krijgt dat niet opnieuw voorgesteld. Mislukt het voorstellen (naamherkenning, AI-keuzes, regels) of het uitvoeren, dan gebeurt er niets automatisch, ook geen bevestiging, en blijft het document ter controle.
- **(b) Zelf bevestigen** (`auto_confirm`): het document wordt direct bevestigd (afzender, type, dagtekening, samenvatting, taken met deadline), maar alleen als een zuivere beslisfunctie elke vangrail goed vindt. Anders blijft het een voorstel. De vangrails gebruiken alleen echte signalen: de zekerheid van het model bij waarden met een bron (afzender, dagtekening, deadline; minimaal 0,850) en de deterministische controles (bron staat letterlijk in de tekst, datum plausibel). Gegenereerde velden (type, samenvatting, omschrijving) hebben geen bron en geen echte zekerheid; daar leunt geen vangrail op. Het blijft een voorstel bij: geen AI-antwoord; afzender of dagtekening ontbreekt, staat niet in de brief of heeft zekerheid onder 0,850; een dagtekening na vandaag; een onbekend documenttype of actietype; een actie zonder omschrijving of meer dan 20 acties; een deadline die niet in de brief staat, onzeker is, in het verleden ligt of voor de dagtekening; een brief die een termijn noemt ("binnen 14 dagen", "binnen veertien (14) dagen", "zes weken na de dagtekening", "uiterlijk", "vóór 1 december", "tot 1 december", "vervaldag", "within 14 days") terwijl er geen acties zijn of een actie geen deadline heeft; een factuur met vervaldatum zonder actie betalen, of met een actie betalen zonder deadline; een mislukte stap voorstellen; en, als (a) ook aan staat, een voorstel voor een persoon dat na (a) nog open is (dan is niet duidelijk van wie de brief is) of een mislukte (a)-stap. Een voorstel voor een label dat open blijft, bijvoorbeeld van een regel die vraagt, houdt het bevestigen niet tegen: het blijft als voorstel bij de bevestigde brief staan. Een bevestiging die de eigen controles van bevestigen niet haalt (bijvoorbeeld een bron die niet letterlijk in de tekst staat), blijft ook een voorstel.
- **Wat er gemarkeerd wordt.** Geen verzonnen actor: bij wat de autopilot doet is er geen gebruiker. Het document draagt `confirmed_by_ai`, een taak `created_by_ai`, een label of persoon `automatic`, de geschiedenis heeft herkomst `ai` of `name_match`. In de lijst staat het kenmerk "Automatisch door AI" en er is een filter. Een gebruiker kan een automatisch label of persoon terugdraaien en een bevestigd document opnieuw laten controleren ("Zelf controleren": velden leeg, open AI-taken weg, een afgevinkte taak blijft). Een persoon wint altijd: wat een gebruiker al bevestigde of aanpast is geen `processing` meer. Bevestigt een persoon de brief met de personen en labels (`sets_organisation`), dan is elk label en elke persoon die blijft van die persoon: `automatic` gaat uit en de automatische toevoeging is niet meer terug te draaien. Bewerken met de hand laat het kenmerk staan. Een review van een persoon op een brief die de AI bevestigde is nooit een herhaling: eerst "Zelf controleren".
- **Grenzen, eerlijk.** Een overbodige taak (bijvoorbeeld "bewaar" bij een loonstrook) is niet te herkennen en wordt dus bevestigd. Een relatieve termijn ("binnen 14 dagen na dagtekening") wordt niet uitgerekend: zo'n brief blijft een voorstel. Een naamherkenning kan een verkeerde persoon zijn; ze wordt toegepast zoals ze is. De vangrails controleren dat wat de AI invult letterlijk in de brief staat, niet dat de brief klopt: een brief die fout is of misleidt, levert een verkeerde afzender, datum of taak op die toch bevestigd wordt. Een regel in stand `auto` is de eigen keuze van de gebruiker en blijft zoals hij was. De autopilot is een gemak met een bekend risico, geen controle.

**Modelkeuze:** er is geen actuele benchmark voor Nederlandse extractie gevonden. Kies het standaardmodel per hardware-profiel daarom op de eigen testset uit fase 0. Kandidaten om te testen: kleine Qwen-modellen (Apache), Ministral 3B/8B (Apache, Europees bedrijf), Gemma (eigen licentie van Google, geen open-source-licentie: melden in de wizard). Leg de uitkomst vast in `docs/DECISIONS.md`.

**JSON-schema (kern):**

```json
{
  "afzender": {"naam": "string", "kvk": "string|null", "btw_nummer": "string|null", "bron": "string", "zekerheid": 0.0},
  "documenttype": "factuur|aanslag|beschikking|rekening|herinnering|aanmaning|polis|jaaropgave|loonstrook|keuring|contract|bonnetje|informatie|overig",
  "dagtekening": {"waarde": "YYYY-MM-DD", "bron": "string", "zekerheid": 0.0},
  "op_naam_van": [{"naam": "string", "bron": "string"}],
  "samenvatting_b1": "string",
  "acties": [{"type": "betalen|reageren|bezwaar|aanvragen|keuring|opzeggen|verlengen|aangifte|overig", "omschrijving": "string", "deadline": {"waarde": "YYYY-MM-DD|null", "bron": "string|null", "zekerheid": 0.0}}],
  "factuur": {
    "nummer": "string|null", "vervaldatum": "YYYY-MM-DD|null",
    "bedrag_excl": "decimal|null", "bedrag_incl": "decimal|null",
    "btw": [{"tarief": "21|9|0|verlegd", "bedrag": "decimal"}],
    "bron": "string", "zekerheid": 0.0
  },
  "kenmerken": {"kenteken": "string|null", "aanslagnummer": "string|null", "belastingjaar": "int|null", "polisnummer": "string|null"},
  "labels_voorstel": ["string"]
}
```

De veldnamen in dit schema blijven Nederlands: dat werkt beter op Nederlandse brieven. Direct na de validatie worden ze omgezet naar de Engelse namen uit de code (sectie 19).

**Drempel:** onder 0,85 zekerheid op een deadline of bedrag wordt het veld oranje en komt er geen automatische herinnering.

---

## 11. Architectuur: twee repos

| Repo | Inhoud | Licentie |
|---|---|---|
| `Brievie_host` | Server: API, worker, webinterface, NL-regels, installatie, documentatie | AGPL |
| `Brievie_ios` | iOS-app, deelmenu-extensie, widget | AGPL, met CLA voor bijdragers (sectie 14) |
| `Brievie_connect` (later, privé) | Push-relay, tunnel, mail, AI-gateway | Gesloten |

**Het contract tussen de repos is het OpenAPI-schema van de host.**

- De host genereert `docs/api/openapi.json` uit de code. CI faalt als het bestand niet overeenkomt met de code.
- De API heeft een versie in het pad (`/api/v1`). Binnen een versie alleen toevoegen, nooit iets weghalen of van betekenis veranderen.
- De iOS-repo haalt het schema op met één commando (`make sync-api`), legt de versie vast en genereert er zijn client uit. Er wordt nooit met de hand een API-type geschreven.
- De server meldt zijn API-versie; de app toont een nette melding als de server te oud of te nieuw is.

### `Brievie_host`: wat er draait (Docker Compose)

| Container | Rol |
|---|---|
| `brievie-api` | Django + Django Ninja. Bevat ook de gebouwde webinterface als statische bestanden |
| `brievie-worker` | Dezelfde image, ander startcommando: OCR, AI, regels, herinneringen, mail-inname |
| `postgres` | PostgreSQL met row-level security per werkruimte. Is ook de wachtrij |
| `caddy` | HTTPS en het uitserveren van de webinterface |
| `ollama` (optioneel) | Lokaal taalmodel |
| `cloudflared` (optioneel) | Toegang van buitenaf |
| Opslag | Documenten op een volume (NAS-share), versleuteld per werkruimte |

Er is geen Redis en geen Node-container. De onderbouwing staat in sectie 18.

**Installeren met alleen een compose-bestand (2026-11-23):** naast `./brievie` kan Brievie met één bestand (`deploy/compose/docker-compose.yml`, release-asset) worden geïnstalleerd en bijgewerkt in Portainer, Synology Container Manager, Dockge of met `docker compose up -d`, zonder checkout of script. De sleutels worden bij de eerste start gemaakt en staan als bestanden in volumes (per dienst alleen wat die nodig heeft); de eerste container, `init`, bewaakt versies en data (geen stap terug, geen te grote sprong, dezelfde PostgreSQL), maakt vóór een update de back-up en zet de database tijdens de update op alleen-lezen. Herstelblad, nachtelijke back-up en back-upstatus staan in de webinterface. Zie `docs/SELFHOST.md` sectie 24 en de besluiten van 2026-11-23 in `docs/DECISIONS.md`.

**Installatiedoelen:** Synology Container Manager en gewone Linux + Docker in fase 1. Daarna Unraid, Proxmox LXC en Raspberry Pi 5 (zonder lokale AI). Unraid verwacht één container; daarvoor komt er in fase 4 een alles-in-één image naast de Compose-opzet. Alle images zijn multi-arch (amd64 en arm64).

**Hardware-profielen in de wizard:**

- *Licht* (instap-NAS, Pi): OCR lokaal, AI extern of uit.
- *Normaal* (mini-pc, 16 GB RAM): OCR + klein lokaal model.
- *Sterk* (server met GPU): alles lokaal, ook grotere modellen.

**Klaar voor een gehoste variant.** Werkruimtes zijn al volledig gescheiden. Bouw daarom niets dat aanneemt dat er maar één huishouden op een server zit: geen globale instellingen die per werkruimte horen, opslag achter een interface (schijf nu, S3-compatibel later), geen paden naar de NAS in de code. Een gehoste variant is dan een andere manier van uitrollen, geen andere codebase.

**Structuur van de repo:**

```
Brievie_host/
  backend/
    brievie/              # Django-project: settings, urls
    apps/                 # één map per domein
      workspaces/         # werkruimtes, leden, rollen, RLS
      documents/          # documenten, pagina's, mappen, labels
      pipeline/           # OCR, privacy-filter, extractie, validatie
      rules/              # regels-engine
      tasks/              # taken, deadlines, herinneringen
      ledger/             # posten, btw, bankimport
      intake/             # upload, watch-map, IMAP
      devices/            # app koppelen, tokens
    tests/
  web/                    # React + Vite
  rules/nl/               # NL-regels (YAML) + tests
  deploy/                 # docker-compose, Caddyfile, platform-templates
  docs/                   # SPEC.md, DECISIONS.md, PRIVACY.md, SELFHOST.md, GLOSSARY.md, api/
```

### `Brievie_ios`: de app

Native Swift en SwiftUI, alleen iOS. Android komt later als eigen app op dezelfde API. Houd de app daarom dun: de logica zit op de server.

- **Koppelen:** QR-code scannen in de webinterface (server-URL + eenmalige koppelcode + certificaat-pin). Daarnaast automatisch vinden op het thuisnetwerk via Bonjour (`_brievie._tcp`), met de iOS-toestemming voor lokaal netwerk (`NSLocalNetworkUsageDescription`).
- **HTTPS verplicht.** De server maakt bij installatie een eigen certificaatautoriteit met lange looptijd. **De app pint het certificaat van die autoriteit, niet het losse servercertificaat.** Caddy vernieuwt het servercertificaat vaak; pinnen op dat certificaat zou de koppeling steeds breken. De QR-code bevat de SHA-256-vingerafdruk van het autoriteitscertificaat. De app haalt het certificaat bij het koppelen op, controleert de vingerafdruk en vertrouwt daarna alleen nog die autoriteit. Met een eigen domein of tunnel: gewoon Let's Encrypt.
- **Scannen:** VisionKit-documentscanner, OCR op het toestel (Apple Vision), uploaden op de achtergrond (`URLSession` background) met een wachtrij als de server even niet bereikbaar is.
- **Deelmenu:** Share Extension, zodat je een PDF uit de Berichtenbox-app, Mail of WhatsApp direct naar Brievie stuurt.
- **Meldingen:** iOS geeft een app op de achtergrond geen betrouwbare manier om zelf een server te pollen. Daarom:
  - Zonder Connect: deadlines worden bij elke sync als **lokale meldingen** ingepland (`UNUserNotificationCenter`), zodat herinneringen ook werken zonder cloud. Nieuwe documenten ziet de gebruiker pas bij openen van de app.
  - Met Connect (fase 3): pushmeldingen via APNs, zonder inhoud, alleen een signaal.
- **Onderweg:** via Cloudflare Tunnel, Tailscale/WireGuard of later een Connect-tunnel; de app probeert eerst het lokale adres, dan het publieke.
- **Beveiliging:** Face ID / Touch ID om de app te openen, tokens in de Keychain, geen documenten in de iCloud-back-up (cache uitgesloten van back-up).
- **Widget:** eerstvolgende deadlines op het beginscherm.
- **App Store:** de app is gratis en werkt volledig met een eigen server. **In de app staat geen koopknop en geen link naar een betaalpagina.** Hoe een Connect-abonnement wordt verkocht, is beslissing 4 in sectie 21.

**Structuur van de repo:**

```
Brievie_ios/
  project.yml             # XcodeGen: het Xcode-project wordt gegenereerd, niet bewaard
  App/                    # dunne app-laag: start, navigatie
  ShareExtension/
  Widget/
  Packages/
    BrievieAPI/           # gegenereerde client + openapi.json (vastgelegde versie)
    BrievieCore/          # modellen, Keychain, koppelen, certificaat-pin, uploadwachtrij
    BrievieScan/          # VisionKit + Vision OCR
    BrievieUI/            # gedeelde schermonderdelen
    Features/             # Today, Documents, Money, Workspace, Pairing
  docs/                   # DECISIONS.md, verwijzing naar SPEC.md
```

De extensie en de widget gebruiken dezelfde pakketten als de app. Gedeelde gegevens lopen via een App Group.

### Toegang van buitenaf

> **Bijgewerkt 2026-11-24 (DECISIONS "Van buiten bereikbaar"):** compose beslist, de webinterface
> laat alleen zien (pagina "Van buiten bereikbaar" voor serverbeheerders, zonder schakelaar en
> zonder token). Geen wizard-stap waarin de gebruiker een token plakt: de wizard linkt naar de
> uitleg op de website. Volgorde: **VPN (aanbevolen) · Cloudflare Tunnel (makkelijkst, Cloudflare
> leest mee) · eigen reverse proxy · alleen thuis.** Via het publieke adres werkt standaard
> alleen de app (`BRIEVIE_PUBLIC_WEB`), tot tweestapsverificatie er is. Wat hieronder over de
> wizard en de volgorde staat, is daarmee vervangen.

Oorspronkelijk (Cloudflare Tunnel als advies):

- Geen poorten open op de router, geen vast IP nodig, gratis, geldig HTTPS-certificaat op een eigen (sub)domein. Daarmee vervalt ook het eigen certificaat voor de app buitenshuis.
- **Wizard-stap:** gebruiker plakt zijn tunnel-token; Brievie start een `cloudflared`-container in dezelfde Compose-stack en zet de publieke hostname in de app-koppeling.
- **Alleen wat nodig is publiek:** de tunnel wijst naar de API en optioneel de webinterface. Database, Ollama en worker blijven intern.
- **Webinterface extra afschermen:** adviseer Cloudflare Access vóór de webinterface, met e-mail-OTP of SSO. De app gaat via een apart pad met eigen authenticatie (Brievie-account + 2FA + device-token), want Access-logins werken slecht in een native app.
- **Hardening in de API:** rate limiting, lockout na mislukte logins, korte tokenlevensduur, alleen TLS 1.2+, security headers. Ga ervan uit dat de API publiek bereikbaar is.

**Eerlijk in de documentatie en de wizard vermelden:** Cloudflare beëindigt TLS op zijn eigen servers. Verkeer (dus ook geüploade scans) is daar technisch leesbaar voor Cloudflare. Alternatieven in de wizard:

1. **Tailscale/WireGuard:** end-to-end versleuteld, niets publiek, wel een VPN-app op elke telefoon.
2. **Brievie Connect-tunnel** (fase 3): end-to-end TLS tot de eigen server.

Volgorde in de wizard: Cloudflare Tunnel (makkelijkst) · Tailscale (meeste privacy) · alleen thuisnetwerk · later Connect.

### Brievie Connect (optioneel, fase 3, onder voorbehoud van beslissing 3)

Connect ziet **nooit documentinhoud**. Taken:

1. **Push-relay:** de server stuurt "nieuwe taak in werkruimte X" naar Connect, Connect stuurt een push via APNs. Geen inhoud, alleen een signaal; de app haalt de details op bij de eigen server.
2. **Toegang van buitenaf:** versleutelde tunnel (TLS end-to-end tot de eigen server).
3. **Mailadres per werkruimte:** mail komt binnen bij Connect, wordt direct versleuteld met de publieke sleutel van de eigen server en verwijderd na aflevering (max. 7 dagen bij offline server).
4. **Connect AI:** EU-gehoste extractie voor servers zonder sterke hardware. Alleen gemaskeerde tekst, geen opslag, geen training. Kandidaat-leverancier: Scaleway (Parijs, standaard geen opslag). Voorwaarden opnieuw controleren vóór de bouw.

Zonder Connect werkt Brievie volledig, behalve push buiten de deur, het maildoorstuuradres en Connect AI.

### Updates en back-ups

- Ondertekende images, updatekanaal `stable`/`beta`, update vanuit de webinterface met automatische database-back-up vooraf.
- Back-up-wizard: versleutelde back-up naar een tweede schijf, NAS-snapshot of eigen S3-doel. Herstel-test vanuit de webinterface.

### CI/CD

GitHub Actions in beide repos, als harde poort: opmaak, lint, typecheck, tests, dependency- en secretscan. In de host ook SAST, containerscan en image-signing, en de controle dat `openapi.json` klopt.

---

## 12. Datamodel

Alle tabellen: `id` (UUID), `created_at`, `updated_at`. Alles behalve `user` en `server_settings` heeft `workspace_id` en wordt altijd daarop gefilterd (RLS + API-check).

De namen hieronder zijn de begrippen uit dit document. In de code zijn tabel- en veldnamen Engels; de vertaling staat in `docs/GLOSSARY.md` (sectie 19).

| Tabel | Belangrijkste velden |
|---|---|
| `user` | email, naam, taal, uitlegtaal, 2FA |
| `device` | user_id, naam, token (gehasht), laatst gezien, ingetrokken |
| `workspace` | naam, type (`thuis`/`zakelijk`), modules (JSON), versleutelingssleutel-id, mailadres |
| `membership` | user_id, workspace_id, rol, uitnodigingsstatus, map-beperking (optioneel) |
| `person` | (Thuis) weergavenaam, naamvarianten, gekoppeld user_id |
| `relation` | (Zakelijk) naam, kvk, btw_nummer, iban, soort (leverancier/klant), standaardcategorie |
| `folder` | parent_id, naam, pad, sjabloon |
| `document` | soort (`kind`: brief of eigen document), titel (`title`), gescand_door, afzender, relation_id, type, dagtekening, samenvatting_b1, ocr_tekst (BSN gemaskeerd), zoekindex, zichtbaarheid, ~~folder_id~~ (niet gebouwd: label, persoon, type en jaar in plaats van mappen; DECISIONS "Archive"), status, bron, geldig_tot (`expires_on`), herinner_dagen (`remind_days`), bewaren_tot (`retain_until`), verwijderd_op, verwijderd_door, wissen_gestart_op |
| `document_page` | document_id, volgorde, bestandspad (bestand versleuteld) |
| `document_person` | document_id, person_id |
| `document_acl` | document_id, user_id |
| `label` / `document_label` | systeem- of eigen labels |
| `extraction` | document_id, veld, waarde, bron, zekerheid, provider, model, bevestigd_door |
| `rule` | volgorde, naam, voorwaarden (JSON), acties (JSON), stop, actief |
| `rule_run` | rule_id, document_id, resultaat, tijd |
| `task` | document_id (optioneel), type, deadline, deadline_bron, toegewezen_aan, status, afgerond_op |
| `duplicate_candidate` | eerste_document_id, tweede_document_id (geordend, één rij per paar), stand (`open`/`samengevoegd`/`afgewezen`/`ingetrokken`), tekstgelijkenis, getallen_overlap, behouden_document_id, besloten_door, besloten_op |
| `entry` (post) | document_id, richting, bedrag_excl, btw (JSON), bedrag_incl, categorie, person_id/relation_id, vervaldatum, status |
| `bank_transaction` | datum, bedrag, omschrijving, tegenrekening (versleuteld), match_entry_id |
| `audit_log` | user_id, actie, object, tijd |

**Dubbele brieven.** Bij binnenkomst vergelijkt Brievie een brief met de brieven van de laatste 180 dagen in dezelfde werkruimte en slaat een vermoedelijk paar op (`duplicate_candidate`, hooguit 3 open per brief). Een paar is alleen zichtbaar voor wie beide brieven mag zien. API: `listDuplicates` (`GET /workspaces/{id}/duplicates`), `getDuplicate` (`GET .../duplicates/{duplicate_id}`), `mergeDuplicate` (`POST .../merge`: de gekozen brief blijft, de andere gaat naar de prullenbak) en `dismissDuplicate` (`POST .../dismiss`: het zijn twee brieven, nooit meer gevraagd). Erbij: `InboxCountsOut.duplicate`, `DocumentSummaryOut.open_duplicate_count` en `DocumentEventOut.related_document_id`. Ontwerp: `docs/superpowers/specs/2026-11-20-duplicates-design.md`.

**Wat "versleuteld per werkruimte" precies dekt** (keuze, zie sectie 14): de documentbestanden en losse gevoelige velden (IBAN, tegenrekening). De OCR-tekst en de zoekindex staan leesbaar in de database, anders kan er niet gezocht worden. Die worden beschermd door RLS, door BSN-maskering vóór opslag en door het advies om het databasevolume op een versleutelde schijf te zetten.

---

## 13. Schermen en UX

**Installatiewizard (web):** welkom → kies Thuis of Zakelijk (twee grote kaarten met uitleg) → naam werkruimte → beheerdersaccount → hardware-profiel en AI-keuze → opslaglocatie → toegang van buitenaf (link naar de uitleg op de website; compose beslist) → QR-code om de app te koppelen.

**App (tabbalk):** Vandaag · Documenten · [scanknop] · Geld · Werkruimte

**Webinterface (beheer):** Inbox · Mappen (boomstructuur, slepen) · Regels (regel-bouwer met test) · Taken · Geld · Mensen (iedereen die post krijgt, met accountstatus; vervangt Leden en Personen, zie `docs/superpowers/specs/2026-11-21-mensen-en-accounts-design.md`) · Instellingen · Back-ups · Updates

- Thuis-teksten warm en uitleggend; Zakelijk-teksten kort en zakelijk. Zelfde componenten.
- Controle-scherm: document bovenaan, kaarten eronder; elk AI-veld met "waar staat dit?" die de bron markeert.
- Doel: scannen tot bevestigde taak in < 30 seconden; toevoegen van een regel in < 1 minuut.
- WCAG 2.2 AA, grote tekst, schermlezer-labels. In de app: Dynamic Type en VoiceOver.

---

## 14. Privacy, beveiliging en juridisch

- **Data blijft thuis.** Documenten, OCR-tekst en metadata staan alleen op de server van de gebruiker. Wij zijn voor de documenten geen verwerker.
- **Connect** verwerkt alleen wat sectie 11 noemt. De uitzondering voor huishoudelijk gebruik geldt voor de gebruiker, niet voor ons: voor Connect zijn een verwerkersovereenkomst en een DPIA nodig.
- **Versleuteling:** documentbestanden per werkruimte versleuteld (envelope-versleuteling, AES-GCM); TLS overal. Wat niet versleuteld is, staat eerlijk in `PRIVACY.md` (zie sectie 12).
- **BSN** gemaskeerd in OCR-tekst, zoekindex, logs en elke AI-aanroep.
- **Gezondheidsdata** (AVG art. 9): Zorg-label standaard alleen zichtbaar voor de persoon zelf.
- **Toegang:** 2FA, biometrie in de app, sessies en apparaten intrekken, auditlog per document.
- **Lid verlaat werkruimte:** keuze per document op zijn/haar naam: meenemen of achterlaten.
- **Geen telemetrie** standaard; optionele, anonieme foutrapportage met opt-in.
- **AI-verordening (EU):** geen hoog-risicosysteem, wel de transparantieplicht van artikel 50. Elke AI-uitleg is zichtbaar gemarkeerd als door AI gemaakt. Data en details laten controleren bij een jurist; de gevonden bronnen waren advocatenblogs.
- **Aansprakelijkheid voor deadlines:** bron tonen, bevestiging verplicht, duidelijke voorwaarden dat Brievie geen juridisch of fiscaal advies geeft.
- **AGPL en de App Store:** dat gaat alleen samen als wij alle auteursrechten op de iOS-app hebben. Daarom vanaf de eerste externe bijdrage een CLA, in beide repos.
- `PRIVACY.md` en `SELFHOST.md` in de host-repo met datastromen en hardening-tips (niet direct aan internet zonder tunnel/VPN).

---

## 15. Verdienmodel

Hypotheses, te valideren in fase 0. De onderbouwing en de alternatieven staan in sectie 20.

| Wat | Prijs (voorstel) | Inhoud |
|---|---|---|
| Brievie (self-hosted) | Gratis, open-core | Volledige app, Thuis en Zakelijk, lokale AI |
| Supporterslicentie | € 25–49 eenmalig | Vrijwillig, zoals Immich. Geen extra functies, wel een badge |
| Brievie Connect Thuis | € 3–4 / mnd | Push, toegang buitenaf, mailadres, Connect AI |
| Brievie Connect Zakelijk | € 8–12 / mnd per werkruimte | Idem + meer AI-volume, prioriteitssupport |
| Later: gehost | € 4–8 / mnd per huishouden | Voor wie geen eigen hardware heeft; Engelstalige variant voor expats |
| Later: Brievie Box | Eenmalig | Kant-en-klaar kastje |

**Eerlijke kanttekening:** tunnels en push zijn elders gratis te krijgen. Een ruwe schatting voor Connect alleen in een Nederlandstalige self-hosted niche is enkele honderden tot € 1.000 per maand *(niet geverifieerd)*. Dat is geen inkomen. Het geld zit waarschijnlijk in de gehoste variant of in een zakelijke module (sectie 20).

**Licentie:** AGPL voor host en app (concurrenten kunnen geen gesloten cloudversie bouwen), Connect gesloten.

---

## 16. Roadmap en acceptatiecriteria

### Fase 0: validatie en geld (2–3 weken, start nu)

**Met een harde datum:**

- **WBSO 2027** aanvragen vóór 31 december 2026.
- **NLnet**-voorstel indienen vóór 3 januari 2027. Eerst controleren welk fonds open staat.
- **SIDN fonds Pioneers** (tot € 10.000, doorlopend). Lees Simpel is hier eerder gefinancierd.
- Domeinen brievie.com en brievie.eu vastleggen; Naamchecker en merkdepot.

**Gesprekken (20–25):**

- 8 met regelaars en mantelzorgers die post van een ander bijhouden.
- 5 met NAS-eigenaren. Wat gebruiken ze nu, wat mist er in Paperless-ngx 3?
- 5 met expats of mensen die hen helpen.
- 5 met bewindvoerders- of administratiekantoren. Vraag naar postvolume, huidig pakket en wat ze nu betalen.

**Verder:**

- Paperless-ngx 3 zelf draaien, met de AI aan, en opschrijven wat er ontbreekt.
- Testset: 50 brieven (Thuis) + 50 facturen (Zakelijk), geanonimiseerd. Daarop het lokale model kiezen (sectie 10).
- De open beslissingen uit sectie 21 nemen.

### Fase 1: kern + Thuis (± 10 weken)

De eerste mijlpaal is één dunne lijn door beide repos, daarna verbreden.

| Stap | `Brievie_host` | `Brievie_ios` |
|---|---|---|
| **1a. Fundament** | Repo-opzet, CI, Compose, Caddy met eigen certificaat, werkruimtes + leden + RLS, inloggen, OpenAPI v1 | Repo-opzet, CI, gegenereerde client, koppelen via QR, certificaat-pin, Face ID |
| **1b. Eerste lijn** | Upload-endpoint, opslag versleuteld, OCR, pipeline met AI `off` en `local`, validatie, taak aanmaken | Scannen, OCR op toestel, uploadwachtrij, documentenlijst, controlescherm |
| **1c. Ordenen** | Mappen, labels, regels-engine met regel-bouwer en test, NL-regels, zoeken | Vandaag-scherm, taken afvinken, lokale meldingen |
| **1d. Invoer en beheer** | Watch-map, IMAP, Berichtenbox-notificaties, webinterface compleet, wizard, export, back-ups | Deelmenu-extensie, widget, Bonjour, uitlegtaal |

**Klaar met 1b betekent:** een brief scannen op de iPhone, die binnen een minuut verwerkt in de host zien, met een taak en een deadline waarvan de bron te zien is.

### Fase 2: Zakelijk (± 6 weken, onder voorbehoud van beslissing 2)

Relaties, factuurvelden, UBL-import, btw-overzicht, doorsturen naar boekhoudpakket, CSV-export, bewaartermijnen, meekijker voor boekhouder.

### Fase 3: Connect of gehost (± 6 weken, afhankelijk van beslissing 1 en 3)

Push-relay, tunnel, mailadres, Connect AI, betaling. Of in plaats daarvan: de gehoste variant.

### Fase 4

Huishoudboekje + bankimport, alles-in-één image, Unraid/Proxmox-templates, import uit Paperless-ngx, conceptbrieven, Android-app, Brievie Box.

### Acceptatiecriteria (fase 1 + 2)

Elk criterium is een geautomatiseerde test, tenzij anders vermeld.

- [ ] Installatie op een Synology in < 15 minuten met alleen de wizard (handmatig getest).
- [ ] Bij installatie kies je Thuis of Zakelijk; labels, mappen en teksten passen zich aan.
- [ ] Eén server kan een Thuis- en een Zakelijk-werkruimte naast elkaar draaien, volledig gescheiden.
- [ ] Een regel "factuur → `/{jaar}/{kwartaal}/Inkoop/{relatie}`" werkt en is te testen op bestaande documenten.
- [ ] Testset: ≥ 95% juiste afzender, ≥ 90% juiste deadline of terecht `te_controleren`, 0 verzonnen bedragen.
- [ ] Btw-rekensom wordt gecontroleerd; afwijking = `te_controleren`.
- [ ] Met AI op `off` werken OCR, regels, mappen en handmatige taken nog volledig.
- [ ] Geen documentinhoud verlaat de server bij AI `local` of `off` (netwerk-test).
- [ ] BSN komt nergens voor in OCR-tekst, zoekindex, logs of AI-aanroepen.
- [ ] Gebruiker van werkruimte A kan niets zien van werkruimte B: getest per endpoint én per achtergrondtaak.
- [ ] Zoeken op "verzekering" vindt ook "zorgverzekering".
- [ ] De app blijft gekoppeld nadat de server zijn certificaat heeft vernieuwd.
- [ ] Een scan zonder verbinding wordt later vanzelf geüpload, ook als de app gesloten is.
- [ ] `openapi.json` in de repo komt overeen met de code.
- [ ] Back-up maken en terugzetten werkt vanuit de webinterface.

---

## 17. Eerlijke risico's

| Risico | Grootte | Aanpak |
|---|---|---|
| Paperless-ngx 3 is gratis, heeft nu AI en is goed genoeg voor techneuten | Groot | Winnen op handelen, NL-kennis, gedeelde post en gebruiksgemak; import uit Paperless aanbieden |
| Wie de uitleg nodig heeft, heeft geen NAS | Groot | De regelaar als brug; architectuur klaar voor gehost; beslissing 1 |
| Self-hosted is te moeilijk voor gewone mensen | Groot | Wizard, platform-templates, later Brievie Box of gehoste variant |
| Connect levert te weinig op | Groot | Fase 0 valideren; supporterslicentie; gehost of zakelijke module als echte inkomstenbron |
| AI mist een deadline | Groot | Bevestiging verplicht, bronnen tonen, duidelijke voorwaarden |
| Zakelijk concurreert met pakketten die zzp'ers al hebben | Middel | Klein houden, doorsturen in plaats van nabouwen; pas in fase 2; beslissing 2 |
| Lokale AI te traag op instap-NAS | Middel | OCR op de telefoon, hardware-profielen, externe AI, AI-uit-modus die bruikbaar blijft |
| Support op duizenden verschillende installaties | Middel | Strakke Docker-images, diagnose-pagina, alleen `stable` ondersteunen |
| Twee apps bij Android (geen gedeelde code) | Middel | App dun houden, logica op de server, client genereren uit OpenAPI |
| Procrastinate en Django Ninja zijn kleiner dan Celery en DRF | Klein–middel | Taken en endpoints dun houden, zodat wisselen weinig werk is (sectie 18) |
| Apple wijst de app af vanwege het abonnement | Klein–middel | Geen koopknop in de app; beslissing 4 |
| brievie.nl in handen van een ander | Klein–middel | brievie.com of .eu als hoofddomein |

**Inschatting:** self-hosted maakt het privacyverhaal sterk, maar het speelveld is klein en de directe concurrent is gratis en heeft nu ook AI. Het verschil moet zitten in "het regelt mijn post en die van mijn ouders", niet in "het bewaart mijn post". Begin bij fase 0 en laat de gesprekken bepalen hoe breed fase 2 en 3 worden.

---

## 18. Techniekkeuze en onderbouwing

De keuzes zijn getoetst op vijf punten: draait het licht op een NAS of Pi, past het bij documentverwerking, kunnen AI-bouwers er goed mee overweg, is er een grote groep ontwikkelaars voor een open-source-project, en hoeveel onderhoud vraagt het van één persoon.

### Host

| Onderdeel | Keuze | Afgevallen | Waarom | Sterkste tegenargument |
|---|---|---|---|---|
| Taal en framework | **Python 3.13 + Django** (nu 6.1, in april 2027 naar 6.2 LTS) | FastAPI, Litestar, Go, Rust, TypeScript (NestJS/Hono), Kotlin, .NET, Elixir | De kern is documentverwerking: OCR, PDF, XML, taalmodellen. Daar is Python het sterkst. Django levert accounts, rechten en migraties mee. Paperless-ngx bewijst dat het werkt op een NAS | Go en Rust gebruiken een fractie van het geheugen, maar hebben voor OCR toch een Python-hulpproces nodig. TypeScript zou één taal met de webinterface geven |
| API-laag | **Django Ninja** | Django REST Framework | De Pydantic-schema's zijn dezelfde als die van de AI-extractie en leveren een nauwkeurig OpenAPI-schema. Dat schema is het contract met de app | DRF heeft veel meer voorbeelden en een grotere gemeenschap. Mitigatie: endpoints dun houden |
| Achtergrondtaken | **Procrastinate** (wachtrij in PostgreSQL) | Celery + Redis, Django's eigen takenraamwerk, Dramatiq, Huey, Temporal | Het volume is tientallen documenten per dag. Redis is dan een extra container, extra geheugen en iets extra's om te back-uppen. Django's eigen raamwerk heeft nog geen productie-worker | Celery is het bekendst en heeft meer mogelijkheden. Mitigatie: taakfuncties dun houden, zodat overstappen klein is |
| Database | **PostgreSQL 18** | SQLite | Row-level security geeft een tweede, harde scheiding tussen werkruimtes. SQLite heeft dat niet | SQLite is het lichtst en het eenvoudigst te back-uppen |
| Zoeken | **PostgreSQL full-text (Nederlands) + `pg_trgm`** | Meilisearch, Typesense, ParadeDB | Geen extra container en geen tweede leesbare kopie van alle tekst. `pg_trgm` is nodig omdat de Nederlandse woordstam geen samenstellingen splitst | Een aparte zoekmachine zoekt beter en vergeeft typfouten |
| Webinterface | **React 19 + Vite + TypeScript**, statisch gebouwd | Next.js, SvelteKit, Angular, Vue, Django-templates + HTMX | Achter een login is rendering op de server niet nodig; Next.js kost een extra container. De regel-bouwer, de mappenboom en het markeren in een PDF vragen een echte frontend. React heeft de beste toegankelijke componenten | Django-templates met HTMX zou de hoeveelheid code ruwweg halveren |
| Webbibliotheken | React Aria Components, TanStack Query + Router, dnd-kit, pdf.js, Lingui | | React Aria is de sterkste keuze voor WCAG 2.2 AA | |
| HTTPS | **Caddy** | Traefik, nginx | Maakt zelf certificaten, ook de eigen autoriteit voor thuis. Eén kort configuratiebestand | Traefik is handiger bij veel wisselende containers; dat speelt hier niet |
| OCR | **OCRmyPDF + Tesseract**; optioneel PaddleOCR en granite-docling | Surya, EasyOCR, grote visuele modellen | Draait op alles, ook een Pi, met goede Nederlandse herkenning en een open licentie. Surya valt af door de licentie (GPL, gewichten niet vrij voor commercieel gebruik) | Nieuwe visuele modellen lezen tabellen en slechte scans beter, maar vragen een GPU |
| Lokale AI | **Ollama**, aangesproken via de OpenAI-compatibele API | llama.cpp-server, vLLM | Eenvoudig te installeren, dwingt een JSON-schema af. Door de standaard-API is wisselen later gratis | llama.cpp is lichter |

### iOS

| Onderdeel | Keuze | Afgevallen | Waarom | Sterkste tegenargument |
|---|---|---|---|---|
| Taal en framework | **Swift 6 + SwiftUI**, iOS 18 en hoger | React Native + Expo, Flutter, Kotlin Multiplatform, Capacitor/PWA | Bijna elke functie is een Apple-onderdeel: scanner, OCR, deelmenu, widget, uploaden op de achtergrond, Bonjour, Keychain, Face ID. In Expo en Flutter schrijf je de extensie en de widget ook in Swift en onderhoud je daarbovenop een tweede laag. Een PWA kan geen deelmenu en geen upload op de achtergrond | Android wordt een tweede, volledige app. Mitigatie: dunne app, logica op de server, gegenereerde client |
| API-client | Swift OpenAPI Generator (Apple) | Handgeschreven | Eén bron van waarheid: het schema van de host | |
| Lokale opslag | SwiftData voor de wachtrij en de cache; bestanden op schijf | Core Data, GRDB | Minste code voor wat nodig is | GRDB geeft meer controle bij ingewikkelde query's |
| Project | XcodeGen (`project.yml`) + lokale Swift-pakketten | Los Xcode-project | Het projectbestand is leesbaar en geeft geen merge-conflicten. Pakketten dwingen grenzen tussen onderdelen af | Een stap extra voor wie het project opent |

### Tooling

| | Host (Python) | Host (web) | iOS |
|---|---|---|---|
| Pakketten | uv | pnpm | Swift Package Manager |
| Opmaak en lint | ruff | Biome | swift-format + SwiftLint |
| Typecheck | mypy (strict) + django-stubs | TypeScript strict | Swift 6 met strikte concurrency |
| Tests | pytest + pytest-django | Vitest, Playwright | Swift Testing; XCUITest voor de hoofdroute |
| API-types | (bron) | openapi-typescript | swift-openapi-generator |

### Valkuilen die bij deze keuzes horen

- **RLS met Django:** zet de werkruimte per verzoek met `SET LOCAL` binnen een transactie, anders lekt de instelling naar het volgende verzoek. De app-rol mag geen eigenaar van de tabellen zijn. **Elke achtergrondtaak zet de werkruimte ook.** Migraties draaien onder een aparte rol.
- **Certificaat-pin:** op de eigen autoriteit, niet op het servercertificaat (sectie 11).
- **Versies:** Django gaat na 6.2 over op jaarlijkse uitgaven zonder LTS. Plan één keer per jaar een upgrade.

---

## 19. Code guidelines

Het doel: code die iemand over een jaar nog kan lezen en veilig kan aanpassen, ook als een AI hem heeft geschreven.

### Voor beide repos

1. **Indeling per domein, niet per technische laag.** Alles over taken staat bij elkaar. Een onderdeel praat met een ander onderdeel via een kleine, benoemde ingang, niet door in elkaars modellen te graven.
2. **Dunne randen, logica in het midden.** Endpoints, schermen en taken doen alleen invoer controleren, één functie aanroepen en het antwoord teruggeven. De bedrijfslogica staat in gewone functies die zonder netwerk of scherm te testen zijn.
3. **Code in het Engels, schermteksten in het Nederlands.** Namen in code, commits en commentaar zijn Engels. `docs/GLOSSARY.md` legt de vertaling vast (werkruimte = `workspace`, post = `entry`, op naam van = `addressee`). Schermteksten staan nooit hard in de code, altijd via i18n.
4. **Overal types.** De typecheck staat op strikt en is een harde poort. Geen `Any`, geen geforceerde uitpakking (`!`) in Swift, behalve met een regel uitleg.
5. **Geld en tijd.** Geld is altijd `Decimal`, nooit een kommagetal, en gaat als tekst over de lijn. Tijdstippen hebben altijd een tijdzone. Een deadline is een datum, geen tijdstip.
6. **Fouten zijn expliciet.** Geen stille `except` of lege `catch`. Een fout wordt afgehandeld of doorgegeven met context. De gebruiker krijgt een begrijpelijke melding in het Nederlands, het log krijgt de details.
7. **Geen persoonsgegevens in logs.** Loggen loopt door een filter dat BSN's maskeert. Documentinhoud wordt nooit gelogd, ook niet bij fouten.
8. **Kleine functies, duidelijke namen, weinig commentaar.** Commentaar legt uit *waarom*, niet *wat*. Een functie die niet op een scherm past, wordt gesplitst.
9. **Tests eerst voor logica.** Validatie, de regels-engine, termijnberekening en het BSN-filter zijn pure functies en krijgen eerst een test. Elk acceptatiecriterium uit sectie 16 heeft een test met dezelfde naam.
10. **Geen afhankelijkheid zonder reden.** Elke nieuwe bibliotheek krijgt één regel in `DECISIONS.md`: waarom, en wat het alternatief was.
11. **Keuzes vastleggen.** `docs/DECISIONS.md` in beide repos, kort: datum, keuze, reden, wat is afgevallen.
12. **Kleine wijzigingen.** Conventional commits, één onderwerp per pull request, CI groen vóór samenvoegen. Opmaak wordt door het gereedschap bepaald, niet door discussie.

### Extra voor de host

- **Werkruimte-scheiding op één plek.** Eén middleware en één basisklasse regelen het filter; geen losse `filter(workspace=…)` verspreid door de code. Per endpoint en per taak een test dat werkruimte A niets van B ziet.
- **Het API-schema is het product.** Elk endpoint heeft getypeerde invoer en uitvoer, een vaste foutvorm en een naam die in de gegenereerde client leesbaar is.
- **Migraties** zijn klein, omkeerbaar waar het kan, en draaien zonder dat de server lang stilstaat.
- **De pipeline is een rij losse stappen** met elk een eigen invoer- en uitvoertype. Een stap kan opnieuw draaien zonder schade (idempotent), want taken worden bij een fout herhaald.
- **Externe aanroepen** (Ollama, IMAP, externe AI) lopen via een interface met een nep-versie voor tests, een tijdslimiet en een beperkt aantal herhalingen.
- **NL-regels** zijn data (YAML) met een versie, een bron en tests. Geen Nederlandse wetskennis hard in de code.

### Extra voor iOS

- **Schermen zijn dom.** Een scherm toont de toestand en geeft acties door; de logica zit in een model met `@Observable`, dat zonder scherm te testen is.
- **Concurrency volgens Swift 6:** `async/await`, geen eigen threads, strikte controle aan.
- **Geheimen alleen in de Keychain.** Nooit in `UserDefaults` of in een bestand.
- **De wachtrij overleeft alles.** Een scan staat op schijf vóór de upload begint, en verdwijnt pas na bevestiging van de server.
- **Toegankelijkheid is geen nabewerking:** elk scherm werkt met VoiceOver en met de grootste tekstinstelling voordat het af is.

### Werken met AI-bouwers

- Elke repo heeft een `CLAUDE.md` met: de verwijzing naar dit document, de commando's om te bouwen en te testen, en de regels uit deze sectie in het kort.
- Eén fase-stap per opdracht (sectie 16), met de bijbehorende acceptatiecriteria als doel.
- Een stap is af als de tests groen zijn, de typecheck schoon is en het resultaat echt is gestart en bekeken, niet eerder.

---

## 20. Zakelijke kansen (hypotheses voor fase 0)

Uit onderzoek van 13 november 2026. Dit zijn hypotheses, geen besluiten.

| # | Kans | Opbrengst | Moeite | Wat het vraagt |
|---|---|---|---|---|
| 1 | **Subsidies en fiscaal voordeel** (WBSO, NLnet, SIDN fonds) | € 20–60.000 zonder aandelen af te staan | Laag | Aanvragen vóór de data in sectie 16. Open source en privacy passen precies bij NLnet en SIDN fonds |
| 2 | **Familie- en mantelzorgerpost** | Middel | Middel | Zit al in de Thuis-modus (personen, meekijkers). Voor het grote publiek is een gehoste variant of een kastje nodig |
| 3 | **Expats en nieuwkomers:** Nederlandse brief uitgelegd in het Engels, met archief en deadlines | Middel | Laag | Uitlegtaal per lid (zit in fase 1). Bereik via expatcentra en werkgevers. Vraagt een gehoste variant |
| 4 | **Post-intake voor bewindvoerders en budgetbeheer** | Hoogst per klant | Hoog, lange verkoop | Ongeveer 266.000 bewinden in Nederland. Als koppelbare module naast bestaande pakketten (Onview, Mcas), niet als concurrent. Self-hosting is hier eerder een nadeel |
| 5 | **Plek in het Paperless-ecosysteem** | Laag | Laag | Import uit Paperless-ngx; zichtbaar zijn op r/selfhosted, Tweakers en in de catalogi van Unraid en TrueNAS. Levert publiek en vertrouwen op, weinig omzet |

**Wat geen kans is:**

- **Peppol op je eigen server.** Ontvangen loopt altijd via een betaald access point, en zzp'ers krijgen het al in hun boekhoudpakket. De Nederlandse verplichting voor e-facturen tussen bedrijven begint pas op 1 juli 2030. UBL als bestand inlezen is genoeg.
- **De Berichtenbox koppelen.** Er is geen API voor burgers en een app kan niet gemachtigd worden. Alleen de notificatiemails zijn bruikbaar.
- **Gemeenten en schuldhulp.** Dat loopt via aanbestedingen en is voor één persoon niet haalbaar.

**Meewind:** België verplicht e-facturen tussen bedrijven sinds 1 januari 2026, Duitsland volgt in stappen, Nederland in 2030. Administratie wordt overal digitaler, terwijl de papieren en gemengde post voor huishoudens blijft.

---

## 21. Open beslissingen voor de eigenaar

De bouwer neemt deze beslissingen niet zelf. Tot ze genomen zijn, geldt de standaard.

| # | Beslissing | Standaard in dit document | Advies uit het onderzoek |
|---|---|---|---|
| 1 | Komt er een gehoste variant naast self-hosted, en wanneer? | **Besloten 13-11-2026:** eerst alleen self-hosted; architectuur houdt gehost mogelijk. Opnieuw bekijken na de gesprekken van fase 0 | Gehost naar voren halen: daar zit de doelgroep met de echte pijn en waarschijnlijk het geld |
| 2 | Hoe groot wordt Zakelijk? | Volledige fase 2 | Klein houden: doorsturen naar het boekhoudpakket en CSV, geen eigen btw-administratie |
| 3 | Blijft Connect een los product? | Ja, fase 3 | Schrappen als los product; push en toegang horen bij de gehoste variant |
| 4 | Hoe wordt een abonnement verkocht? | Buiten de app, geen koopknop in de app | Zo houden. De regels van Apple in de EU zijn in beweging; opnieuw bekijken vóór fase 3 |
| 5 | Welke doelgroep krijgt na fase 1 voorrang? | NAS-eigenaren en regelaars | Laten afhangen van de gesprekken: expats (weinig werk) of bewindvoerders (meeste opbrengst per klant) |

---

## Bronnen (selectie, 13-11-2026)

- Paperless-ngx releases: https://github.com/paperless-ngx/paperless-ngx/releases
- Django 6.1 en uitgavecyclus: https://www.djangoproject.com/weblog/2026/aug/05/django-61-released/ · https://www.djangoproject.com/weblog/2026/aug/10/annual-release-cycle/
- Django-takenraamwerk: https://docs.djangoproject.com/en/6.1/topics/tasks/
- Procrastinate: https://procrastinate.readthedocs.io/
- RLS en connection pooling: https://dvoraj75.github.io/django-rls-tenants/guides/connection-pooling
- Surya-licentie: https://github.com/VikParuchuri/surya
- Ollama structured outputs: https://docs.ollama.com/capabilities/structured-outputs
- Scaleway dataprivacy: https://www.scaleway.com/en/docs/generative-apis/reference-content/data-privacy/
- Expo SDK 58: https://expo.dev/changelog/sdk-58
- E-facturatie Nederland: https://www.kvk.nl/geldzaken/e-facturatie-komt-eraan/
- Berichtenbox machtigen: https://www.nederlandwereldwijd.nl/mijnoverheid-buiten-nederland/berichtenbox-machtigen
- Bewind en mentorschap (Kamerbrief): https://zoek.officielebekendmakingen.nl/kst-24515-795.html
- NLnet: https://www.nlnet.nl/commonsfund · SIDN fonds: https://www.sidnfonds.nl/funding/pioneers
- WBSO: https://www.rvo.nl/subsidies-financiering/wbso/bereken-wbso-voordeel
- Apple reader-apps: https://developer.apple.com/support/reader-apps/
- Lees Simpel en SIDN fonds: https://www.sidn.nl/en/news-and-blogs/new-app-explains-hard-to-follow-letters-in-easy-language
