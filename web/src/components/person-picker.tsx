import { t } from "@lingui/core/macro";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, ListBox, ListBoxItem, Popover, Select, SelectValue } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import type { components } from "../api/schema";

type Person = components["schemas"]["PersonOut"];

/** Names for a list of ids, in the order of the ids; ids nobody knows are skipped. */
export function personNames(persons: Person[], ids: string[]): string[] {
  return ids.flatMap((id) => {
    const person = persons.find((candidate) => candidate.id === id);
    return person ? [person.display_name] : [];
  });
}

/**
 * Who a letter is addressed to: a multi-select over the workspace's persons. Archived persons
 * are only listed while they are selected, so they can still be taken off a letter.
 */
export function PersonPicker({
  persons,
  selectedIds,
  onChange,
  disabled = false,
}: {
  persons: Person[];
  selectedIds: string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
}) {
  const options = persons.filter((person) => !person.archived || selectedIds.includes(person.id));
  const names = personNames(persons, selectedIds);
  return (
    <Select
      aria-label={t`Op naam van`}
      selectionMode="multiple"
      value={selectedIds}
      onChange={(keys) => onChange(keys.map(String))}
      isDisabled={disabled}
      className="picker"
    >
      <Button className="picker-button">
        <SelectValue>{names.length > 0 ? names.join(", ") : t`Niemand`}</SelectValue>
      </Button>
      <Popover className="picker-popover">
        <ListBox className="picker-list" items={options}>
          {(person) => (
            <ListBoxItem id={person.id} textValue={person.display_name} className="picker-option">
              {person.display_name}
              {person.archived ? ` ${t`(gearchiveerd)`}` : ""}
            </ListBoxItem>
          )}
        </ListBox>
      </Popover>
    </Select>
  );
}

/** Sets the addressees of one letter; afterwards every list that shows it is fetched again. */
export function useSetAddressees(workspaceId: string, documentId: string) {
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
