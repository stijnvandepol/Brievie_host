import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createRoute, Link, useNavigate } from "@tanstack/react-router";
import { ApiFailure, api } from "../api/client";
import { documentQuery, labelsQuery, personsQuery } from "../api/queries";
import type { components } from "../api/schema";
import { refreshAfterTrash, trashDocument } from "../api/trash";
import { ConfirmAction } from "../components/confirm-dialog";
import { DocumentHistory } from "../components/document-history";
import {
  AutomaticBadge,
  formatLetterDate,
  StatusBadge,
  typeLabel,
} from "../components/document-table";
import { LabelPicker, useSetLabels } from "../components/label-picker";
import { ErrorAlert, focusPageHeading, PageHeader, Shell, usePageTitle } from "../components/page";
import { PersonPicker, useSetAddressees } from "../components/person-picker";
import { SuggestionList } from "../components/suggestion-list";
import { TaskList } from "../components/task-list";
import { Loading, MessageBar } from "../components/ui";
import { showUndo } from "../undo-trash";
import { workspaceRoute } from "./workspace";

type Document = components["schemas"]["DocumentOut"];
type Proposal = components["schemas"]["ProposalOut"];

function proposalLabel(field: string): string {
  switch (field) {
    case "sender":
      return t`Afzender`;
    case "document_type":
      return t`Soort`;
    case "letter_date":
      return t`Datum brief`;
    case "summary":
      return t`Samenvatting`;
    case "action_type":
      return t`Soort taak`;
    case "action_description":
      return t`Taak`;
    case "action_deadline":
      return t`Deadline`;
    case "invoice_number":
      return t`Factuurnummer`;
    case "invoice_due_date":
      return t`Vervaldatum`;
    case "invoice_amount_incl":
      return t`Bedrag incl. btw`;
    default:
      return field;
  }
}

function Proposals({ proposals }: { proposals: Proposal[] }) {
  if (proposals.length === 0) return null;
  return (
    <section className="panel" aria-labelledby="proposals-heading">
      <div className="panel-head">
        <h2 id="proposals-heading">{t`Wat de AI las`}</h2>
      </div>
      <dl className="detail-list">
        {proposals.map((proposal) => (
          <div key={`${proposal.group}-${proposal.field}-${proposal.value}`}>
            <dt>{proposalLabel(proposal.field)}</dt>
            <dd>
              {proposal.value}
              {proposal.quote ? <q className="muted small block">{proposal.quote}</q> : null}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function StatusNotice({ document }: { document: Document }) {
  if (document.status === "processing") {
    return <MessageBar intent="info">{t`Deze brief wordt nog gelezen.`}</MessageBar>;
  }
  if (document.status === "needs_review" || document.status === "failed") {
    return <MessageBar intent="warning">{t`Controleer deze brief in de app.`}</MessageBar>;
  }
  return null;
}

/** What is on the letter because Brievie put it there itself: names, one hint per kind. */
function AutomaticHint({ names }: { names: string[] }) {
  if (names.length === 0) return null;
  const list = names.join(", ");
  return <p className="muted small">{t`Automatisch toegevoegd: ${list}`}</p>;
}

/** Hands a letter the AI confirmed back to "Inbox" (Controleren); the answer is the letter itself. */
function useReopen(workspaceId: string, documentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST(
        "/api/v1/workspaces/{workspace_id}/documents/{document_id}/reopen",
        { params: { path: { workspace_id: workspaceId, document_id: documentId } } },
      );
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: (document) => {
      queryClient.setQueryData(["workspaces", workspaceId, "documents", documentId], document);
      // The button and its dialog go away with the AI's confirmation: focus the page heading
      // once they are gone, so it does not fall to the page's body.
      setTimeout(focusPageHeading, 0);
    },
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, "documents"] }),
        queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId, "inbox-counts"] }),
      ]);
    },
  });
}

/**
 * Moves the letter to the trash, then goes to "Alle post" with the undo bar. The letter's own
 * cache entry goes: it answers `document_in_trash` from now on.
 */
function useTrash(workspaceId: string, documentId: string) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  return useMutation({
    mutationFn: () => trashDocument(workspaceId, documentId),
    onSuccess: async () => {
      showUndo(workspaceId, documentId);
      await navigate({ to: "/w/$workspaceId/post", params: { workspaceId } });
      queryClient.removeQueries({ queryKey: ["workspaces", workspaceId, "documents", documentId] });
      await refreshAfterTrash(queryClient, workspaceId);
    },
  });
}

function DocumentView({
  workspaceId,
  document,
  canEdit,
}: {
  workspaceId: string;
  document: Document;
  canEdit: boolean;
}) {
  const persons = useQuery(personsQuery(workspaceId));
  const labels = useQuery(labelsQuery(workspaceId));
  const setAddressees = useSetAddressees(workspaceId, document.id);
  const setLabels = useSetLabels(workspaceId, document.id);
  const reopen = useReopen(workspaceId, document.id);
  const trash = useTrash(workspaceId, document.id);
  const autoLabels = (labels.data ?? [])
    .filter((label) => (document.auto_label_ids ?? []).includes(label.id))
    .map((label) => label.name);
  const autoPeople = (persons.data ?? [])
    .filter((person) => (document.auto_addressee_ids ?? []).includes(person.id))
    .map((person) => person.display_name);
  const sender = document.sender || t`Onbekende afzender`;
  const pages = Array.from({ length: document.page_count }, (_, index) => index + 1);
  const reviewing = document.status === "needs_review" || document.status === "failed";
  return (
    <div className="document">
      <div className="document-pages">
        {pages.map((n) => (
          <img
            key={n}
            className="document-page"
            src={`/api/v1/workspaces/${workspaceId}/documents/${document.id}/pages/${n}`}
            alt={t`Pagina ${n} van ${document.page_count}`}
            loading="lazy"
          />
        ))}
      </div>
      <div className="document-side">
        <StatusNotice document={document} />
        <section className="panel" aria-label={t`Gegevens`}>
          <dl className="detail-list">
            <div>
              <dt>{t`Afzender`}</dt>
              <dd>{sender}</dd>
            </div>
            <div>
              <dt>{t`Soort`}</dt>
              <dd>{typeLabel(document.document_type)}</dd>
            </div>
            <div>
              <dt>{t`Datum brief`}</dt>
              <dd>{document.letter_date ? formatLetterDate(document.letter_date) : ""}</dd>
            </div>
            <div>
              <dt>{t`Status`}</dt>
              <dd>
                <span className="cell-badges">
                  <StatusBadge status={document.status} inboxState={document.inbox_state} />
                  <AutomaticBadge document={document} />
                </span>
              </dd>
            </div>
            <div>
              <dt>{t`Op naam van`}</dt>
              <dd>
                <PersonPicker
                  persons={persons.data ?? []}
                  selectedIds={document.addressee_ids ?? []}
                  onChange={(ids) => setAddressees.mutate(ids)}
                  disabled={!canEdit || document.status === "processing" || setAddressees.isPending}
                />
                <AutomaticHint names={autoPeople} />
              </dd>
            </div>
            <div>
              <dt>{t`Labels`}</dt>
              <dd>
                <LabelPicker
                  labels={labels.data ?? []}
                  selectedIds={document.label_ids ?? []}
                  onChange={(ids) => setLabels.mutate(ids)}
                  disabled={!canEdit || document.status === "processing" || setLabels.isPending}
                />
                <AutomaticHint names={autoLabels} />
              </dd>
            </div>
          </dl>
          <ErrorAlert
            error={setAddressees.error ?? setLabels.error ?? persons.error ?? labels.error}
          />
          {canEdit ? (
            <div className="panel-actions">
              {document.confirmed_by_ai ? (
                <ConfirmAction
                  label={t`Zelf controleren`}
                  icon="undo"
                  title={t`Zelf controleren?`}
                  confirmLabel={t`Zelf controleren`}
                  tone="primary"
                  onConfirm={() => reopen.mutateAsync()}
                >
                  <p>{t`De brief komt terug in de Inbox, bij Controleren. De taken die de AI maakte en die nog open staan, verdwijnen. Je controleert de brief daarna in de app.`}</p>
                </ConfirmAction>
              ) : null}
              {/* Primary, not danger: the letter can be put back for 30 days. */}
              <ConfirmAction
                label={t`Verwijderen`}
                icon="delete"
                title={t`Brief verwijderen?`}
                confirmLabel={t`Naar de prullenbak`}
                tone="primary"
                onConfirm={() => trash.mutateAsync()}
              >
                <p>{t`De brief gaat naar de prullenbak. Daar blijft hij 30 dagen. Zo lang kun je hem terugzetten. Daarna wordt hij voorgoed gewist.`}</p>
              </ConfirmAction>
            </div>
          ) : null}
        </section>
        <SuggestionList
          workspaceId={workspaceId}
          document={document}
          persons={persons.data ?? []}
          labels={labels.data ?? []}
          canEdit={canEdit}
        />
        {document.summary ? (
          <section className="panel" aria-labelledby="summary-heading">
            <div className="panel-head">
              <h2 id="summary-heading">{t`Samenvatting`}</h2>
            </div>
            <p className="panel-note">{document.summary}</p>
            {document.summary_by_ai ? (
              <p className="panel-note muted small">{t`Gemaakt door AI. Controleer of het klopt.`}</p>
            ) : null}
          </section>
        ) : null}
        {reviewing ? <Proposals proposals={document.proposals} /> : null}
        {document.tasks.length > 0 ? (
          <section className="panel" aria-labelledby="tasks-heading">
            <div className="panel-head">
              <h2 id="tasks-heading">{t`Taken`}</h2>
            </div>
            <TaskList
              workspaceId={workspaceId}
              documentId={document.id}
              tasks={document.tasks}
              canEdit={canEdit}
            />
          </section>
        ) : null}
        <DocumentHistory workspaceId={workspaceId} documentId={document.id} canEdit={canEdit} />
        <details className="panel letter-text">
          <summary>{t`Tekst van de brief`}</summary>
          {document.pages.map((page) => (
            <p key={page.index} className="letter-page">
              {page.ocr_text}
            </p>
          ))}
        </details>
      </div>
    </div>
  );
}

function DocumentPage() {
  const { workspace } = documentRoute.useRouteContext();
  const { documentId } = documentRoute.useParams();
  const { data, error, isPending } = useQuery(documentQuery(workspace.id, documentId));
  const unknown = t`Onbekende afzender`;
  const title = data ? data.sender || unknown : t`Brief`;
  usePageTitle(title);

  return (
    <Shell workspace={workspace}>
      {/* Always there, so the focus after a route change has a heading to land on. */}
      <PageHeader
        title={title}
        breadcrumb={{
          to: "/w/$workspaceId/post",
          params: { workspaceId: workspace.id },
          label: t`Alle post`,
        }}
      />
      {isPending ? <Loading /> : null}
      {error instanceof ApiFailure && error.code === "document_in_trash" ? (
        <MessageBar intent="info">
          {t`Deze brief staat in de prullenbak.`}{" "}
          <Link to="/w/$workspaceId/prullenbak" params={{ workspaceId: workspace.id }}>
            {t`Naar de prullenbak`}
          </Link>
        </MessageBar>
      ) : (
        <ErrorAlert error={error} />
      )}
      {data ? (
        <DocumentView
          workspaceId={workspace.id}
          document={data}
          canEdit={workspace.role !== "viewer"}
        />
      ) : null}
    </Shell>
  );
}

export const documentRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "post/$documentId",
  component: DocumentPage,
});
