import { t } from "@lingui/core/macro";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  Button,
  Dialog,
  DialogTrigger,
  Header,
  Input,
  Label,
  ListBox,
  ListBoxItem,
  ListBoxSection,
  Popover,
  Select,
  SelectValue,
  TextField,
} from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import type { components } from "../api/schema";
import { matchName } from "../names";
import { Icon } from "./icons";
import { ErrorAlert } from "./page";

type Person = components["schemas"]["PersonOut"];

/** Names for a list of ids, in the order of the ids; ids nobody knows are skipped. */
export function personNames(persons: Person[], ids: string[]): string[] {
  return ids.flatMap((id) => {
    const person = persons.find((candidate) => candidate.id === id);
    return person ? [person.display_name] : [];
  });
}

function PersonOption({ person }: { person: Person }) {
  return (
    <ListBoxItem id={person.id} textValue={person.display_name} className="picker-option">
      {person.display_name}
      {person.archived ? ` ${t`(gearchiveerd)`}` : ""}
    </ListBoxItem>
  );
}

/**
 * "Nieuwe persoon" beside the picker: type a name; a person who already has it (or writes
 * it so) is offered first ("Bedoel je ...?"), otherwise "Nieuwe persoon: ‘<naam>’" makes the
 * person and puts them on the letter. A name the server already knows selects that person.
 */
function NewPerson({
  workspaceId,
  persons,
  disabled,
  onChosen,
}: {
  workspaceId: string;
  persons: Person[];
  disabled: boolean;
  onChosen: (personId: string) => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const { exact, partial } = matchName(persons, name);
  const typed = name.trim();
  const create = useMutation({
    mutationFn: async () => {
      const { data, error, response } = await api.POST(
        "/api/v1/workspaces/{workspace_id}/persons",
        {
          params: { path: { workspace_id: workspaceId } },
          body: { display_name: typed, name_variants: [], archived: false, kind: "person" },
        },
      );
      if (data) return data.id;
      const failure = new ApiFailure(error);
      if (response.status === 409 && failure.code === "person_exists") {
        // Made a moment ago by somebody else, or written another way: take that person.
        const known = await queryClient.fetchQuery({
          queryKey: ["workspaces", workspaceId, "persons"],
          queryFn: async () => {
            const answer = await api.GET("/api/v1/workspaces/{workspace_id}/persons", {
              params: { path: { workspace_id: workspaceId } },
            });
            if (answer.error !== undefined || !answer.data) throw new ApiFailure(answer.error);
            return answer.data;
          },
        });
        const found = matchName(known, typed).exact[0];
        if (found) return found.person.id;
      }
      throw failure;
    },
    onSuccess: async (personId) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, "persons"] }),
        queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, "people"] }),
      ]);
      setName("");
      onChosen(personId);
    },
  });
  return (
    <DialogTrigger>
      <Button className="button-text" aria-label={t`Nieuwe persoon`} isDisabled={disabled}>
        <Icon name="personAdd" />
        {t`Nieuwe persoon`}
      </Button>
      <Popover className="popover" placement="bottom start" offset={4}>
        <Dialog className="new-person-dialog" aria-label={t`Nieuwe persoon`}>
          {({ close }) => (
            <div className="form">
              <TextField className="field" value={name} onChange={setName} autoFocus>
                <Label>{t`Naam`}</Label>
                <Input autoComplete="off" />
              </TextField>
              {[...exact.map((match) => match.person), ...partial.slice(0, 5)].map((person) => {
                const variant = exact.find((match) => match.person.id === person.id)?.variant;
                return (
                  <Button
                    key={person.id}
                    className="button secondary"
                    onPress={() => {
                      onChosen(person.id);
                      setName("");
                      close();
                    }}
                  >
                    {variant
                      ? t`Bedoel je ${person.display_name}? (ook: ${variant})`
                      : t`Bedoel je ${person.display_name}?`}
                  </Button>
                );
              })}
              {typed && exact.length === 0 ? (
                <Button
                  className="button"
                  isPending={create.isPending}
                  onPress={() =>
                    create.mutate(undefined, {
                      onSuccess: () => close(),
                    })
                  }
                >
                  {t`Nieuwe persoon: ‘${typed}’`}
                </Button>
              ) : null}
              <ErrorAlert error={create.error} />
            </div>
          )}
        </Dialog>
      </Popover>
    </DialogTrigger>
  );
}

/**
 * Who a letter is addressed to: a multi-select over the workspace's persons. Archived persons
 * are only listed while they are selected, so they can still be taken off a letter. With
 * `departments` ("Afdelingen" or, at home, "Groepen") the departments come first, under
 * that heading, and the persons after them.
 */
export function PersonPicker({
  persons,
  selectedIds,
  onChange,
  disabled = false,
  departments,
  newPersonIn,
}: {
  persons: Person[];
  selectedIds: string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
  departments?: string;
  /** The workspace in which "Nieuwe persoon" may add one (never for a viewer). */
  newPersonIn?: string;
}) {
  const options = persons.filter((person) => !person.archived || selectedIds.includes(person.id));
  const names = personNames(persons, selectedIds);
  const groups = departments
    ? [
        {
          name: departments,
          items: options.filter((person) => person.kind === "department"),
        },
        { name: t`Personen`, items: options.filter((person) => person.kind !== "department") },
      ].filter((group) => group.items.length > 0)
    : null;
  const picker = (
    <Select
      aria-label={t`Op naam van`}
      selectionMode="multiple"
      value={selectedIds}
      onChange={(keys) => onChange(keys.map(String))}
      isDisabled={disabled}
      className="picker"
    >
      <Button className="picker-button">
        <SelectValue className="picker-value">
          {names.length > 0 ? names.join(", ") : t`Niemand`}
        </SelectValue>
        <Icon name="chevronDown" className="picker-chevron" />
      </Button>
      <Popover className="picker-popover">
        {groups ? (
          <ListBox className="picker-list" items={groups}>
            {(group) => (
              <ListBoxSection id={group.name} className="picker-section">
                <Header className="picker-header">{group.name}</Header>
                {group.items.map((person) => (
                  <PersonOption key={person.id} person={person} />
                ))}
              </ListBoxSection>
            )}
          </ListBox>
        ) : (
          <ListBox className="picker-list" items={options}>
            {(person) => <PersonOption person={person} />}
          </ListBox>
        )}
      </Popover>
    </Select>
  );
  if (!newPersonIn) return picker;
  // The same tree while a save runs (the picker is disabled then): a new tree would close
  // the open list.
  return (
    <span className="picker-with-new">
      {picker}
      <NewPerson
        workspaceId={newPersonIn}
        persons={persons}
        disabled={disabled}
        onChosen={(personId) => {
          if (!selectedIds.includes(personId)) onChange([...selectedIds, personId]);
        }}
      />
    </span>
  );
}

/** Sets the addressees of one letter; afterwards every list that shows it is fetched again. */
export function useSetAddressees(workspaceId: string, documentId: string, onSuccess?: () => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (personIds: string[]) => {
      const { data, error } = await api.PUT(
        "/api/v1/workspaces/{workspace_id}/documents/{document_id}/addressees",
        {
          params: { path: { workspace_id: workspaceId, document_id: documentId } },
          body: { person_ids: personIds },
        },
      );
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess,
    // Also after a failure: the lists show the truth.
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, "documents"] }),
        queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, "inbox-counts"] }),
        queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, "persons"] }),
      ]);
    },
  });
}
