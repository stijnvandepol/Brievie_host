import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { Button } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import {
  documentQuery,
  labelsQuery,
  membersQuery,
  meQuery,
  personsQuery,
  visibilityQuery,
} from "../api/queries";
import { markRead, markUnread, refreshAfterReading } from "../api/reading";
import type { components } from "../api/schema";
import { refreshAfterTrash, trashDocument } from "../api/trash";
import { ConfirmAction, ConfirmDialog } from "../components/confirm-dialog";
import { DocumentAccess } from "../components/document-access";
import { DocumentDetails, isRetained } from "../components/document-details-form";
import { DocumentHistory } from "../components/document-history";
import {
  ArchiveBadges,
  AutomaticBadge,
  formatLetterDate,
  RowStatus,
  typeLabel,
} from "../components/document-table";
import { handOverSentence, losesLetter, ownSight } from "../components/hand-over";
import { HandleDialog } from "../components/handle-dialog";
import { Icon } from "../components/icons";
import { LabelPicker, useSetLabels } from "../components/label-picker";
import { ErrorAlert, focusPageHeading, PageHeader, Shell, usePageTitle } from "../components/page";
import { PersonPicker, useSetAddressees } from "../components/person-picker";
import { PrivateLock } from "../components/private-lock";
import { ShareDialog, useSetVisibility } from "../components/share-dialog";
import { NewPersonList, SuggestionList } from "../components/suggestion-list";
import { TaskList } from "../components/task-list";
import { Loading, MessageBar } from "../components/ui";
import { showLetterGone } from "../lost-letter";
import { showUndo } from "../undo-trash";
import { departmentsTitle } from "./departments";
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

/** An own document whose text was not read (archive spec S 6.2 point 5): it is in the
 * archive anyway; an editor may have it read again. */
function FailedTextNotice({
  workspaceId,
  document,
  canEdit,
}: {
  workspaceId: string;
  document: Document;
  canEdit: boolean;
}) {
  const queryClient = useQueryClient();
  const retry = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST(
        "/api/v1/workspaces/{workspace_id}/documents/{document_id}/retry",
        { params: { path: { workspace_id: workspaceId, document_id: document.id } } },
      );
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: (answer) => {
      queryClient.setQueryData(["workspaces", workspaceId, "documents", document.id], answer);
    },
  });
  return (
    <>
      <MessageBar intent="warning">
        {t`De tekst is niet gelezen. Het document staat wel in het archief.`}{" "}
        {canEdit ? (
          <Button
            className="button-text"
            isDisabled={retry.isPending}
            onPress={() => retry.mutate()}
          >
            {t`Opnieuw proberen`}
          </Button>
        ) : null}
      </MessageBar>
      <ErrorAlert error={retry.error} />
    </>
  );
}

function StatusNotice({ document }: { document: Document }) {
  if (document.kind === "document") {
    return document.status === "processing" ? (
      <MessageBar intent="info">{t`Dit document wordt nog gelezen.`}</MessageBar>
    ) : null;
  }
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
 * Moves the letter to the trash, then goes to "Archief" with the undo bar. The letter's own
 * cache entry goes: it answers `document_in_trash` from now on.
 */
function useTrash(workspaceId: string, documentId: string) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  return useMutation({
    mutationFn: () => trashDocument(workspaceId, documentId),
    onSuccess: async () => {
      showUndo(workspaceId, documentId);
      await navigate({ to: "/w/$workspaceId/archief", params: { workspaceId } });
      queryClient.removeQueries({ queryKey: ["workspaces", workspaceId, "documents", documentId] });
      await refreshAfterTrash(queryClient, workspaceId);
    },
  });
}

/** After a change the caller can no longer read the letter: back to the Inbox, which says so. */
export function useLetterGone(workspaceId: string) {
  const navigate = useNavigate();
  return () => {
    showLetterGone(workspaceId);
    void navigate({ to: "/w/$workspaceId/inbox", params: { workspaceId } });
  };
}

function DocumentView({
  workspaceId,
  document,
  canEdit,
  role,
}: {
  workspaceId: string;
  document: Document;
  canEdit: boolean;
  role: string;
}) {
  const persons = useQuery(personsQuery(workspaceId));
  const labels = useQuery(labelsQuery(workspaceId));
  const visibility = useQuery(visibilityQuery(workspaceId));
  const members = useQuery(membersQuery(workspaceId));
  const me = useQuery(meQuery);
  const setAddressees = useSetAddressees(workspaceId, document.id);
  const gone = useLetterGone(workspaceId);
  const [handOver, setHandOver] = useState<string[] | null>(null);
  const [sharing, setSharing] = useState<"private" | "share" | null>(null);
  const [handling, setHandling] = useState(false);
  const setVisibility = useSetVisibility(workspaceId, document.id);
  const restricted = document.visibility === "restricted";
  async function makePublic() {
    const answer = await setVisibility.mutateAsync({
      visibility: "workspace",
      shared_user_ids: document.shared_user_ids ?? [],
    });
    if (answer.can_read === false) gone();
  }
  const departmentsOn = visibility.data?.departments_enabled === true;
  const sight = ownSight(
    visibility.data,
    role,
    me.data?.id,
    members.data ?? [],
    persons.data ?? [],
  );
  async function saveAddressees(ids: string[]) {
    const answer = await setAddressees.mutateAsync(ids);
    if (answer.can_read === false) gone();
  }
  function chooseAddressees(ids: string[]) {
    if (
      document.visibility !== "restricted" &&
      losesLetter(sight, ids, persons.data ?? [], departmentsOn)
    ) {
      setHandOver(ids);
      return;
    }
    saveAddressees(ids).catch(() => {
      // Shown in the panel (setAddressees.error).
    });
  }
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
  const own = document.kind === "document";
  const retained = isRetained(document.retain_until);
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
        {own && document.status === "done" && document.failure_code ? (
          <FailedTextNotice workspaceId={workspaceId} document={document} canEdit={canEdit} />
        ) : null}
        <PrivateLock
          visibility={document.visibility}
          readerNames={document.reader_names}
          variant="page"
        />
        <section className="panel" aria-label={t`Gegevens`}>
          {own ? (
            <DocumentDetails workspaceId={workspaceId} document={document} canEdit={canEdit} />
          ) : null}
          <dl className="detail-list">
            {own ? null : (
              <>
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
              </>
            )}
            <div>
              <dt>{t`Status`}</dt>
              <dd>
                <span className="cell-badges">
                  {own ? (
                    <ArchiveBadges document={document} />
                  ) : (
                    <RowStatus document={document} departments={departmentsOn} letterStatus="all" />
                  )}
                  <AutomaticBadge document={document} />
                </span>
              </dd>
            </div>
            <div>
              <dt>{own ? t`Van wie` : t`Op naam van`}</dt>
              <dd>
                <PersonPicker
                  persons={persons.data ?? []}
                  selectedIds={document.addressee_ids ?? []}
                  onChange={chooseAddressees}
                  disabled={!canEdit || document.status === "processing" || setAddressees.isPending}
                  newPersonIn={canEdit ? workspaceId : undefined}
                  departments={
                    departmentsOn
                      ? departmentsTitle(visibility.data?.kind ?? "business")
                      : undefined
                  }
                />
                {sight && handOver ? (
                  <ConfirmDialog
                    isOpen
                    onOpenChange={(open) => {
                      if (!open) setHandOver(null);
                    }}
                    title={t`Brief aan iemand anders geven?`}
                    confirmLabel={t`Opslaan`}
                    tone="primary"
                    onConfirm={() => saveAddressees(handOver)}
                  >
                    <p>
                      {handOverSentence(
                        sight,
                        document.addressee_ids ?? [],
                        handOver,
                        persons.data ?? [],
                      )}
                    </p>
                  </ConfirmDialog>
                ) : null}
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
              {restricted ? null : (
                <Button className="button-text" onPress={() => setSharing("private")}>
                  <Icon name="lock" />
                  {t`Privé maken`}
                </Button>
              )}
              <Button className="button-text" onPress={() => setSharing("share")}>
                <Icon name="share" />
                {restricted ? t`Delen` : t`Delen met`}
              </Button>
              {restricted && document.can_make_public ? (
                <ConfirmAction
                  label={t`Niet meer privé`}
                  icon="lock"
                  title={t`Niet meer privé maken?`}
                  confirmLabel={t`Niet meer privé`}
                  tone="primary"
                  onConfirm={makePublic}
                >
                  <p>{t`Wie deze brief mag zien, volgt daarna weer de instellingen van Wie ziet wat. Met wie je hem deelde, houdt hem.`}</p>
                </ConfirmAction>
              ) : null}
              {/* Primary, not danger: the letter can be put back for 30 days. */}
              <ConfirmAction
                label={t`Verwijderen`}
                icon="delete"
                title={
                  retained && document.retain_until
                    ? t`Bewaren tot ${formatLetterDate(document.retain_until)}. Weet je het zeker?`
                    : own
                      ? t`Document verwijderen?`
                      : t`Brief verwijderen?`
                }
                confirmLabel={t`Naar de prullenbak`}
                tone="primary"
                onConfirm={() => trash.mutateAsync()}
              >
                {retained ? (
                  <p>{t`Je kunt het naar de prullenbak verplaatsen. Voorgoed wissen kan pas als de datum voorbij is of iemand de datum weghaalt.`}</p>
                ) : null}
                <p>
                  {own
                    ? t`Het document gaat naar de prullenbak. Daar blijft het 30 dagen. Zo lang kun je het terugzetten. Daarna wordt het voorgoed gewist.`
                    : t`De brief gaat naar de prullenbak. Daar blijft hij 30 dagen. Zo lang kun je hem terugzetten. Daarna wordt hij voorgoed gewist.`}
                </p>
              </ConfirmAction>
            </div>
          ) : null}
        </section>
        <DocumentOrigin workspaceId={workspaceId} document={document} />
        <SuggestionList
          workspaceId={workspaceId}
          document={document}
          persons={persons.data ?? []}
          labels={labels.data ?? []}
          canEdit={canEdit}
        />
        <NewPersonList workspaceId={workspaceId} document={document} canEdit={canEdit} />
        {own ? null : (
          <section className="panel" aria-label={t`Bewaren`}>
            <DocumentDetails workspaceId={workspaceId} document={document} canEdit={canEdit} />
          </section>
        )}
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
              {canEdit &&
              (document.letter_status === "open" || document.letter_status === "waiting") ? (
                <Button className="button secondary" onPress={() => setHandling(true)}>
                  <Icon name="checkmark" />
                  {t`Brief afhandelen`}
                </Button>
              ) : null}
            </div>
            <TaskList
              workspaceId={workspaceId}
              documentId={document.id}
              tasks={document.tasks}
              canEdit={canEdit}
            />
          </section>
        ) : null}
        <HandleDialog
          workspaceId={workspaceId}
          documentId={document.id}
          tasks={document.tasks}
          isOpen={handling}
          onOpenChange={setHandling}
        />
        {sharing ? (
          <ShareDialog
            workspaceId={workspaceId}
            document={document}
            mode={sharing}
            onClose={() => setSharing(null)}
            onGone={gone}
          />
        ) : null}
        <DocumentAccess workspaceId={workspaceId} documentId={document.id} />
        <DocumentHistory workspaceId={workspaceId} documentId={document.id} canEdit={canEdit} />
        <details className="panel letter-text">
          <summary>{own ? t`Tekst van het document` : t`Tekst van de brief`}</summary>
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

/**
 * "Ongelezen" on the letter page (spec S 5.3): the letter counts as read once this page
 * shows it (one call per opening, never on a prefetch or a refetch), and "Markeer als
 * ongelezen" / "Markeer als gelezen" change the caller's own mark.
 */
function useReadMark(workspaceId: string, document: Document | undefined) {
  const queryClient = useQueryClient();
  const marked = useRef<string | null>(null);
  const shown = document?.id;
  useEffect(() => {
    if (!shown || marked.current === shown) return;
    marked.current = shown;
    void markRead(workspaceId, shown)
      .then(() => {
        queryClient.setQueryData(documentQuery(workspaceId, shown).queryKey, (old) =>
          old ? { ...old, unread: false } : old,
        );
        return refreshAfterReading(queryClient, workspaceId, shown);
      })
      .catch(() => {
        // The letter's own answer says what is wrong with it; a lost mark is no error.
      });
  }, [shown, workspaceId, queryClient]);
  return useMutation({
    mutationFn: (read: boolean) =>
      read ? markRead(workspaceId, shown ?? "") : markUnread(workspaceId, shown ?? ""),
    onSuccess: () => refreshAfterReading(queryClient, workspaceId),
  });
}

function DocumentPage() {
  const { workspace } = documentRoute.useRouteContext();
  const { documentId } = documentRoute.useParams();
  const { data, error, isPending } = useQuery(documentQuery(workspace.id, documentId));
  const unknown = t`Onbekende afzender`;
  const title = data
    ? data.kind === "document"
      ? data.title || t`Document zonder titel`
      : data.sender || unknown
    : t`Brief`;
  usePageTitle(title);
  const reading = useReadMark(workspace.id, data);

  return (
    <Shell workspace={workspace}>
      {/* Always there, so the focus after a route change has a heading to land on. */}
      <PageHeader
        title={title}
        breadcrumb={{
          to: "/w/$workspaceId/archief",
          params: { workspaceId: workspace.id },
          label: t`Archief`,
        }}
        commands={
          data && data.kind !== "document" ? (
            <Button
              className="button secondary"
              isDisabled={reading.isPending}
              onPress={() => reading.mutate(data.unread === true)}
            >
              <Icon name="mail" />
              {data.unread ? t`Markeer als gelezen` : t`Markeer als ongelezen`}
            </Button>
          ) : undefined
        }
      />
      <ErrorAlert error={reading.error} />
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
          role={workspace.role}
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

/** How the letter arrived, and its original file when a browser or the scan folder sent one
 * (intake spec S 2). The download is the server's: session-only, never cached. */
function DocumentOrigin({
  workspaceId,
  document,
}: {
  workspaceId: string;
  document: { id: string; source?: string; has_original?: boolean };
}) {
  const source =
    document.source === "upload"
      ? t`Binnengekomen via upload`
      : document.source === "watch_folder"
        ? t`Binnengekomen via de scanmap`
        : "";
  if (!source && !document.has_original) return null;
  return (
    <section className="panel" aria-label={t`Herkomst`}>
      {source ? <p className="muted small">{source}</p> : null}
      {document.has_original ? (
        <a
          className="button"
          href={`/api/v1/workspaces/${workspaceId}/documents/${document.id}/original`}
          download
        >
          {t`Origineel downloaden`}
        </a>
      ) : null}
    </section>
  );
}
