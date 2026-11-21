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
import { labelsQuery, meQuery, personsQuery, visibilityQuery } from "../api/queries";
import type { components } from "../api/schema";
import { ExternalAiNotice } from "../components/external-ai-notice";
import { Icon } from "../components/icons";
import { colorName, LABEL_COLORS, LabelChip } from "../components/label-chip";
import { ErrorAlert, focusPageHeading, PageHeader, Shell, usePageTitle } from "../components/page";
import { Loading, MessageBar } from "../components/ui";
import { workspaceRoute } from "./workspace";

type LabelOut = components["schemas"]["LabelOut"];
type LabelBody = components["schemas"]["LabelIn"];
type Person = components["schemas"]["PersonOut"];

/** "Finance: Facturen": the suggestion when a label's name is taken (S 4.6). */
function prefixed(department: Person | undefined, name: string): string {
  return department ? `${department.display_name}: ${name}` : name;
}

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
  departments,
  departmentWord,
}: {
  workspaceId: string;
  editing: Editing;
  onClose: () => void;
  /** The departments the caller may give a label to (all for an admin, their own for a
   * lead); empty when they may not, or departments are off. */
  departments: Person[];
  departmentWord: string;
}) {
  const refresh = useRefreshLabels(workspaceId);
  const label = editing === "new" ? null : editing;
  const [name, setName] = useState(label?.name ?? "");
  const [color, setColor] = useState(label?.color ?? "grey");
  const [owner, setOwner] = useState(label?.department_id ?? "");
  const ownerChanged = owner !== (label?.department_id ?? "");
  const chosen = departments.find((department) => department.id === owner);

  const save = useMutation({
    mutationFn: async () => {
      const body: LabelBody = { name: name.trim(), color };
      // Only a change is sent: an update without department_id never moves a label.
      if (label ? ownerChanged : owner !== "") body.department_id = owner === "" ? null : owner;
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
        <p className="muted">{t`Noem een label niet naar een persoon of een gevoelig onderwerp.`}</p>
        {departments.length > 0 ? (
          <div className="field">
            <label htmlFor="label-owner">{t`Van`}</label>
            <select
              id="label-owner"
              value={owner}
              onChange={(event) => setOwner(event.target.value)}
            >
              <option value="">{t`Iedereen`}</option>
              {departments.map((department) => (
                <option key={department.id} value={department.id}>
                  {department.display_name}
                </option>
              ))}
            </select>
            <span className="muted small">
              {t`Alleen de mensen in ${departmentWord} en de beheerders krijgen dit label te kiezen. Wie een brief met dit label ziet, ziet het label ook.`}
            </span>
          </div>
        ) : null}
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
        {save.error instanceof ApiFailure && save.error.code === "label_exists" && chosen ? (
          <p>
            {t`Probeer`}{" "}
            <Button
              className="button-text"
              onPress={() => {
                setName(prefixed(chosen, name.trim()));
                save.reset();
              }}
            >
              {prefixed(chosen, name.trim())}
            </Button>
          </p>
        ) : null}
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
          <MessageBar intent="success" role="status">
            {plural(result.added, { one: "# label toegevoegd", other: "# labels toegevoegd" })}
            {result.existing > 0
              ? `, ${plural(result.existing, { one: "# bestond al", other: "# bestonden al" })}`
              : ""}
          </MessageBar>
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
        <Icon name="delete" />
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
  owner,
}: {
  workspaceId: string;
  label: LabelOut;
  /** "Iedereen" or the department's name, while departments are on. */
  owner: string | null;
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
      {owner !== null ? <td>{owner}</td> : null}
      <td className="num">{label.document_count}</td>
      <td className="cell-action">
        {canEdit ? (
          <span className="row-actions">
            <Button className="button-text" aria-label={t`Wijzigen: ${name}`} onPress={onEdit}>
              <Icon name="edit" />
              {t`Wijzigen`}
            </Button>
            <DeleteButton label={label} onConfirm={() => remove.mutate()} />
          </span>
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
  const visibility = useQuery(visibilityQuery(workspace.id));
  const departmentsOn = visibility.data?.departments_enabled === true;
  const persons = useQuery({ ...personsQuery(workspace.id), enabled: departmentsOn });
  const me = useQuery(meQuery);
  const { van } = labelsRoute.useSearch();
  const refresh = useRefreshLabels(workspace.id);
  const allDepartments = (persons.data ?? []).filter((person) => person.kind === "department");
  // An admin gives a label to any department, a lead to their own (S 4.6).
  const ownable = departmentsOn
    ? allDepartments.filter(
        (department) =>
          !department.archived &&
          (workspace.role === "admin" ||
            (department.lead_user_ids ?? []).includes(me.data?.id ?? "")),
      )
    : [];
  const ownerName = (label: LabelOut): string | null => {
    if (!departmentsOn) return null;
    if (!label.department_id) return t`Iedereen`;
    return (
      allDepartments.find((department) => department.id === label.department_id)?.display_name ?? ""
    );
  };
  const shown = (labels.data ?? []).filter((label) => !van || label.department_id === van);
  const departmentWord = visibility.data?.kind === "home" ? t`de groep` : t`de afdeling`;
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
      <PageHeader
        title={t`Labels`}
        description={
          showPreset
            ? t`Met labels zet je orde in je post, bijvoorbeeld Belasting of Zorg. Met de startset krijg je in één keer de gebruikelijke labels.`
            : t`Met labels zet je orde in je post, bijvoorbeeld Belasting of Zorg.`
        }
        commands={
          canEdit ? (
            <>
              <Button
                className="button"
                onPress={() => {
                  setRowError(null);
                  setOpen("new");
                }}
              >
                <Icon name="add" />
                {t`Label toevoegen`}
              </Button>
              {showPreset ? (
                <Button
                  className="button-text"
                  isPending={applyPreset.isPending}
                  onPress={() => {
                    setRowError(null);
                    applyPreset.mutate();
                  }}
                >
                  <Icon name="layers" />
                  {t`Startset toevoegen`}
                </Button>
              ) : null}
              <Button
                className="button-text"
                onPress={() => {
                  setRowError(null);
                  setOpen("bulk");
                }}
              >
                <Icon name="add" />
                {t`Meerdere tegelijk toevoegen`}
              </Button>
            </>
          ) : null
        }
      />
      <ExternalAiNotice workspaceId={workspace.id} />
      {labels.isPending ? <Loading /> : null}
      <ErrorAlert error={labels.error ?? applyPreset.error ?? rowError} />
      {labels.data ? (
        // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrolling box must be reachable by keyboard (WCAG 2.1.1).
        <section className="panel table-scroll" aria-label={t`Labels`} tabIndex={0}>
          <table className="table">
            <thead>
              <tr>
                <th scope="col">{t`Label`}</th>
                {departmentsOn ? <th scope="col">{t`Van`}</th> : null}
                <th scope="col">{t`Aantal brieven`}</th>
                <th scope="col">
                  <span className="visually-hidden">{t`Acties`}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {shown.map((label) => (
                <LabelRow
                  key={label.id}
                  owner={ownerName(label)}
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
              departments={ownable}
              departmentWord={departmentWord}
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
  // `?van=<department>`: only that department's labels (from the Afdelingen page).
  validateSearch: (search: Record<string, unknown>): { van?: string } =>
    typeof search.van === "string" ? { van: search.van } : {},
  component: LabelsPage,
});
