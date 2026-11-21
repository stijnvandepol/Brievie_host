import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createRoute, Link, useNavigate } from "@tanstack/react-router";
import { type ReactNode, useEffect, useId, useState } from "react";
import { Button, Label, Radio, RadioGroup } from "react-aria-components";
import { ApiFailure } from "../api/client";
import { dismissDuplicate, mergeDuplicate, refreshAfterDuplicate } from "../api/duplicates";
import { documentQuery, duplicateQuery, labelsQuery, personsQuery } from "../api/queries";
import type { components } from "../api/schema";
import { ConfirmDialog } from "../components/confirm-dialog";
import {
  Addressees,
  formatLetterDate,
  LetterDateText,
  SenderText,
  TypeText,
} from "../components/document-table";
import { LabelChips } from "../components/label-chip";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import { Badge, Loading, MessageBar } from "../components/ui";
import { showDismissed } from "../duplicate-notice";
import { showUndo } from "../undo-trash";
import { workspaceRoute } from "./workspace";

type Duplicate = components["schemas"]["DuplicateOut"];
type Summary = components["schemas"]["DocumentSummaryOut"];
type Difference = components["schemas"]["DuplicateDifferenceOut"];

const scanned = new Intl.DateTimeFormat("nl-NL", { dateStyle: "medium" });
const euro = new Intl.NumberFormat("nl-NL", { style: "currency", currency: "EUR" });
const list = new Intl.ListFormat("nl-NL", { type: "conjunction" });

/** A fact's name inside a sentence ("Let op: bedrag verschilt."). */
function fieldWord(field: string): string {
  switch (field) {
    case "sender":
      return t`afzender`;
    case "letter_date":
      return t`datum`;
    case "document_type":
      return t`soort`;
    case "amount":
      return t`bedrag`;
    case "invoice_number":
      return t`factuurnummer`;
    case "payment_reference":
      return t`kenmerk`;
    case "page_count":
      return t`pagina's`;
    default:
      return field;
  }
}

/** A fact's name as the label of a row. */
function fieldLabel(field: string): string {
  switch (field) {
    case "sender":
      return t`Afzender`;
    case "letter_date":
      return t`Datum`;
    case "document_type":
      return t`Soort`;
    case "amount":
      return t`Bedrag`;
    case "invoice_number":
      return t`Factuurnummer`;
    case "payment_reference":
      return t`Kenmerk`;
    case "page_count":
      return t`Pagina's`;
    default:
      return field;
  }
}

/** A value of `differences` as it reads: dates and amounts in Dutch. */
function shownValue(field: string, value: string): string {
  if (field === "amount" && value !== "" && !Number.isNaN(Number(value))) {
    return euro.format(Number(value));
  }
  if (field === "letter_date" && /^\d{4}-\d{2}-\d{2}$/.test(value)) return formatLetterDate(value);
  return value;
}

function DiffMark({ difference }: { difference: Difference | undefined }) {
  if (!difference) return null;
  return difference.uncertain ? (
    <Badge intent="info">{t`Verschilt misschien (slecht leesbaar)`}</Badge>
  ) : (
    <Badge intent="warning">{t`Verschilt`}</Badge>
  );
}

/** One row of a column: the fact, its value, and whether it differs from the other letter. */
function Row({
  label,
  difference,
  children,
}: {
  label: string;
  difference?: Difference;
  children: ReactNode;
}) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>
        {children} <DiffMark difference={difference} />
      </dd>
    </div>
  );
}

/** The facts only `differences` carries (no field of the summary holds them). */
const EXTRA_FACTS = ["amount", "invoice_number", "payment_reference"];

function Column({
  workspaceId,
  position,
  letter,
  side,
  differences,
  canChoose,
}: {
  workspaceId: string;
  position: 1 | 2;
  letter: Summary;
  side: "first" | "second";
  differences: Difference[];
  canChoose: boolean;
}) {
  const persons = useQuery(personsQuery(workspaceId));
  const labels = useQuery(labelsQuery(workspaceId));
  const detail = useQuery(documentQuery(workspaceId, letter.id));
  const headingId = useId();
  const diff = (field: string) => differences.find((d) => d.field === field);
  const tasks = detail.data?.tasks ?? [];
  const when = scanned.format(new Date(letter.created_at));
  return (
    <section className="panel duplicate-column" aria-labelledby={headingId}>
      <div className="panel-head">
        <h2 id={headingId}>{t`Brief ${position}`}</h2>
      </div>
      <p className="panel-note">
        {t`Gescand op ${when}`} ·{" "}
        <Link to="/w/$workspaceId/post/$documentId" params={{ workspaceId, documentId: letter.id }}>
          {t`Brief openen`}
        </Link>
      </p>
      <img
        className="duplicate-page"
        src={`/api/v1/workspaces/${workspaceId}/documents/${letter.id}/pages/1`}
        alt={t`Eerste pagina van brief ${position}`}
        loading="lazy"
      />
      <dl className="detail-list">
        <Row label={t`Afzender`} difference={diff("sender")}>
          <SenderText document={letter} />
        </Row>
        <Row label={t`Soort`} difference={diff("document_type")}>
          <TypeText document={letter} />
        </Row>
        <Row label={t`Datum`} difference={diff("letter_date")}>
          <LetterDateText document={letter} />
        </Row>
        {EXTRA_FACTS.flatMap((field) => {
          const difference = diff(field);
          return difference
            ? [
                <Row key={field} label={fieldLabel(field)} difference={difference}>
                  {shownValue(field, difference[side])}
                </Row>,
              ]
            : [];
        })}
        <Row label={t`Pagina's`} difference={diff("page_count")}>
          {letter.page_count}
        </Row>
        <Row label={t`Labels`}>
          <LabelChips labels={labels.data ?? []} ids={letter.label_ids ?? []} />
        </Row>
        <Row label={t`Op naam van`}>
          <Addressees persons={persons.data ?? []} ids={letter.addressee_ids ?? []} />
        </Row>
        <Row label={t`Taken`}>
          {tasks.length === 0 ? (
            <span className="muted">{t`Geen`}</span>
          ) : (
            <ul className="plain-list">
              {tasks.map((task) => (
                <li key={task.id}>
                  {task.description}
                  {task.status === "done" ? <span className="muted"> ({t`afgerond`})</span> : null}
                </li>
              ))}
            </ul>
          )}
        </Row>
      </dl>
      {canChoose ? (
        <Radio
          value={letter.id}
          className="keep-choice"
          aria-label={t`Deze brief houden: brief ${position}`}
        >
          <span className="radio-indicator" aria-hidden="true" />
          {t`Deze brief houden`}
        </Radio>
      ) : null}
    </section>
  );
}

function MergeDialog({
  workspaceId,
  pair,
  keepId,
  isOpen,
  onOpenChange,
}: {
  workspaceId: string;
  pair: Duplicate;
  keepId: string;
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [completeTwins, setCompleteTwins] = useState(true);
  const checkboxId = useId();
  // Done on the letter that goes, open on the one that stays.
  const twins = (pair.task_twins ?? []).filter((twin) => twin.open_document_id === keepId);
  const otherId = keepId === pair.first.id ? pair.second.id : pair.first.id;
  useEffect(() => {
    if (isOpen) setCompleteTwins(true);
  }, [isOpen]);

  async function merge() {
    const kept = await mergeDuplicate(workspaceId, pair.id, keepId, completeTwins);
    showUndo(workspaceId, otherId);
    queryClient.setQueryData(["workspaces", workspaceId, "documents", kept.id], kept);
    queryClient.removeQueries({ queryKey: ["workspaces", workspaceId, "documents", otherId] });
    await navigate({
      to: "/w/$workspaceId/post/$documentId",
      params: { workspaceId, documentId: kept.id },
    });
    queryClient.removeQueries({ queryKey: ["workspaces", workspaceId, "duplicates", pair.id] });
    await refreshAfterDuplicate(queryClient, workspaceId);
  }

  return (
    <ConfirmDialog
      isOpen={isOpen}
      onOpenChange={onOpenChange}
      title={t`Samenvoegen?`}
      confirmLabel={t`Samenvoegen`}
      tone="primary"
      closeOnSuccess={false}
      onConfirm={merge}
    >
      <p>
        {t`De brief die je houdt, krijgt de labels en personen van allebei. De andere brief gaat naar de prullenbak. Daar kun je hem 30 dagen terugzetten.`}
      </p>
      {twins.length > 0 ? (
        <div className="check">
          <input
            id={checkboxId}
            type="checkbox"
            checked={completeTwins}
            onChange={(event) => setCompleteTwins(event.currentTarget.checked)}
            aria-describedby={`${checkboxId}-hint`}
          />
          <div>
            <label htmlFor={checkboxId}>{t`Taak ook afronden`}</label>
            <div id={`${checkboxId}-hint`} className="muted small">
              {twins.map((twin) => {
                const task = twin.description;
                return (
                  <p key={`${twin.done_document_id}-${task}`}>
                    {t`Op de andere scan is ''${task}'' al afgerond.`}
                  </p>
                );
              })}
            </div>
          </div>
        </div>
      ) : null}
    </ConfirmDialog>
  );
}

function DuplicateView({
  workspaceId,
  pair,
  canEdit,
}: {
  workspaceId: string;
  pair: Duplicate;
  canEdit: boolean;
}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [keepId, setKeepId] = useState(pair.suggested_keep_id);
  const [asking, setAsking] = useState(false);
  const differences = pair.differences ?? [];
  const same = pair.same ?? [];
  const dismiss = useMutation({
    mutationFn: () => dismissDuplicate(workspaceId, pair.id),
    onSuccess: async () => {
      showDismissed(workspaceId);
      queryClient.removeQueries({ queryKey: ["workspaces", workspaceId, "duplicates", pair.id] });
      await navigate({ to: "/w/$workspaceId/inbox", params: { workspaceId } });
      await refreshAfterDuplicate(queryClient, workspaceId);
    },
  });
  const fields = list.format(differences.map((d) => fieldWord(d.field)));
  const similarity = pair.text_similarity;
  const sameFields = list.format(same.map(fieldWord));
  const columns = (
    <div className="duplicate-columns">
      <Column
        workspaceId={workspaceId}
        position={1}
        letter={pair.first}
        side="first"
        differences={differences}
        canChoose={canEdit}
      />
      <Column
        workspaceId={workspaceId}
        position={2}
        letter={pair.second}
        side="second"
        differences={differences}
        canChoose={canEdit}
      />
    </div>
  );
  return (
    <>
      <p className="muted">{t`Lijkt op elkaar voor ${similarity}%`}</p>
      {differences.length > 0 ? (
        <MessageBar intent="warning">
          {t`Let op: ${fields} verschilt. Een herinnering of een tweede termijn lijkt vaak op de eerste brief, maar het is een andere brief.`}
        </MessageBar>
      ) : null}
      {same.length > 0 ? (
        <p className="muted">{t`Op beide brieven hetzelfde: ${sameFields}.`}</p>
      ) : null}
      {canEdit ? (
        <RadioGroup value={keepId} onChange={setKeepId} className="duplicate-choice">
          <Label className="choice-label">{t`Welke brief houd je?`}</Label>
          {columns}
        </RadioGroup>
      ) : (
        columns
      )}
      {canEdit ? (
        <>
          {pair.can_merge ? null : (
            <MessageBar intent="info">
              {t`Deze twee brieven zijn niet voor dezelfde mensen zichtbaar. Daarom kun je ze niet samenvoegen. Kies 'Het zijn twee brieven', of laat ze eerst voor dezelfde mensen zichtbaar zijn.`}
            </MessageBar>
          )}
          <div className="panel-actions">
            <Button className="button" isDisabled={!pair.can_merge} onPress={() => setAsking(true)}>
              {t`Het is dezelfde brief`}
            </Button>
            <Button
              className="button secondary"
              isPending={dismiss.isPending}
              onPress={() => dismiss.mutate()}
            >
              {t`Het zijn twee brieven`}
            </Button>
          </div>
          <ErrorAlert error={dismiss.error} />
          <MergeDialog
            workspaceId={workspaceId}
            pair={pair}
            keepId={keepId}
            isOpen={asking}
            onOpenChange={setAsking}
          />
        </>
      ) : null}
    </>
  );
}

function DuplicatePage() {
  const { workspace } = duplicateRoute.useRouteContext();
  const { duplicateId } = duplicateRoute.useParams();
  const { data, error, isPending } = useQuery(duplicateQuery(workspace.id, duplicateId));
  usePageTitle(t`Mogelijk dubbel`);
  const gone = error instanceof ApiFailure && error.code === "duplicate_not_found";
  return (
    <Shell workspace={workspace}>
      <PageHeader
        title={t`Mogelijk dubbel`}
        breadcrumb={{
          to: "/w/$workspaceId/inbox",
          params: { workspaceId: workspace.id },
          label: t`Te doen`,
        }}
        description={t`Deze twee brieven lijken op elkaar. Is het dezelfde brief, twee keer gescand? Kijk wat hetzelfde is en wat verschilt.`}
      />
      {isPending ? <Loading /> : null}
      {gone ? (
        <MessageBar intent="info">
          {t`Deze dubbele brieven zijn er niet (meer).`}{" "}
          <Link to="/w/$workspaceId/inbox" params={{ workspaceId: workspace.id }}>
            {t`Naar Te doen`}
          </Link>
        </MessageBar>
      ) : (
        <ErrorAlert error={error} />
      )}
      {data ? (
        <DuplicateView
          workspaceId={workspace.id}
          pair={data}
          canEdit={workspace.role !== "viewer"}
        />
      ) : null}
    </Shell>
  );
}

export const duplicateRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "dubbel/$duplicateId",
  component: DuplicatePage,
});
