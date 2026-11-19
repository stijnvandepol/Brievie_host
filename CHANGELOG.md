# Wijzigingslog

Alle veranderingen in Brievie (de server) die je als beheerder merkt. De vorm volgt
[Keep a Changelog](https://keepachangelog.com/nl/1.1.0/); de versienummers volgen
[Semantic Versioning](https://semver.org/lang/nl/). Elke versie heeft een tag `vX.Y.Z`.
Lees bij elke versie eerst **Voor je bijwerkt**, als die er staat.

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

### Toegevoegd

- Back-up en herstel: `./brievie backup`, `./brievie restore`, `./brievie status`, met een
  back-up naar een schijf, een NAS-map, S3 of een REST-server, en een herstelblad om af te
  drukken.
- Versies: de server heeft een versienummer, dat `/api/v1/health` laat zien
  (`server_version`), en elke versie staat in dit wijzigingslog.
- `./brievie update`: werkt Brievie bij naar een nieuwere versie, met eerst een back-up en
  bij een fout de weg terug.
- De gezondheidscontrole (`/api/v1/health`) controleert nu ook de database.
