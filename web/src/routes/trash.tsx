import { plural, t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Button } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { trashQuery } from "../api/queries";
import type { components } from "../api/schema";
import { emptyTrash, purgeDocument, refreshAfterTrash, restoreDocument } from "../api/trash";
import { ConfirmDialog } from "../components/confirm-dialog";
import { isRetained } from "../components/document-details-form";
import {
  formatLetterDate,
  LetterDateText,
  SenderText,
  TypeText,
} from "../components/document-table";
import { Icon } from "../components/icons";
import { ErrorAlert, focusPageHeading, PageHeader, Shell, usePageTitle } from "../components/page";
import { EmptyState, Loading, MessageBar } from "../components/ui";
import { dateFormat } from "../format";
import { workspaceRoute } from "./workspace";

type Trashed = components["schemas"]["TrashedDocumentOut"];

/** The trash list holds at most this many letters (the server's cap). */
const LIST_CAP = 500;

const longDate = dateFormat({ dateStyle: "long" });

/** Calendar days from today until `purgeAfter`, in the viewer's own time zone. */
export function daysLeft(purgeAfter: string, now: Date = new Date()): number {
  const end = new Date(purgeAfter);
  const startDay = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  const endDay = Date.UTC(end.getFullYear(), end.getMonth(), end.getDate());
  return Math.round((endDay - startDay) / 86_400_000);
}

function DaysLeft({ purgeAfter }: { purgeAfter: string }) {
  const days = daysLeft(purgeAfter);
  if (days <= 0) return <>{t`Wordt vandaag gewist`}</>;
  return <>{plural(days, { one: "Nog # dag", other: "Nog # dagen" })}</>;
}

function TrashedLine({ letter }: { letter: Trashed }) {
  const when = longDate.format(new Date(letter.trashed_at));
  const who = letter.trashed_by_name ?? "";
  return <>{who ? t`Verwijderd op ${when} door ${who}` : t`Verwijderd op ${when}`}</>;
}

/** The sender as plain text, for a button's accessible name. */
function senderName(letter: Trashed): string {
  return letter.sender || letter.proposed_sender || t`Onbekende afzender`;
}

type Question = { kind: "purge"; letter: Trashed } | { kind: "empty" } | null;

function TrashPage() {
  usePageTitle(t`Prullenbak`);
  const { workspace } = trashRoute.useRouteContext();
  const queryClient = useQueryClient();
  const trash = useQuery(trashQuery(workspace.id));
  const [question, setQuestion] = useState<Question>(null);
  const [restoredOne, setRestoredOne] = useState(false);
  const canRestore = workspace.role !== "viewer";
  const isAdmin = workspace.role === "admin";
  const items = trash.data?.items ?? [];
  const days = trash.data?.retention_days ?? 30;

  async function settle() {
    await refreshAfterTrash(queryClient, workspace.id);
    // The row and its button are gone: the focus goes to the page's title.
    focusPageHeading();
  }

  // "Bewaren tot" stops a purge; an admin may take the date away here (security review of
  // PR #54, point 3), and so may the person it belongs to (`can_clear_retention`), so the
  // purge (and an erasure request) is never blocked.
  const unpin = useMutation({
    mutationFn: async (documentId: string) => {
      const { error } = await api.PATCH(
        "/api/v1/workspaces/{workspace_id}/documents/{document_id}/details",
        {
          params: { path: { workspace_id: workspace.id, document_id: documentId } },
          body: {
            title: "",
            document_type: "",
            document_date: null,
            expires_on: null,
            clear_expires_on: false,
            remind_days: 0,
            retain_until: null,
            clear_retain_until: true,
          },
        },
      );
      if (error !== undefined) throw new ApiFailure(error);
    },
    onSuccess: () => refreshAfterTrash(queryClient, workspace.id),
  });

  const restore = useMutation({
    mutationFn: (documentId: string) => restoreDocument(workspace.id, documentId),
    onMutate: () => setRestoredOne(false),
    onSuccess: async () => {
      setRestoredOne(true);
      await settle();
    },
  });

  // Ask with the newest letter the admin was shown: one trashed meanwhile stays.
  const upTo = items.reduce<string | null>(
    (newest, letter) =>
      newest === null || letter.trashed_at > newest ? letter.trashed_at : newest,
    null,
  );
  const count = items.length;
  // Every letter the empty takes, also beyond the listed ones (the list stops at LIST_CAP).
  const emptyCount = Math.max(trash.data?.total ?? 0, count);

  return (
    <Shell workspace={workspace}>
      <PageHeader
        title={t`Prullenbak`}
        description={t`Brieven in de prullenbak worden na ${days} dagen voorgoed gewist. Tot die tijd kun je ze terugzetten.`}
        commands={
          isAdmin && count > 0 ? (
            <Button
              className="button-text danger"
              onPress={() => {
                setRestoredOne(false);
                setQuestion({ kind: "empty" });
              }}
            >
              <Icon name="delete" />
              {t`Prullenbak legen`}
            </Button>
          ) : null
        }
      />
      {isAdmin ? (
        <MessageBar intent="info">
          {t`Brievie houdt nog geen bewaartermijn bij. Moet je een brief bewaren, bijvoorbeeld een factuur voor de belasting? Let daar dan zelf op voordat je hem wist.`}
        </MessageBar>
      ) : null}
      {restoredOne ? (
        <MessageBar intent="success" role="status">
          {t`De brief is teruggezet.`}
        </MessageBar>
      ) : null}
      {trash.isPending ? <Loading /> : null}
      <ErrorAlert error={trash.error ?? restore.error ?? unpin.error} />
      {trash.data && count === 0 ? (
        <EmptyState icon="delete">{t`De prullenbak is leeg.`}</EmptyState>
      ) : null}
      {count > 0 ? (
        // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrolling box must be reachable by keyboard (WCAG 2.1.1).
        <section className="panel table-scroll" aria-label={t`Prullenbak`} tabIndex={0}>
          <table className="table">
            <thead>
              <tr>
                <th scope="col">{t`Afzender`}</th>
                <th scope="col">{t`Soort`}</th>
                <th scope="col">{t`Datum brief`}</th>
                <th scope="col">
                  <span className="visually-hidden">{t`Acties`}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((letter) => {
                const name = senderName(letter);
                return (
                  <tr key={letter.id}>
                    <th scope="row" className="cell-strong">
                      <SenderText document={letter} />
                      <span className="muted small block">
                        <TrashedLine letter={letter} />
                      </span>
                      <span className="muted small block">
                        <DaysLeft purgeAfter={letter.purge_after} />
                      </span>
                      {isRetained(letter.retain_until) && letter.retain_until ? (
                        <span className="muted small block">
                          {t`Bewaren tot ${formatLetterDate(letter.retain_until)}: niet definitief te wissen.`}
                        </span>
                      ) : null}
                    </th>
                    <td>
                      <TypeText document={letter} />
                    </td>
                    <td>
                      <LetterDateText document={letter} />
                    </td>
                    <td className="cell-action">
                      {canRestore ? (
                        <span className="row-actions">
                          <Button
                            className="button-text"
                            aria-label={t`Terugzetten: ${name}`}
                            isDisabled={restore.isPending}
                            onPress={() => restore.mutate(letter.id)}
                          >
                            <Icon name="restore" />
                            {t`Terugzetten`}
                          </Button>
                          {letter.can_clear_retention && isRetained(letter.retain_until) ? (
                            <Button
                              className="button-text"
                              aria-label={t`Bewaren tot weghalen: ${name}`}
                              isDisabled={unpin.isPending}
                              onPress={() => unpin.mutate(letter.id)}
                            >
                              {t`Bewaren tot weghalen`}
                            </Button>
                          ) : null}
                          {isAdmin ? (
                            <Button
                              className="button-text danger"
                              aria-label={t`Definitief verwijderen: ${name}`}
                              onPress={() => {
                                setRestoredOne(false);
                                setQuestion({ kind: "purge", letter });
                              }}
                            >
                              <Icon name="delete" />
                              {t`Definitief verwijderen`}
                            </Button>
                          ) : null}
                        </span>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      ) : null}
      {count >= LIST_CAP ? (
        <p className="muted small">{t`Alleen de nieuwste ${LIST_CAP} worden getoond.`}</p>
      ) : null}
      <ConfirmDialog
        isOpen={question?.kind === "purge"}
        onOpenChange={(open) => {
          if (!open) setQuestion(null);
        }}
        title={t`Brief voorgoed wissen?`}
        confirmLabel={t`Definitief verwijderen`}
        onConfirm={async () => {
          if (question?.kind !== "purge") return;
          await purgeDocument(workspace.id, question.letter.id);
          await settle();
        }}
      >
        <p>{t`De brief en de gescande pagina's worden voorgoed uit Brievie gewist. Dit kun je niet ongedaan maken. Een back-up die eerder is gemaakt, heeft de brief nog. Daar blijft hij in tot die back-up verloopt.`}</p>
      </ConfirmDialog>
      <ConfirmDialog
        isOpen={question?.kind === "empty"}
        onOpenChange={(open) => {
          if (!open) setQuestion(null);
        }}
        title={t`Prullenbak legen?`}
        confirmLabel={t`Prullenbak legen`}
        onConfirm={async () => {
          await emptyTrash(workspace.id, upTo);
          await settle();
        }}
      >
        <p>
          {plural(emptyCount, {
            one: "# brief in de prullenbak wordt voorgoed uit Brievie gewist. Dit kun je niet ongedaan maken. Een back-up die eerder is gemaakt, heeft deze brief nog. Daar blijft hij in tot die back-up verloopt.",
            other:
              "# brieven in de prullenbak worden voorgoed uit Brievie gewist. Dit kun je niet ongedaan maken. Een back-up die eerder is gemaakt, heeft deze brieven nog. Daar blijven ze in tot die back-up verloopt.",
          })}
        </p>
      </ConfirmDialog>
    </Shell>
  );
}

export const trashRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "prullenbak",
  component: TrashPage,
});
