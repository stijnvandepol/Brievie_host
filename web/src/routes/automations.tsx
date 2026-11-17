import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createRoute, Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { Button, Dialog, Heading, Modal, ModalOverlay, Switch } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { labelsQuery, personsQuery, rulesQuery } from "../api/queries";
import type { components } from "../api/schema";
import { ErrorAlert, focusPageHeading, Shell, usePageTitle } from "../components/page";
import { ruleSentence } from "../components/rule-sentence";
import { workspaceRoute } from "./workspace";

type RuleOut = components["schemas"]["RuleOut"];
type RuleBody = components["schemas"]["RuleIn"];

/** The editable part of a rule: what `updateRule` takes. */
function toBody(rule: RuleOut, patch: Partial<RuleBody>): RuleBody {
  return {
    name: rule.name,
    active: rule.active,
    mode: rule.mode,
    stop: rule.stop,
    match: rule.match,
    conditions: rule.conditions,
    actions: rule.actions,
    ...patch,
  };
}

function DeleteButton({ rule, onConfirm }: { rule: RuleOut; onConfirm: () => void }) {
  const [open, setOpen] = useState(false);
  const name = rule.name;
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
              {t`Regel verwijderen?`}
            </Heading>
            <p>{t`${name} wordt verwijderd. Wat de regel al heeft gedaan, blijft staan.`}</p>
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

function RuleItem({
  workspaceId,
  rule,
  sentence,
  canEdit,
  isFirst,
  isLast,
  onMove,
  onError,
}: {
  workspaceId: string;
  rule: RuleOut;
  sentence: string;
  canEdit: boolean;
  isFirst: boolean;
  isLast: boolean;
  onMove: (direction: -1 | 1) => void;
  onError: (error: unknown) => void;
}) {
  const queryClient = useQueryClient();
  const name = rule.name;
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: rulesQuery(workspaceId).queryKey });

  const update = useMutation({
    mutationFn: async (patch: Partial<RuleBody>) => {
      const { error } = await api.PUT("/api/v1/workspaces/{workspace_id}/rules/{rule_id}", {
        params: { path: { workspace_id: workspaceId, rule_id: rule.id } },
        body: toBody(rule, patch),
      });
      if (error !== undefined) throw new ApiFailure(error);
    },
    onSuccess: refresh,
    onError,
  });
  const remove = useMutation({
    mutationFn: async () => {
      const { error, response } = await api.DELETE(
        "/api/v1/workspaces/{workspace_id}/rules/{rule_id}",
        { params: { path: { workspace_id: workspaceId, rule_id: rule.id } } },
      );
      if (!response.ok) throw new ApiFailure(error);
    },
    onSuccess: async () => {
      await refresh();
      // The dialog gave focus back to this row's button, which is gone now.
      focusPageHeading();
    },
    onError,
  });

  const applied = rule.applied_count;
  const proposed = rule.proposed_count;
  return (
    <li className="rule-item" data-rule-id={rule.id}>
      <div className="rule-main">
        <h2 className="rule-name">
          {canEdit ? (
            <Link
              to="/w/$workspaceId/automatisering/$ruleId"
              params={{ workspaceId, ruleId: rule.id }}
            >
              {rule.name}
            </Link>
          ) : (
            rule.name
          )}
        </h2>
        <p className="rule-sentence">{sentence}</p>
        <p className="rule-counts">{t`${applied} keer toegepast, ${proposed} keer voorgesteld`}</p>
      </div>
      {canEdit ? (
        <div className="rule-controls">
          <Switch
            className="switch"
            aria-label={t`Automatisch toepassen: ${name}`}
            isSelected={rule.mode === "auto"}
            isDisabled={update.isPending}
            onChange={(on) => update.mutate({ mode: on ? "auto" : "propose" })}
          >
            <span className="switch-track" aria-hidden="true" />
            {t`Automatisch toepassen`}
          </Switch>
          <Switch
            className="switch"
            aria-label={t`Actief: ${name}`}
            isSelected={rule.active}
            isDisabled={update.isPending}
            onChange={(on) => update.mutate({ active: on })}
          >
            <span className="switch-track" aria-hidden="true" />
            {t`Actief`}
          </Switch>
          <div className="rule-buttons">
            <Button
              className="button-text"
              aria-label={t`Omhoog: ${name}`}
              isDisabled={isFirst}
              data-move="up"
              onPress={() => onMove(-1)}
            >
              {t`Omhoog`}
            </Button>
            <Button
              className="button-text"
              aria-label={t`Omlaag: ${name}`}
              isDisabled={isLast}
              data-move="down"
              onPress={() => onMove(1)}
            >
              {t`Omlaag`}
            </Button>
            <DeleteButton rule={rule} onConfirm={() => remove.mutate()} />
          </div>
        </div>
      ) : null}
    </li>
  );
}

function AutomationsPage() {
  usePageTitle(t`Automatisering`);
  const { workspace } = automationsRoute.useRouteContext();
  const queryClient = useQueryClient();
  // A viewer may not read the rules (the server answers 403), so they are not asked for.
  const canRead = workspace.role !== "viewer";
  const rules = useQuery({ ...rulesQuery(workspace.id), enabled: canRead });
  const labels = useQuery({ ...labelsQuery(workspace.id), enabled: canRead });
  const persons = useQuery({ ...personsQuery(workspace.id), enabled: canRead });
  const [rowError, setRowError] = useState<unknown>(null);
  const canEdit = workspace.role === "admin";
  const params = { workspaceId: workspace.id };

  const reorder = useMutation({
    mutationFn: async (ruleIds: string[]) => {
      const { error } = await api.PUT("/api/v1/workspaces/{workspace_id}/rules/order", {
        params: { path: { workspace_id: workspace.id } },
        body: { rule_ids: ruleIds },
      });
      if (error !== undefined) throw new ApiFailure(error);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: rulesQuery(workspace.id).queryKey }),
  });

  const applyPreset = useMutation({
    mutationFn: async () => {
      const { error } = await api.POST("/api/v1/workspaces/{workspace_id}/labels/preset", {
        params: { path: { workspace_id: workspace.id } },
      });
      if (error !== undefined) throw new ApiFailure(error);
    },
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: rulesQuery(workspace.id).queryKey }),
        queryClient.invalidateQueries({ queryKey: labelsQuery(workspace.id).queryKey }),
      ]),
  });

  const list = rules.data ?? [];
  const listElement = useRef<HTMLOListElement>(null);
  // The button that was pressed: focus returns to it (or its sibling) once the order is final.
  const moved = useRef<{ id: string; direction: -1 | 1 } | null>(null);
  const { isPending: reordering } = reorder;
  const { isFetching } = rules;
  useEffect(() => {
    const last = moved.current;
    if (!last || reordering || isFetching) return;
    moved.current = null;
    const item = listElement.current?.querySelector(`[data-rule-id="${last.id}"]`);
    const wanted = item?.querySelector<HTMLButtonElement>(
      `[data-move="${last.direction === -1 ? "up" : "down"}"]`,
    );
    const other = item?.querySelector<HTMLButtonElement>(
      `[data-move="${last.direction === -1 ? "down" : "up"}"]`,
    );
    (wanted && !wanted.disabled ? wanted : other)?.focus();
  }, [reordering, isFetching]);

  function move(index: number, direction: -1 | 1) {
    // The order on screen is not final while a move is on its way.
    if (reorder.isPending) return;
    const ids = list.map((rule) => rule.id);
    const other = index + direction;
    if (other < 0 || other >= ids.length) return;
    [ids[index], ids[other]] = [ids[other] as string, ids[index] as string];
    setRowError(null);
    moved.current = { id: ids[other] as string, direction };
    reorder.mutate(ids);
  }

  return (
    <Shell workspace={workspace}>
      <header className="page-header">
        <h1>{t`Automatisering`}</h1>
        {canEdit ? (
          <div className="header-actions">
            <Link to="/w/$workspaceId/automatisering/nieuw" params={params} className="button">
              {t`Nieuwe regel`}
            </Link>
          </div>
        ) : null}
      </header>
      <p className="panel-note">
        {canEdit
          ? t`Een regel zet labels of een naam op nieuwe post. De regels draaien van boven naar beneden.`
          : canRead
            ? t`Alleen een beheerder kan regels wijzigen.`
            : t`Alleen leden en beheerders van deze werkruimte zien de regels.`}
      </p>
      {canRead && rules.isPending ? <p>{t`Even laden...`}</p> : null}
      <ErrorAlert error={rules.error ?? reorder.error ?? applyPreset.error ?? rowError} />
      {rules.data && list.length === 0 ? (
        <section className="panel" aria-label={t`Regels`}>
          <p className="panel-note">{t`Je hebt nog geen regels.`}</p>
          <div className="header-actions">
            {canEdit ? (
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
            {canEdit ? (
              <Link to="/w/$workspaceId/automatisering/nieuw" params={params} className="button">
                {t`Nieuwe regel`}
              </Link>
            ) : null}
          </div>
        </section>
      ) : null}
      {list.length > 0 ? (
        <ol ref={listElement} className="panel rule-list" aria-label={t`Regels`}>
          {list.map((rule, index) => (
            <RuleItem
              key={rule.id}
              workspaceId={workspace.id}
              rule={rule}
              sentence={ruleSentence(rule, labels.data ?? [], persons.data ?? [])}
              canEdit={canEdit}
              isFirst={index === 0}
              isLast={index === list.length - 1}
              onMove={(direction) => move(index, direction)}
              onError={setRowError}
            />
          ))}
        </ol>
      ) : null}
    </Shell>
  );
}

export const automationsRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "automatisering",
  component: AutomationsPage,
});
