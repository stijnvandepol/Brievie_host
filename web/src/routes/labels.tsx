import { plural, t } from "@lingui/core/macro";
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
  Radio,
  RadioGroup,
  TextArea,
  TextField,
} from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { labelsQuery } from "../api/queries";
import type { components } from "../api/schema";
import { ExternalAiNotice } from "../components/external-ai-notice";
import { colorName, LABEL_COLORS, LabelChip } from "../components/label-chip";
import { ErrorAlert, focusPageHeading, Shell, usePageTitle } from "../components/page";
import { workspaceRoute } from "./workspace";

type LabelOut = components["schemas"]["LabelOut"];
type LabelBody = components["schemas"]["LabelIn"];

/** How many labels the start set of each kind holds (the server's `presets.py`). */
const PRESET_SIZE: Record<string, number> = { home: 10, business: 9 };

/** What the form edits: `null` is closed, `"new"` adds a label. */
type Editing = LabelOut | "new" | null;

function useRefreshLabels(workspaceId: string) {
  const queryClient = useQueryClient();
  // Letters carry label ids, so their lists are stale too.
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: labelsQuery(workspaceId).queryKey }),
      queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, "documents"] }),
    ]);
}

function LabelDialog({
  workspaceId,
  editing,
  onClose,
}: {
  workspaceId: string;
  editing: Editing;
  onClose: () => void;
}) {
  const refresh = useRefreshLabels(workspaceId);
  const label = editing === "new" ? null : editing;
  const [name, setName] = useState(label?.name ?? "");
  const [color, setColor] = useState(label?.color ?? "grey");

  const save = useMutation({
    mutationFn: async () => {
      const body: LabelBody = { name: name.trim(), color };
      const path = { workspace_id: workspaceId };
      const { error } = label
        ? await api.PUT("/api/v1/workspaces/{workspace_id}/labels/{label_id}", {
            params: { path: { ...path, label_id: label.id } },
            body,
          })
        : await api.POST("/api/v1/workspaces/{workspace_id}/labels", {
            params: { path },
            body,
          });
      if (error !== undefined) throw new ApiFailure(error);
    },
    onSuccess: async () => {
      await refresh();
      onClose();
    },
  });

  return (
    <Dialog className="dialog" aria-label={label ? t`Label wijzigen` : t`Label toevoegen`}>
      <Heading slot="title" className="dialog-title">
        {label ? t`Label wijzigen` : t`Label toevoegen`}
      </Heading>
      <Form
        className="form"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <TextField className="field" value={name} onChange={setName} isRequired maxLength={50}>
          <Label>{t`Naam`}</Label>
          <Input />
        </TextField>
        <RadioGroup className="choice" value={color} onChange={setColor}>
          <Label className="choice-label">{t`Kleur`}</Label>
          <div className="color-options">
            {LABEL_COLORS.map((option) => (
              <Radio key={option} value={option} className={`color-option label-${option}`}>
                {colorName(option)}
              </Radio>
            ))}
          </div>
        </RadioGroup>
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

/** The names in "Sales, Finance" or one per line; a name twice (any case) counts once. */
function splitNames(text: string): { names: string[]; total: number } {
  const parts = text
    .split(/[,\n]/)
    .map((part) => part.trim())
    .filter((part) => part !== "");
  const seen = new Set<string>();
  const names = parts.filter((name) => {
    const key = name.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return { names, total: parts.length };
}

type BulkResult = { added: number; existing: number; error: unknown };

function BulkDialog({
  workspaceId,
  labels,
  onClose,
}: {
  workspaceId: string;
  labels: LabelOut[];
  onClose: () => void;
}) {
  const refresh = useRefreshLabels(workspaceId);
  const [text, setText] = useState("");
  const [result, setResult] = useState<BulkResult | null>(null);

  const add = useMutation({
    mutationFn: async (): Promise<BulkResult> => {
      const known = new Set(labels.map((label) => label.name.toLowerCase()));
      const { names, total } = splitNames(text);
      const fresh = names.filter((name) => !known.has(name.toLowerCase()));
      let added = 0;
      let error: unknown = null;
      for (const name of fresh) {
        const { error: failure } = await api.POST("/api/v1/workspaces/{workspace_id}/labels", {
          params: { path: { workspace_id: workspaceId } },
          body: { name, color: "grey" },
        });
        if (failure === undefined) added += 1;
        else error ??= new ApiFailure(failure);
      }
      return { added, existing: total - fresh.length, error };
    },
    onSuccess: async (outcome) => {
      setResult(outcome);
      setText("");
      await refresh();
    },
  });

  return (
    <Dialog className="dialog" aria-label={t`Meerdere tegelijk toevoegen`}>
      <Heading slot="title" className="dialog-title">
        {t`Meerdere tegelijk toevoegen`}
      </Heading>
      <Form
        className="form"
        onSubmit={(event) => {
          event.preventDefault();
          add.mutate();
        }}
      >
        <TextField className="field" value={text} onChange={setText}>
          <Label>{t`Namen`}</Label>
          <TextArea rows={4} />
        </TextField>
        {result ? (
          <p role="status">
            {plural(result.added, { one: "# label toegevoegd", other: "# labels toegevoegd" })}
            {result.existing > 0
              ? `, ${plural(result.existing, { one: "# bestond al", other: "# bestonden al" })}`
              : ""}
          </p>
        ) : null}
        <ErrorAlert error={add.error ?? result?.error} />
        <div className="dialog-actions">
          <Button className="button secondary" onPress={onClose}>
            {t`Sluiten`}
          </Button>
          <Button type="submit" className="button" isPending={add.isPending}>
            {t`Toevoegen`}
          </Button>
        </div>
      </Form>
    </Dialog>
  );
}

function DeleteButton({ label, onConfirm }: { label: LabelOut; onConfirm: () => void }) {
  const [open, setOpen] = useState(false);
  const count = label.document_count;
  const name = label.name;
  return (
    <>
      <Button
        className="button-text danger"
        aria-label={t`Verwijderen: ${name}`}
        onPress={() => setOpen(true)}
      >
        {t`Verwijderen`}
      </Button>
      <ModalOverlay isOpen={open} onOpenChange={setOpen} isDismissable className="overlay">
        <Modal className="modal">
          <Dialog role="alertdialog" className="dialog">
            <Heading slot="title" className="dialog-title">
              {t`Label verwijderen?`}
            </Heading>
            <p>
              {count === 1
                ? t`${name} wordt verwijderd en van 1 brief gehaald. Dit kun je niet ongedaan maken.`
                : t`${name} wordt verwijderd en van ${count} brieven gehaald. Dit kun je niet ongedaan maken.`}
            </p>
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

function LabelRow({
  workspaceId,
  label,
  canEdit,
  onEdit,
  onError,
  onDone,
}: {
  workspaceId: string;
  label: LabelOut;
  canEdit: boolean;
  onEdit: () => void;
  onError: (error: unknown) => void;
  onDone: () => void;
}) {
  const refresh = useRefreshLabels(workspaceId);
  const name = label.name;
  const remove = useMutation({
    mutationFn: async () => {
      const { error, response } = await api.DELETE(
        "/api/v1/workspaces/{workspace_id}/labels/{label_id}",
        { params: { path: { workspace_id: workspaceId, label_id: label.id } } },
      );
      if (!response.ok) throw new ApiFailure(error);
    },
    onSuccess: async () => {
      onDone();
      await refresh();
      // The dialog gave focus back to this row's button, which is gone now.
      focusPageHeading();
    },
    onError,
  });
  return (
    <tr>
      <th scope="row" className="cell-strong">
        <LabelChip label={label} />
      </th>
      <td className="num">{label.document_count}</td>
      <td className="cell-action">
        {canEdit ? (
          <>
            <Button className="button-text" aria-label={t`Wijzigen: ${name}`} onPress={onEdit}>
              {t`Wijzigen`}
            </Button>
            <DeleteButton label={label} onConfirm={() => remove.mutate()} />
          </>
        ) : null}
      </td>
    </tr>
  );
}

/** Which dialog is open: a label to edit, `"new"`, `"bulk"` or none. */
type Open = Editing | "bulk";

function LabelsPage() {
  usePageTitle(t`Labels`);
  const { workspace } = labelsRoute.useRouteContext();
  const labels = useQuery(labelsQuery(workspace.id));
  const refresh = useRefreshLabels(workspace.id);
  const [open, setOpen] = useState<Open>(null);
  const [rowError, setRowError] = useState<unknown>(null);
  const canEdit = workspace.role !== "viewer";

  const applyPreset = useMutation({
    mutationFn: async () => {
      const { error } = await api.POST("/api/v1/workspaces/{workspace_id}/labels/preset", {
        params: { path: { workspace_id: workspace.id } },
      });
      if (error !== undefined) throw new ApiFailure(error);
    },
    onSuccess: refresh,
  });

  // The start set is complete once every preset key exists, whatever the labels were renamed to.
  const presetCount = (labels.data ?? []).filter((label) => label.system_key !== "").length;
  const showPreset =
    workspace.role === "admin" &&
    labels.data !== undefined &&
    presetCount < (PRESET_SIZE[workspace.kind] ?? 0);

  return (
    <Shell workspace={workspace}>
      <header className="page-header">
        <h1>{t`Labels`}</h1>
        {canEdit ? (
          <div className="header-actions">
            {showPreset ? (
              <Button
                className="button secondary"
                isPending={applyPreset.isPending}
                onPress={() => {
                  setRowError(null);
                  applyPreset.mutate();
                }}
              >
                {t`Startset toevoegen`}
              </Button>
            ) : null}
            <Button
              className="button secondary"
              onPress={() => {
                setRowError(null);
                setOpen("bulk");
              }}
            >
              {t`Meerdere tegelijk toevoegen`}
            </Button>
            <Button
              className="button"
              onPress={() => {
                setRowError(null);
                setOpen("new");
              }}
            >
              {t`Label toevoegen`}
            </Button>
          </div>
        ) : null}
      </header>
      <ExternalAiNotice workspaceId={workspace.id} />
      {labels.isPending ? <p>{t`Even laden...`}</p> : null}
      <ErrorAlert error={labels.error ?? applyPreset.error ?? rowError} />
      {labels.data ? (
        // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrolling box must be reachable by keyboard (WCAG 2.1.1).
        <section className="panel table-scroll" aria-label={t`Labels`} tabIndex={0}>
          <table className="table">
            <thead>
              <tr>
                <th scope="col">{t`Label`}</th>
                <th scope="col">{t`Aantal brieven`}</th>
                <th scope="col">
                  <span className="visually-hidden">{t`Acties`}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {labels.data.map((label) => (
                <LabelRow
                  key={label.id}
                  workspaceId={workspace.id}
                  label={label}
                  canEdit={canEdit}
                  onEdit={() => {
                    setRowError(null);
                    setOpen(label);
                  }}
                  onError={setRowError}
                  onDone={() => setRowError(null)}
                />
              ))}
            </tbody>
          </table>
        </section>
      ) : null}
      <ModalOverlay
        isOpen={open !== null}
        onOpenChange={(isOpen) => {
          if (!isOpen) setOpen(null);
        }}
        isDismissable
        className="overlay"
      >
        <Modal className="modal">
          {open === "bulk" ? (
            <BulkDialog
              workspaceId={workspace.id}
              labels={labels.data ?? []}
              onClose={() => setOpen(null)}
            />
          ) : (
            <LabelDialog
              // A fresh form per label.
              key={open === null || open === "new" ? "new" : open.id}
              workspaceId={workspace.id}
              editing={open}
              onClose={() => setOpen(null)}
            />
          )}
        </Modal>
      </ModalOverlay>
    </Shell>
  );
}

export const labelsRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "labels",
  component: LabelsPage,
});
