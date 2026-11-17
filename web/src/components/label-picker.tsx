import { t } from "@lingui/core/macro";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, ListBox, ListBoxItem, Popover, Select, SelectValue } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import type { components } from "../api/schema";
import { LabelChip } from "./label-chip";

type Label = components["schemas"]["LabelOut"];

/** The labels of one letter: a multi-select over the workspace's labels. */
export function LabelPicker({
  labels,
  selectedIds,
  onChange,
  disabled = false,
}: {
  labels: Label[];
  selectedIds: string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
}) {
  const selected = selectedIds.flatMap((id) => labels.filter((label) => label.id === id));
  return (
    <Select
      aria-label={t`Labels`}
      selectionMode="multiple"
      value={selectedIds}
      onChange={(keys) => onChange(keys.map(String))}
      isDisabled={disabled}
      className="picker"
    >
      <Button className="picker-button">
        <SelectValue>
          {selected.length > 0 ? (
            <span className="label-chips">
              {selected.map((label) => (
                <LabelChip key={label.id} label={label} />
              ))}
            </span>
          ) : (
            t`Geen`
          )}
        </SelectValue>
      </Button>
      <Popover className="picker-popover">
        <ListBox className="picker-list" items={labels}>
          {(label) => (
            <ListBoxItem id={label.id} textValue={label.name} className="picker-option">
              {label.name}
            </ListBoxItem>
          )}
        </ListBox>
      </Popover>
    </Select>
  );
}

/** Sets the labels of one letter; afterwards every list that shows it is fetched again. */
export function useSetLabels(workspaceId: string, documentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (labelIds: string[]) => {
      const { data, error } = await api.PUT(
        "/api/v1/workspaces/{workspace_id}/documents/{document_id}/labels",
        {
          params: { path: { workspace_id: workspaceId, document_id: documentId } },
          body: { label_ids: labelIds },
        },
      );
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, "documents"] }),
        queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, "labels"] }),
      ]);
    },
  });
}
