import { t } from "@lingui/core/macro";
import { useMutation, useQueryClient } from "@tanstack/react-query";
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
import type { components } from "../api/schema";
import { ErrorAlert } from "./page";

type PersonBody = components["schemas"]["PersonIn"];

/** What the form edits: a person's name, variants and whether it is archived. */
export type EditedPerson = {
  id: string;
  display_name: string;
  name_variants: string[];
  archived: boolean;
  kind?: string;
};

/** Everything of the workspace that shows a person's name or count is asked again. */
export function refreshPeople(queryClient: ReturnType<typeof useQueryClient>, workspaceId: string) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, "persons"] }),
    queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, "people"] }),
    queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, "members"] }),
  ]);
}

function PersonForm({
  workspaceId,
  person,
  onClose,
}: {
  workspaceId: string;
  person: EditedPerson | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(person?.display_name ?? "");
  const [variants, setVariants] = useState((person?.name_variants ?? []).join("\n"));

  const save = useMutation({
    mutationFn: async () => {
      const body: PersonBody = {
        // Read on create only; the server keeps a department a department.
        kind: person?.kind ?? "person",
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
      await refreshPeople(queryClient, workspaceId);
      onClose();
    },
  });

  return (
    <Dialog
      className="dialog"
      aria-label={person ? t`Naam en schrijfwijzen` : t`Persoon toevoegen`}
    >
      <Heading slot="title" className="dialog-title">
        {person ? t`Naam en schrijfwijzen` : t`Persoon toevoegen`}
      </Heading>
      {person ? null : (
        <p>{t`Iemand die post krijgt. Wil die zelf inloggen, nodig hem of haar daarna uit.`}</p>
      )}
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

/** Adds a person (`editing` "new") or changes a person's name and variants. */
export function PersonDialog({
  workspaceId,
  editing,
  onClose,
}: {
  workspaceId: string;
  editing: EditedPerson | "new" | null;
  onClose: () => void;
}) {
  return (
    <ModalOverlay
      isOpen={editing !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      isDismissable
      className="overlay"
    >
      <Modal className="modal">
        <PersonForm
          // A fresh form per person.
          key={editing === null || editing === "new" ? "new" : editing.id}
          workspaceId={workspaceId}
          person={editing === "new" ? null : editing}
          onClose={onClose}
        />
      </Modal>
    </ModalOverlay>
  );
}
