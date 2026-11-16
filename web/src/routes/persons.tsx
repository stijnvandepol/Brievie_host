import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createRoute } from "@tanstack/react-router";
import { useState } from "react";
import {
  Button,
  Dialog,
  Form,
  Heading,
  Input,
  Label,
  Modal,
  ModalOverlay,
  Text,
  TextArea,
  TextField,
} from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { membersQuery, personsQuery } from "../api/queries";
import type { components } from "../api/schema";
import { ErrorAlert, Shell, usePageTitle } from "../components/page";
import { workspaceRoute } from "./workspace";

type Person = components["schemas"]["PersonOut"];
type Member = components["schemas"]["MemberOut"];
type PersonBody = components["schemas"]["PersonIn"];

/** What the form edits: `null` is closed, `"new"` adds a person. */
type Editing = Person | "new" | null;

function PersonDialog({
  workspaceId,
  editing,
  onClose,
}: {
  workspaceId: string;
  editing: Editing;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const person = editing === "new" ? null : editing;
  const [name, setName] = useState(person?.display_name ?? "");
  const [variants, setVariants] = useState((person?.name_variants ?? []).join("\n"));

  const save = useMutation({
    mutationFn: async () => {
      const body: PersonBody = {
        display_name: name.trim(),
        name_variants: variants
          .split("\n")
          .map((line) => line.trim())
          .filter((line) => line !== ""),
        archived: person?.archived ?? false,
      };
      const path = { workspace_id: workspaceId };
      const { error } = person
        ? await api.PUT("/api/v1/workspaces/{workspace_id}/persons/{person_id}", {
            params: { path: { ...path, person_id: person.id } },
            body,
          })
        : await api.POST("/api/v1/workspaces/{workspace_id}/persons", {
            params: { path },
            body,
          });
      if (error !== undefined) throw new ApiFailure(error);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: personsQuery(workspaceId).queryKey });
      onClose();
    },
  });

  return (
    <Dialog className="dialog" aria-label={person ? t`Persoon wijzigen` : t`Persoon toevoegen`}>
      <Heading slot="title" className="dialog-title">
        {person ? t`Persoon wijzigen` : t`Persoon toevoegen`}
      </Heading>
      <Form
        className="form"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <TextField className="field" value={name} onChange={setName} isRequired>
          <Label>{t`Naam`}</Label>
          <Input />
        </TextField>
        <TextField className="field" value={variants} onChange={setVariants}>
          <Label>{t`Naamvarianten`}</Label>
          <Text slot="description">
            {t`Zo staat de naam op brieven, bijvoorbeeld J. de Vries. Zet elke variant op een eigen regel.`}
          </Text>
          <TextArea rows={4} />
        </TextField>
        <ErrorAlert error={save.error} />
        <div className="dialog-actions">
          <Button className="button secondary" onPress={onClose}>
            {t`Annuleren`}
          </Button>
          <Button type="submit" className="button" isPending={save.isPending}>
            {t`Opslaan`}
          </Button>
        </div>
      </Form>
    </Dialog>
  );
}

function PersonRow({
  workspaceId,
  person,
  members,
  isAdmin,
  onEdit,
  onError,
}: {
  workspaceId: string;
  person: Person;
  members: Member[];
  isAdmin: boolean;
  onEdit: () => void;
  onError: (error: unknown) => void;
}) {
  const queryClient = useQueryClient();
  const path = { workspace_id: workspaceId, person_id: person.id };
  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: personsQuery(workspaceId).queryKey }),
      queryClient.invalidateQueries({ queryKey: membersQuery(workspaceId).queryKey }),
    ]);

  const archive = useMutation({
    mutationFn: async (archived: boolean) => {
      const { error } = await api.PUT("/api/v1/workspaces/{workspace_id}/persons/{person_id}", {
        params: { path },
        body: { display_name: person.display_name, name_variants: person.name_variants, archived },
      });
      if (error !== undefined) throw new ApiFailure(error);
    },
    onSuccess: refresh,
    onError,
  });
  const remove = useMutation({
    mutationFn: async () => {
      const { error, response } = await api.DELETE(
        "/api/v1/workspaces/{workspace_id}/persons/{person_id}",
        { params: { path } },
      );
      if (!response.ok) throw new ApiFailure(error);
    },
    onSuccess: refresh,
    onError,
  });
  const link = useMutation({
    mutationFn: async (userId: string) => {
      const { error } = await api.PUT(
        "/api/v1/workspaces/{workspace_id}/persons/{person_id}/member",
        { params: { path }, body: { user_id: userId } },
      );
      if (error !== undefined) throw new ApiFailure(error);
    },
    onSuccess: refresh,
    onError,
  });

  const account = members.find((member) => member.user_id === person.user_id);
  // Every member already has a person; only a hand-made one can still be linked.
  const free = members.filter((member) => member.person_id === null);
  const canLink = isAdmin && person.user_id === null && free.length > 0;

  return (
    <tr>
      <th scope="row" className="cell-strong">
        {person.display_name}
        {person.archived ? <span className="tag">{t`Gearchiveerd`}</span> : null}
      </th>
      <td>{person.name_variants.join(", ")}</td>
      <td>{account?.name ?? ""}</td>
      <td className="num">{person.document_count}</td>
      <td className="cell-action">
        <Button
          className="button-text"
          aria-label={t`Wijzigen: ${person.display_name}`}
          onPress={onEdit}
        >
          {t`Wijzigen`}
        </Button>
        {person.archived ? (
          <Button
            className="button-text"
            aria-label={t`Terugzetten: ${person.display_name}`}
            onPress={() => archive.mutate(false)}
          >
            {t`Terugzetten`}
          </Button>
        ) : (
          <Button
            className="button-text"
            aria-label={t`Archiveren: ${person.display_name}`}
            onPress={() => archive.mutate(true)}
          >
            {t`Archiveren`}
          </Button>
        )}
        <DeleteButton person={person} onConfirm={() => remove.mutate()} />
        {canLink ? (
          <select
            className="inline-select"
            aria-label={t`Koppel aan account: ${person.display_name}`}
            value=""
            onChange={(event) => {
              if (event.target.value) link.mutate(event.target.value);
            }}
          >
            <option value="">{t`Kies een account`}</option>
            {free.map((member) => (
              <option key={member.user_id} value={member.user_id}>
                {member.name}
              </option>
            ))}
          </select>
        ) : null}
      </td>
    </tr>
  );
}

function DeleteButton({ person, onConfirm }: { person: Person; onConfirm: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        className="button-text danger"
        aria-label={t`Verwijderen: ${person.display_name}`}
        onPress={() => setOpen(true)}
      >
        {t`Verwijderen`}
      </Button>
      <ModalOverlay isOpen={open} onOpenChange={setOpen} isDismissable className="overlay">
        <Modal className="modal">
          <Dialog role="alertdialog" className="dialog">
            <Heading slot="title" className="dialog-title">
              {t`Persoon verwijderen?`}
            </Heading>
            <p>{t`${person.display_name} wordt verwijderd. Dit kun je niet ongedaan maken.`}</p>
            <div className="dialog-actions">
              <Button className="button secondary" onPress={() => setOpen(false)}>
                {t`Annuleren`}
              </Button>
              <Button
                className="button danger-solid"
                onPress={() => {
                  onConfirm();
                  setOpen(false);
                }}
              >
                {t`Verwijderen`}
              </Button>
            </div>
          </Dialog>
        </Modal>
      </ModalOverlay>
    </>
  );
}

function PersonsPage() {
  usePageTitle(t`Personen`);
  const { workspace } = personsRoute.useRouteContext();
  const persons = useQuery(personsQuery(workspace.id));
  const members = useQuery(membersQuery(workspace.id));
  const [editing, setEditing] = useState<Editing>(null);
  const [rowError, setRowError] = useState<unknown>(null);

  return (
    <Shell workspace={workspace}>
      <header className="page-header">
        <h1>{t`Personen`}</h1>
        <Button
          className="button"
          onPress={() => {
            setRowError(null);
            setEditing("new");
          }}
        >
          {t`Persoon toevoegen`}
        </Button>
      </header>
      {persons.isPending ? <p>{t`Even laden...`}</p> : null}
      <ErrorAlert error={persons.error ?? rowError} />
      {persons.data ? (
        // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrolling box must be reachable by keyboard (WCAG 2.1.1).
        <section className="panel table-scroll" aria-label={t`Personen`} tabIndex={0}>
          <table className="table table-wide">
            <thead>
              <tr>
                <th scope="col">{t`Naam`}</th>
                <th scope="col">{t`Naamvarianten`}</th>
                <th scope="col">{t`Account`}</th>
                <th scope="col">{t`Aantal brieven`}</th>
                <th scope="col">
                  <span className="visually-hidden">{t`Acties`}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {persons.data.map((person) => (
                <PersonRow
                  key={person.id}
                  workspaceId={workspace.id}
                  person={person}
                  members={members.data ?? []}
                  isAdmin={workspace.role === "admin"}
                  onEdit={() => {
                    setRowError(null);
                    setEditing(person);
                  }}
                  onError={setRowError}
                />
              ))}
            </tbody>
          </table>
        </section>
      ) : null}
      <ModalOverlay
        isOpen={editing !== null}
        onOpenChange={(open) => {
          if (!open) setEditing(null);
        }}
        isDismissable
        className="overlay"
      >
        <Modal className="modal">
          <PersonDialog
            // A fresh form per person.
            key={editing === null || editing === "new" ? "new" : editing.id}
            workspaceId={workspace.id}
            editing={editing}
            onClose={() => setEditing(null)}
          />
        </Modal>
      </ModalOverlay>
    </Shell>
  );
}

export const personsRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "personen",
  component: PersonsPage,
});
