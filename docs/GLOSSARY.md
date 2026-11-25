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
| Mensen (de lijst van iedereen die post kan krijgen) | people (`listPeople`) |
| accountstatus: niet actief, uitgenodigd, actief, uitgeschakeld | account state: inactive, invited, active, deactivated |
| uitgeschakeld op; vorig account | deactivated_at; former_user |
| beheerd door | managed_by |
| tweestapsverificatie | second factor (`SecondFactor`), two-step sign-in |
| herstelcode | recovery code (`RecoveryCode`) |
| authenticator-app | authenticator app (TOTP) |
| nieuwe telefoon | replacement (`beginSecondFactorReplacement`) |
| nieuwe persoon (AI-voorstel) | new_person suggestion |
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
| organisatie (heette zakelijk) | business |
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
| Inbox (pagina: post die op jou wacht; route `inbox`; heette eerder "Te doen") | inbox |
| Archief (pagina: elke gecontroleerde brief en elk eigen document; route `archief`, `post` stuurt door) | archive (`listDocuments` `archived=true`) |
| Alle post (oude naam van Archief) | documents list |
| soort (brief of eigen document) | kind (`letter`, `document`) |
| eigen document | own document (`kind = document`) |
| titel | title |
| geldig tot | expires_on |
| herinner me … vooraf | remind_days |
| bewaren tot | retain_until |
| verloopt (taak bij geldig tot) | expiry (task type) |
| Controleren (wat een brief in de Inbox nodig heeft) | `inbox_state` `review` (status `needs_review` or `failed`) |
| Naam kiezen (wat een brief in de Inbox nodig heeft) | `inbox_state` `unassigned` |
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
| privé (brief) | restricted (`Document.visibility = restricted`) |
| lezer (van een privébrief) | reader (`DocumentReader`) |
| delen met | share (`DocumentShare`) |
| noodtoegang | emergency access (`Workspace.emergency_access`, `DocumentReader.reason = emergency`) |
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
| Mijn taken | my tasks (`listMyTasks`, `countMyTasks`, `part=mine`) |
| briefstatus | letter status (`letter_status`; never stored, derived from the review and the tasks) |
| Nieuw (status) | new (`letter_status=new`; not the per-user "ongelezen", which is `unread`) |
| Openstaand | open |
| Wacht op reactie | waiting (`Task.waiting_until`, shared) |
| Afgehandeld | handled |
| Te laat | overdue (marker `overdue`, not a status) |
| Uitgesteld | snoozed (`TaskSnooze`, personal) |
| Brief afhandelen | handle a letter (`handleDocument`) |
| Op te pakken | to pick up (`part=to_pick_up`) |
| Alle taken | all tasks (`part=all`) |
| Datum nodig, Datum invullen | needs a date (`needs_deadline`, `term_quote`), set a date (`setTaskDeadline`) |
| Afgerond, Afgevinkt door | done (`status=done`), completed by (`completed_by`) |
| Ik doe dit, Geef aan…, Niet meer aan mij | assign to self, hand over, release (`assignTask`) |
| Vrijgeven (beheerder, taak van een ander) | release (`assignTask`, `can_release`) |
| Mijn post | my post (`listDocuments?addressed_to_me=true`); the start page |
| Ook mijn afdeling / Ook mijn groep | include departments (`include_departments`) |
| Ongelezen (per lid, per brief) | unread (`DocumentReadMark`, `unread`); not "Nieuw", which is the letter's own "not yet checked" |
| Markeer als gelezen / ongelezen, Alles gelezen | mark read / unread (`markDocumentRead`, `markDocumentUnread`, `markDocumentsRead`) |
| Blijft liggen, In de Inbox te lang, Nog niet geopend | lingering (`listLingeringLetters`, `lingering_enabled`), too long in the Inbox, not opened (`addressee_opened_at`) |
| Werkruimte kiezen, Alle werkruimtes | workspace switcher (`/werkruimtes`) |

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
| post (alle brieven samen) | mail | "All mail", "Search your mail". Never "post", except the name "Mijn post": "My post" (decision B1, 21-11-2026; it pairs with "My tasks"). |
| brief | letter | One item. "Letter date" for dagtekening. |
| Archief | Archive | Menu, page. Replaces "Alle post" / "All mail" as the list of everything reviewed. |
| Alle post (oude naam van Archief) | All mail (old name of Archive) | Removed one release after the archive ships. |
| Eigen document / document (soort) | Own document / document (kind) | A letter stays "brief" / "letter". Never "stuk". |
| Soort (brief of document) | Kind (letter or document) | The filter name "Soort"; not "Type". |
| Type: Identiteitsbewijs, Diploma, Garantiebewijs, Belastingaangifte, Medisch | ID document, Diploma, Warranty, Tax return, Medical | The five types of own documents. |
| Document toevoegen | Add a document | |
| Dit document bewaren / Bewaren | Keep this document / Keep | |
| Titel | Title | |
| Datum / Datum brief | Date / Letter date | `letter_date`: "Datum" for a document. |
| Van wie | Belongs to | The person or department of an own document. |
| Geldig tot | Valid until | `expires_on`. |
| Herinner me … vooraf | Remind me … ahead | 7, 30, 60 or 90 days. |
| Bewaren tot | Keep until | `retain_until`. "Bewaren tot 1 jan 2031. Weet je het zeker?": "Keep until Jan 1, 2031. Are you sure?" |
| Alleen voor deze persoon | Only for this person | |
| Loopt af / Verlopen | Expiring / Expired | |
| Verloopt (taak) | Expires | The task an end date becomes. |
| Alles exporteren / Mijn archief exporteren | Export everything / Export my archive | Admin / member. |
| De tekst is niet gelezen | The text could not be read | |
| Inbox | Inbox | The list of letters that wait for you; it was called "Te doen" / "To do" before. |
| Controleren / Te controleren | Check / To check | "Check it yourself" for Zelf controleren. |
| Naam kiezen | Choose a name | |
| Bezig… | Processing… | |
| termijn, uiterlijk | deadline | Never "term" or "due date"; "Deadline {date}" on a task. Vervaldatum (invoice field) is "Due date". |
| taak | task | |
| werkruimte | workspace | |
| Thuis / Organisatie | Home / Organisation | The kind of a workspace (`home`, `business`); "Zakelijk" is no longer shown. |
| lid | member | |
| Alleen lezen (rol; heette meekijker) | Read only | The role `viewer`; its badge says "Read only" (R29). |
| beheerder | admin | serverbeheerder: server admin. |
| koppelen / ontkoppelen | pair / unpair | koppelcode: pairing code; koppelgegevens: pairing details; "App koppelen" page: Devices. |
| apparaat | device | |
| afdeling | department | |
| groep (Thuis) | group | What an Organisatie calls "afdeling" (R51). |
| Verdeelt post | Sorts post | The flag of a member who gets the post nobody else reads. |
| post zonder naam | unnamed post | |
| Iedereen ziet alle post / Eigen post | Everyone sees all post / Own post | The two positions of "Wie ziet wat". |
| Wie ziet wat; Wie kan mijn post zien?; Wie kan deze brief zien? | Who sees what; Who can see my post?; Who can see this letter? | |
| Soort gebruik | Kind of use | Thuis or Organisatie on "Wie ziet wat"; a starting point, not a mode. |
| Zo is Brievie nu ingesteld | This is how Brievie is set up now | The page after installation (`/w/<id>/ingesteld`). |
| Toewijzen | Assign | What a letter without a name waits for while departments are on; "Naam kiezen" without them. |
| Voor mij; Zonder naam | For me; Unnamed | The chips of the Inbox. |
| Privé maken; Delen met; Niet meer privé | Make private; Share with; No longer private | On a letter. |
| Noodtoegang | Emergency access | |
| Zorg-post is privé | Care post is private | The label Zorg is "Healthcare" in English. |
| Leidinggevende | Lead | Of a department. |
| persoon | person | One person; "Persons" where the old list is meant. |
| Mensen | People | The section with everybody who can get mail, each with an account state. |
| niet actief / uitgenodigd / actief / uitgeschakeld | not active / invited / active / switched off | The account state of a person. |
| nooit ingelogd; laatst ingelogd | never signed in; last signed in | "Last in the app" for the newest use of a paired device. |
| Uitnodigen; Opnieuw uitnodigen | Invite; Invite again | |
| Opnieuw versturen; Intrekken | Send again; Withdraw | Of a pending invitation. |
| Uitschakelen | Switch off | An account; the person and their mail stay. |
| Nieuwe persoon | New person | In the addressee picker, and the AI's proposal of a name nobody has. |
| Beheerd door | Managed by | A member who reads the mail of a person without an active account. |
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
| tweestapsverificatie | two-step verification | Never "2FA" or "MFA" in the interface. Aanzetten / Uitzetten: switch on / switch off; wissen (by an admin): clear. |
| herstelcode | recovery code | |
| authenticator-app | authenticator app | |
| Beveiliging | Security | The page of your own two-step verification. |
| Nieuwe telefoon | New phone | Moving two-step verification to another phone. |
| prullenbak | Trash | verwijderen: delete (to the trash); definitief verwijderen: delete permanently; terugzetten: restore; prullenbak legen: empty trash. |
| dubbele brief | duplicate letter | Never "double" or "copy". Dubbel?: "Duplicate?"; Mogelijk dubbel: "Possible duplicate". |
| samenvoegen, Het is dezelfde brief / Het zijn twee brieven | merge, "It is the same letter" / "They are two letters" | |
| Taak ook afronden | Also complete the task | The checkbox when a merge would leave a twin task open. |
| Mijn taken | My tasks | The page and the first navigation entry. |
| Op te pakken | To pick up | Department and unnamed post in an organisation; "Ik doe dit": "I'll do it". |
| Alle taken | All tasks | |
| Datum nodig / Datum invullen | Needs a date / Set a date | |
| Afgerond / Afgevinkt door Anne | Done / Ticked off by Anne | "Toch niet klaar": "Not done after all". |
| Geef aan… / Niet meer aan mij | Hand over… / Not mine any more | Vrijgeven (an admin, on someone else's task): "Release". |
| Mijn post | My post | The start page; "Ook mijn afdeling": "Include my department" (home: "Ook mijn groep", "Include my group"). |
| Ongelezen | Unread | Per member. Markeer als gelezen / ongelezen: "Mark as read" / "Mark as unread"; Alles gelezen: "Mark all read"; Alleen ongelezen: "Unread only". |
| Blijft liggen | Lying too long | "ligt er 4 dagen": "waiting 4 days"; In de Inbox te lang: "Too long in the Inbox"; Nog niet geopend: "Not opened yet". |
| Werkruimte kiezen / Alle werkruimtes | Choose workspace / All workspaces | The switcher's button: "Workspace: Bedrijf. Choose another workspace". |
| Instellingen | Settings | At the bottom of the menu, for everyone: Jouw account (Your account), Apparaten (Devices), Wie kan mijn post zien? (Who can see my post?), AI; an admin also gets "Werkruimte Bedrijf" (Workspace Bedrijf) and Logboek (Log). |
| Doet: Anne | Doing it: Anne | Someone else's task, shown without buttons to a member. |
| Ik doe dit / Vrijgeven | I'll do it / Release | "Ik doe dit" only on a task nobody has; "Vrijgeven" is an admin's on someone else's task. |
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
| Nieuw / Openstaand / Wacht op reactie / Afgehandeld / Te laat | New / Open / Waiting for a reply / Handled / Overdue | Letter status. "Nieuw" is a status of the letter (not yet checked), unlike "ongelezen" (Unread), which is per person. |
| Uitgesteld tot 1 dec | Snoozed until 1 Dec | Personal; only you see it. |
| Brief afhandelen | Mark letter as handled | Button; the dialog lists the tasks it ticks off. |
| Wachten voorbij sinds 3 dec | Waiting ended on 3 Dec | Marker on a task whose waiting date arrived. |
| Van buiten bereikbaar | External access | The settings page and the docs section; "away from home" in running text. |
| publiek adres | public address | The tunnel's or own proxy's name (`BRIEVIE_PUBLIC_HOSTNAME`). |
| eigen reverse proxy | own reverse proxy | |
| tunnel (Cloudflare Tunnel) | tunnel (Cloudflare Tunnel) | |
| Thuisnetwerk | Home network | |
