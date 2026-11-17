import { t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { createRoute, Link } from "@tanstack/react-router";
import { documentQuery, labelsQuery, personsQuery } from "../api/queries";
import type { components } from "../api/schema";
import { DocumentHistory } from "../components/document-history";
import { formatLetterDate, statusLabel, typeLabel } from "../components/document-table";
import { LabelPicker, useSetLabels } from "../components/label-picker";
import { ErrorAlert, Shell, usePageTitle } from "../components/page";
import { PersonPicker, useSetAddressees } from "../components/person-picker";
import { SuggestionList } from "../components/suggestion-list";
import { TaskList } from "../components/task-list";
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
    return <p className="notice">{t`Deze brief wordt nog gelezen.`}</p>;
  }
  if (document.status === "needs_review" || document.status === "failed") {
    return <p className="notice">{t`Controleer deze brief in de app.`}</p>;
  }
  return null;
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
              <dd>{statusLabel(document.status)}</dd>
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
              </dd>
            </div>
          </dl>
          <ErrorAlert
            error={setAddressees.error ?? setLabels.error ?? persons.error ?? labels.error}
          />
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
            <TaskList workspaceId={workspaceId} tasks={document.tasks} canEdit={canEdit} />
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
      <p className="back-link">
        <Link to="/w/$workspaceId/post" params={{ workspaceId: workspace.id }}>
          {t`Terug naar Post`}
        </Link>
      </p>
      {/* Always there, so the focus after a route change has a heading to land on. */}
      <header className="page-header">
        <h1>{title}</h1>
      </header>
      {isPending ? <p>{t`Even laden...`}</p> : null}
      <ErrorAlert error={error} />
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
