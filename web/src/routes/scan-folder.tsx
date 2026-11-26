import { plural, t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createRoute } from "@tanstack/react-router";
import { useId, useState } from "react";
import {
  Button,
  Dialog,
  Heading,
  Input,
  Label,
  Modal,
  ModalOverlay,
  Switch,
  TextField,
} from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { personsQuery, scanFolderQuery } from "../api/queries";
import type { components } from "../api/schema";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import { Loading, MessageBar } from "../components/ui";
import { dateFormat } from "../format";
import { workspaceRoute } from "./workspace";

type ScanFolder = components["schemas"]["ScanFolderOut"];
type Subfolder = components["schemas"]["ScanSubfolderOut"];
type Person = components["schemas"]["PersonOut"];

const failedDate = dateFormat({ dateStyle: "medium", timeStyle: "short" });

/** Why a file went to mislukt/, from the server's code. */
function reasonText(code: string): string {
  switch (code) {
    case "file_type_not_supported":
      return t`Geen PDF, JPG of PNG`;
    case "file_too_large":
      return t`Te groot`;
    case "too_many_pages":
      return t`Te veel pagina's`;
    case "file_encrypted":
      return t`Beveiligd met een wachtwoord`;
    case "file_unreadable":
      return t`Beschadigd of onvolledig`;
    case "image_too_large":
      return t`Afbeelding te groot`;
    case "person_gone":
      return t`De persoon of afdeling is er niet meer`;
    case "never_settled":
      return t`Bleef leeg of veranderde steeds`;
    default:
      return t`Niet te verwerken`;
  }
}

function stateText(state: string): string {
  switch (state) {
    case "active":
      return t`Wordt gelezen`;
    case "paused":
      return t`Wacht: afdelingen staan uit`;
    case "stopped":
      return t`Wordt niet gelezen`;
    default:
      return t`Persoon of afdeling is er niet meer`;
  }
}

/** The intake service's own look at the folder: what works and what to do about it. */
function Check({ status }: { status: ScanFolder }) {
  if (!status.enabled) return null;
  const uid = status.owner_uid;
  const gid = status.owner_gid;
  switch (status.check) {
    case "ok":
      return <MessageBar intent="success">{t`De scanmap werkt.`}</MessageBar>;
    case "not_writable":
    case "not_readable":
      return (
        <MessageBar intent="error">
          <p>{t`Brievie kan de scanmap niet lezen. Geef de groep 'users' lees- en schrijfrechten op deze map.`}</p>
          <p className="muted">{t`De map is van gebruiker ${uid} en groep ${gid}.`}</p>
        </MessageBar>
      );
    case "failing":
      return (
        <MessageBar intent="error">{t`Brievie kon de scanmap de laatste keer niet helemaal lezen. Er komen nu geen brieven binnen. Vraag de serverbeheerder om in het logboek van de dienst intake te kijken.`}</MessageBar>
      );
    case "not_empty":
      return (
        <MessageBar intent="error">{t`Er bestaat al een map met deze naam en er staat iets in. Brievie gebruikt alleen een map die het zelf maakt of die leeg is. Kies een andere naam, of maak de map leeg.`}</MessageBar>
      );
    case "taken":
      return (
        <MessageBar intent="error">{t`Deze map hoort bij een andere werkruimte op deze server. Kies een andere naam, of haal het bestand .brievie-werkruimte uit de map als die werkruimte hem niet meer gebruikt.`}</MessageBar>
      );
    case "missing":
      return (
        <MessageBar intent="warning">{t`Brievie vindt de map nog niet en kan hem ook niet maken. Controleer de rechten op de scanmap van de server.`}</MessageBar>
      );
    default:
      return (
        <MessageBar intent="warning">{t`Brievie heeft de scanmap de laatste minuten niet bekeken. Draait Brievie helemaal?`}</MessageBar>
      );
  }
}

function SwitchForm({ workspaceId, status }: { workspaceId: string; status: ScanFolder }) {
  const queryClient = useQueryClient();
  const [enabled, setEnabled] = useState(status.enabled);
  const [perPerson, setPerPerson] = useState(status.per_person);
  const [name, setName] = useState(status.folder_name || status.suggested_name);
  const [saved, setSaved] = useState(false);
  const save = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.PUT("/api/v1/workspaces/{workspace_id}/scan-folder", {
        params: { path: { workspace_id: workspaceId } },
        body: { enabled, folder_name: name.trim(), per_person: perPerson },
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(scanFolderQuery(workspaceId).queryKey, data);
      setSaved(true);
    },
    onError: () => setSaved(false),
  });
  return (
    <section className="panel panel-pad" aria-labelledby="scan-switch">
      <h2 id="scan-switch">{t`Scanmap`}</h2>
      <Switch
        className="switch"
        isSelected={enabled}
        onChange={(on) => {
          setSaved(false);
          setEnabled(on);
        }}
      >
        <span className="switch-track" aria-hidden="true" />
        {t`Scanmap aan`}
      </Switch>
      <TextField
        className="field form-narrow"
        value={name}
        onChange={(value) => {
          setSaved(false);
          setName(value);
        }}
      >
        <Label>{t`Naam van de map`}</Label>
        <Input />
      </TextField>
      <p className="switch-hint">{t`Kleine letters, cijfers en streepjes. Je scanner zet de brieven in deze map in de scanmap van de server.`}</p>
      <Switch
        className="switch"
        isSelected={perPerson}
        onChange={(on) => {
          setSaved(false);
          setPerPerson(on);
        }}
      >
        <span className="switch-track" aria-hidden="true" />
        {t`Mappen per persoon en afdeling`}
      </Switch>
      <p className="switch-hint">{t`Brievie maakt in je map een map voor iedereen met een account en voor elke afdeling. Een brief in zo'n map komt direct bij die persoon of afdeling.`}</p>
      <ErrorAlert error={save.error} />
      <div className="form-actions">
        <Button className="button" isPending={save.isPending} onPress={() => save.mutate()}>
          {t`Opslaan`}
        </Button>
      </div>
      <p role="status" aria-live="polite" className="status">
        {saved ? t`Opgeslagen.` : ""}
      </p>
    </section>
  );
}

function StopButton({ folder, onConfirm }: { folder: Subfolder; onConfirm: () => void }) {
  const [open, setOpen] = useState(false);
  const name = folder.name;
  return (
    <>
      <Button
        className="button-text"
        aria-label={t`Niet meer lezen: ${name}`}
        onPress={() => setOpen(true)}
      >
        {t`Niet meer lezen`}
      </Button>
      <ModalOverlay isOpen={open} onOpenChange={setOpen} isDismissable className="overlay">
        <Modal className="modal">
          <Dialog role="alertdialog" className="dialog">
            <Heading slot="title" className="dialog-title">
              {t`Map ${name} niet meer lezen?`}
            </Heading>
            <p>{t`Brievie haalt geen brieven meer uit deze map. De map en de bestanden erin blijven op de NAS staan.`}</p>
            <div className="dialog-actions">
              <Button className="button secondary" onPress={() => setOpen(false)}>
                {t`Annuleren`}
              </Button>
              <Button
                className="button"
                onPress={() => {
                  onConfirm();
                  setOpen(false);
                }}
              >
                {t`Niet meer lezen`}
              </Button>
            </div>
          </Dialog>
        </Modal>
      </ModalOverlay>
    </>
  );
}

function LinkUnknown({
  name,
  files,
  persons,
  busy,
  onLink,
}: {
  name: string;
  files: number;
  persons: Person[];
  busy: boolean;
  onLink: (personId: string) => void;
}) {
  const selectId = useId();
  const [chosen, setChosen] = useState("");
  // Quoted outside the message: in a catalogue an apostrophe before a placeholder escapes it.
  const quoted = `'${name}'`;
  return (
    <li className="scan-unknown">
      <p>{t`Brievie leest de map ${quoted} niet. Koppel hem aan een persoon of afdeling, of haal de bestanden eruit.`}</p>
      <p className="muted">
        {plural(files, { one: "# bestand wacht", other: "# bestanden wachten" })}
      </p>
      <div className="field form-narrow">
        <label htmlFor={selectId}>{t`Koppel aan …`}</label>
        <select id={selectId} value={chosen} onChange={(event) => setChosen(event.target.value)}>
          <option value="">{t`Kies een persoon of afdeling`}</option>
          {persons.map((person) => (
            <option key={person.id} value={person.id}>
              {person.display_name}
            </option>
          ))}
        </select>
      </div>
      <Button
        className="button secondary"
        isDisabled={chosen === "" || busy}
        onPress={() => onLink(chosen)}
      >
        {t`Koppelen`}
      </Button>
    </li>
  );
}

function Folders({ workspaceId, status }: { workspaceId: string; status: ScanFolder }) {
  const queryClient = useQueryClient();
  const persons = useQuery(personsQuery(workspaceId));
  const update = (data: ScanFolder) =>
    queryClient.setQueryData(scanFolderQuery(workspaceId).queryKey, data);
  const add = useMutation({
    mutationFn: async (body: components["schemas"]["ScanSubfolderIn"]) => {
      const { data, error } = await api.POST(
        "/api/v1/workspaces/{workspace_id}/scan-folder/folders",
        { params: { path: { workspace_id: workspaceId } }, body },
      );
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: update,
  });
  const stop = useMutation({
    mutationFn: async (folderId: string) => {
      const { data, error } = await api.DELETE(
        "/api/v1/workspaces/{workspace_id}/scan-folder/folders/{folder_id}",
        { params: { path: { workspace_id: workspaceId, folder_id: folderId } } },
      );
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: update,
  });
  const withFolder = new Set((status.folders ?? []).map((folder) => folder.person_id));
  const choosable = (persons.data ?? []).filter(
    (person) => !person.archived && (person.kind !== "department" || status.departments_enabled),
  );
  const without = choosable.filter((person) => !withFolder.has(person.id));
  const busy = add.isPending || stop.isPending;
  return (
    <section className="panel panel-pad" aria-labelledby="scan-folders">
      <h2 id="scan-folders">{t`Mappen per persoon en afdeling`}</h2>
      <p className="muted">{t`Een brief uit de map van een persoon of afdeling komt direct bij die persoon of afdeling. Wie hem verder ziet, volgt 'Wie ziet wat' van deze werkruimte.`}</p>
      <ErrorAlert error={persons.error ?? add.error ?? stop.error} />
      {(status.folders ?? []).length > 0 ? (
        <table className="table" aria-label={t`Mappen`}>
          <thead>
            <tr>
              <th scope="col">{t`Map`}</th>
              <th scope="col">{t`Voor`}</th>
              <th scope="col">{t`Stand`}</th>
              <th scope="col">{t`Wacht`}</th>
              <th scope="col">
                <span className="visually-hidden">{t`Actie`}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {(status.folders ?? []).map((folder) => (
              <tr key={folder.id}>
                <td>{folder.name}</td>
                <td>{folder.person_name}</td>
                <td>{stateText(folder.state)}</td>
                <td>{folder.waiting_count}</td>
                <td>
                  {folder.state === "active" || folder.state === "paused" ? (
                    <StopButton folder={folder} onConfirm={() => stop.mutate(folder.id)} />
                  ) : folder.person_id ? (
                    <Button
                      className="button-text"
                      isDisabled={busy}
                      onPress={() =>
                        add.mutate({ person_id: folder.person_id ?? "", name: folder.name })
                      }
                    >
                      {t`Weer lezen`}
                    </Button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {without.length > 0 ? (
        <>
          <h3>{t`Nog zonder map`}</h3>
          <ul className="plain-list">
            {without.map((person) => {
              const name = person.display_name;
              return (
                <li key={person.id}>
                  {name}{" "}
                  <Button
                    className="button-text"
                    aria-label={t`Map maken: ${name}`}
                    isDisabled={busy}
                    onPress={() => add.mutate({ person_id: person.id, name: "" })}
                  >
                    {t`Map maken`}
                  </Button>
                </li>
              );
            })}
          </ul>
        </>
      ) : null}
      {(status.unknown_folders ?? []).length > 0 ? (
        <ul className="plain-list">
          {(status.unknown_folders ?? []).map((folder) => (
            <LinkUnknown
              key={folder.name}
              name={folder.name}
              files={folder.files ?? 0}
              persons={choosable}
              busy={busy}
              onLink={(personId) => add.mutate({ person_id: personId, name: folder.name })}
            />
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function Status({ workspaceId, status }: { workspaceId: string; status: ScanFolder }) {
  const waiting = status.waiting_count;
  const skipped = status.skipped_count ?? 0;
  return (
    <>
      <Check status={status} />
      {status.enabled && (status.skipped_count ?? 0) > 0 ? (
        <MessageBar intent="warning">
          {plural(skipped, {
            one: "Brievie heeft # bestand in de scanmap overgeslagen: er staan te veel bestanden, of de naam of datum van het bestand past niet.",
            other:
              "Brievie heeft # bestanden in de scanmap overgeslagen: er staan te veel bestanden, of de naam of datum van een bestand past niet.",
          })}
        </MessageBar>
      ) : null}
      {status.enabled && status.oldest_waiting_minutes > 60 ? (
        <MessageBar intent="warning">
          {plural(waiting, {
            one: "Er wacht # bestand langer dan een uur. Draait Brievie?",
            other: "Er wachten # bestanden langer dan een uur. Draait Brievie?",
          })}
        </MessageBar>
      ) : null}
      {(status.failed ?? []).length > 0 ? (
        <section className="panel panel-pad" aria-labelledby="scan-failed">
          <h2 id="scan-failed">{t`Mislukt`}</h2>
          <p className="muted">{t`Deze bestanden staan in de map 'mislukt'. Zet een bestand terug in de scanmap om het opnieuw te proberen, of haal het weg.`}</p>
          <table className="table" aria-label={t`Mislukt`}>
            <thead>
              <tr>
                <th scope="col">{t`Bestand`}</th>
                <th scope="col">{t`Reden`}</th>
                <th scope="col">{t`Datum`}</th>
              </tr>
            </thead>
            <tbody>
              {(status.failed ?? []).map((file) => (
                <tr key={`${file.name}-${file.failed_at}`}>
                  <td>{file.name}</td>
                  <td>{reasonText(file.reason)}</td>
                  <td>{failedDate.format(new Date(file.failed_at))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}
      {status.enabled && status.per_person ? (
        <Folders workspaceId={workspaceId} status={status} />
      ) : null}
    </>
  );
}

/**
 * "Scanmap" (intake spec S 3): an office scanner drops PDFs in a folder on the NAS and
 * Brievie takes them in. Admins only, and only when the server has a scan folder.
 */
function ScanFolderPage() {
  usePageTitle(t`Scanmap`);
  const { workspace } = scanFolderRoute.useRouteContext();
  const admin = workspace.role === "admin";
  const status = useQuery({ ...scanFolderQuery(workspace.id), enabled: admin });
  return (
    <Shell workspace={workspace}>
      <PageHeader
        title={t`Scanmap`}
        description={t`Laat een scanner brieven in een map op je NAS zetten. Brievie haalt ze daar elke minuut op.`}
      />
      {!admin ? (
        <p className="muted">{t`Alleen de beheerder van deze werkruimte kan dit veranderen.`}</p>
      ) : status.isPending ? (
        <Loading />
      ) : status.data && !status.data.available ? (
        <p className="muted">{t`Deze server heeft geen scanmap. Vraag de serverbeheerder om er een in te stellen.`}</p>
      ) : status.data ? (
        <>
          <section className="panel panel-pad" aria-labelledby="scan-how">
            <h2 id="scan-how">{t`Zo werkt het`}</h2>
            <p>{t`Zet je scanner op één bestand per brief. Elke PDF, JPG of PNG in de map wordt een brief in Brievie.`}</p>
            <p>{t`Brievie haalt een bestand pas op als het een minuut niet meer verandert, en haalt het daarna uit de map. Dan staat het in Brievie, niet in de prullenbak van je NAS.`}</p>
            <p>{t`Een bestand dat Brievie niet kan lezen, komt in de map 'mislukt' met een uitleg ernaast.`}</p>
          </section>
          <SwitchForm workspaceId={workspace.id} status={status.data} />
          <Status workspaceId={workspace.id} status={status.data} />
        </>
      ) : null}
      <ErrorAlert error={status.error} />
    </Shell>
  );
}

export const scanFolderRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "instellingen/scanmap",
  component: ScanFolderPage,
});
