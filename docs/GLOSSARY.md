# Glossary

Code, table and field names are English; the product speaks Dutch. This list fixes
the translation. Add a line before introducing a new domain term.

| Dutch (UI, spec) | English (code) |
|---|---|
| werkruimte | workspace |
| lid | member |
| meekijker | viewer |
| beheerder | admin |
| persoon | person |
| naamvariant | name_variant |
| gearchiveerd | archived |
| relatie | relation |
| map | folder |
| regel | rule |
| automatisering | automation |
| voorstellen (modus van een regel) | propose |
| terugdraaien | undo |
| taak | task |
| post | entry |
| op naam van | addressee |
| startset | preset |
| thuis | home |
| zakelijk | business |
| document | document |
| pagina | page |
| dagtekening | letter_date |
| afzender | sender |
| samenvatting | summary |
| zekerheid | confidence |
| bron (van een waarde) | quote |
| controleren | review |
| voorstel | proposal |
| label | label |
| geschiedenis | history (`DocumentEvent`) |
| Te doen (pagina: post die op jou wacht; route `inbox`) | inbox |
| Alle post (pagina: alle brieven; route `post`) | documents list |
| Controleren (wat een brief op Te doen nodig heeft) | `inbox_state` `review` (status `needs_review` or `failed`) |
| Naam kiezen (wat een brief op Te doen nodig heeft) | `inbox_state` `unassigned` |
| Bezig… (brief wordt nog gelezen, niets te doen) | `inbox_state` `new` (status `processing`) |
| naamherkenning | name matching |
| startset-regel | preset rule |
| handmatig (regel uitvoeren) | manual run |
| voorbeeld (regel om mee te beginnen) | rule template |
| betalen | pay |
| reageren | respond |
| bezwaar | object |
| aanvragen | apply |
| keuring | inspection |
| opzeggen | cancel |
| verlengen | renew |
| aangifte | file_tax |
| herstelblad | recovery sheet |
| back-up | backup |
| terugzetten, herstellen | restore |
| opruimen | sweep |
| hoofdsleutel | master key |
| back-updienst | keeper |
| bijwerken | update |
| versie | version |
| noodback-up | update fallback back-up (`update-backup` volume) |
| wijzigingslog | change log (`CHANGELOG.md`) |
| bestemming | destination |
| serverbeheerder | server admin (`User.is_server_admin`) |
| uitnodiging | invitation (`Invitation`) |
| herstellink | password reset link (`PasswordReset`) |
| sessie, "Waar je bent ingelogd" | session (`UserSession`) |
| logboek | audit log (`WorkspaceAuditEvent`, `ServerAuditEvent`) |
| melding (aan een account) | notice (`AccountNotice`) |
| intrekken (apparaat) | revoke |
| verlopen (koppeling, apparaat) | expired |
| werkruimte verlaten | leave |
| lid verwijderen | remove member |
| mijn post exporteren | member export (`MemberExport`) |
| koppellink | server link (`brievie://server#…`) |
| koppelbewijs | pairing proof |
| koppelcode | pairing code (`PairingCode`) |
| app-versie | app version |
| apparaat | device |
| uitgelogd, beëindigen (sessie) | end (a session) |
| Automatisch verwerken (de autopilot) | AI autopilot (`AISettings.auto_organise`, `auto_confirm`) |
| Automatisch door AI (kenmerk) | automatic (`automatic`, `confirmed_by_ai`, `created_by_ai`) |
| Zelf controleren | reopen (`reopenDocument`) |
| Iedereen ziet alle post | everyone sees all (`Workspace.everyone_sees_all`) |
| Afdelingen gebruiken (Thuis: groepen gebruiken) | departments enabled (`Workspace.departments_enabled`) |
| afdeling, groep | department (`Person.kind = department`) |
| leidinggevende (van een afdeling) | department lead (`DepartmentMember.is_lead`) |
| Beheerders lezen alle post | admins read all (`Workspace.admins_read_all`) |
| verdeelt post | sorter (`Membership.sorts_post`) |
| organisatie (soort werkruimte) | workspace kind `business` |
| alleen lezen (rol) | viewer |
