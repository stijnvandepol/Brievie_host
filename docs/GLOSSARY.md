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
| zoeken | search (`searchDocuments`) |
| fragment (stuk tekst bij een treffer) | snippet (`SnippetPartOut`) |
| Geen exacte treffer. Dit lijkt erop. | approximate match (`fuzzy`) |
| prullenbak | trash (`trashed_at`, `listTrash`) |
| verwijderen (naar de prullenbak) | trash (`trashDocument`) |
| terugzetten | restore (`restoreDocument`) |
| definitief verwijderen | purge (`purgeDocument`, `purge_started_at`) |
| prullenbak legen | empty trash (`emptyTrash`) |
| bewaartermijn | retention term (`retention.check_removal`) |
| dubbel, Dubbel?, mogelijk dubbel | duplicate (`DuplicateCandidate`, `listDuplicates`) |
| samenvoegen, Het is dezelfde brief | merge (`mergeDuplicate`) |
| Het zijn twee brieven | dismiss (`dismissDuplicate`) |
| Taak ook afronden | complete twin tasks (`complete_twin_tasks`, `task_twins`) |

## English in the interface

The web interface and the server's messages speak Dutch (the source) and English. The
table above names the code; this one fixes the English **words the user reads**, the
same in the web, the server's messages and the iPhone app (`Brievie_ios`
`docs/GLOSSARY-en.md`; copy changes both ways). Plain and friendly, sentence case,
American spelling ("organize", "color"), readable for British readers too. Not
translated: the content of letters, names of persons and labels a user made, the
AI's summaries and tasks (their language is the workspace setting "Samenvattingen",
`summary_language`). Start-set labels and their rules are created in the language of
the request that creates the workspace and keep that name.

| Dutch | English | Note |
|---|---|---|
| post (alle brieven samen) | mail | "All mail", "Search your mail", "My mail". Never "post". |
| brief | letter | One item. "Letter date" for dagtekening. |
| Alle post | All mail | |
| Te doen | To do | |
| Controleren / Te controleren | Check / To check | "Check it yourself" for Zelf controleren. |
| Naam kiezen | Choose a name | |
| Bezig… | Processing… | |
| termijn, uiterlijk | deadline | Never "term" or "due date"; "Deadline {date}" on a task. Vervaldatum (invoice field) is "Due date". |
| taak | task | |
| werkruimte | workspace | |
| Thuis / Zakelijk (Organisatie) | Home / Business | |
| lid | member | |
| meekijker, Alleen lezen | viewer, Read only | Role badge "Viewer". |
| beheerder | admin | serverbeheerder: server admin. |
| koppelen / ontkoppelen | pair / unpair | koppelcode: pairing code; koppelgegevens: pairing details; "App koppelen" page: Devices. |
| apparaat | device | |
| afdeling | department | |
| persoon | person | Persons (page), never "people" for the list of persons. |
| op naam van, geadresseerde | addressed to, addressee | "Address to" for the rule action. |
| afzender | sender | |
| samenvatting | summary | |
| voorstel | suggestion | negeren: dismiss. |
| regel | rule | Automatisering: Automation. |
| startset | starter set | |
| Automatisch toepassen | Apply automatically | |
| Automatisch verwerken (autopilot) | Autopilot | "Confirm letters automatically", "File letters under persons and labels automatically". |
| Automatisch door AI / door regel | Automatically by AI / by rule | |
| bevestigen | confirm | |
| terugdraaien | undo | |
| herstelblad | recovery sheet | |
| prullenbak | Trash | verwijderen: delete (to the trash); definitief verwijderen: delete permanently; terugzetten: restore; prullenbak legen: empty trash. |
| dubbele brief | duplicate letter | Never "double" or "copy". Dubbel?: "Duplicate?"; Mogelijk dubbel: "Possible duplicate". |
| samenvoegen, Het is dezelfde brief / Het zijn twee brieven | merge, "It is the same letter" / "They are two letters" | |
| Taak ook afronden | Also complete the task | The checkbox when a merge would leave a twin task open. |
| herstellink | reset link | |
| back-up | backup | |
| logboek | log | Serverlogboek: Server log. |
| inloggen / uitloggen | sign in / sign out | |
| installatiecode | setup code | |
| noodtoegang | emergency access | |
| privé | private | |
| export, exporteren | export | |
| BSN | BSN | Explained where it matters ("citizen service number"). |
| Belastingdienst, KVK, WOZ, APK, DUO | (unchanged) | Dutch names of Dutch things. |
| Taak: betalen, reageren, bezwaar maken, aanvragen, keuring, opzeggen, verlengen, aangifte | Pay, Respond, File an objection, Apply, Inspection, Cancel contract, Renew, File a return | "Object" and "Cancel" alone read as a noun and a button. |
| Soort: factuur, aanslag, beschikking, rekening, herinnering, aanmaning, polis, jaaropgave, loonstrook, bonnetje | Invoice, Tax assessment, Decision, Bill, Reminder, Payment demand, Policy, Annual statement, Payslip, Receipt | |
