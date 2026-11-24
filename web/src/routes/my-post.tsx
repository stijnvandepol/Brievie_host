import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createRoute, useNavigate } from "@tanstack/react-router";
import { Button } from "react-aria-components";
import {
  documentsQuery,
  labelsQuery,
  menuCountsQuery,
  personsQuery,
  visibilityQuery,
} from "../api/queries";
import { markManyRead, refreshAfterReading } from "../api/reading";
import { DocumentTable } from "../components/document-table";
import { Icon } from "../components/icons";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import { Loading, MessageBar } from "../components/ui";
import { workspaceRoute } from "./workspace";

type Search = { ongelezen?: true; afdeling?: true };

/**
 * "Mijn post" (spec S 4.4, S 5.4; decisions B1, B2): the letters on the signed-in person's
 * own name, the start page of a workspace. Unread letters are bold with a dot; "Alleen
 * ongelezen" narrows to them, "Alles gelezen" marks the rows on screen read, and where
 * departments (home: groups) are on, "Ook mijn afdeling" adds their post.
 */
function MyPostPage() {
  usePageTitle(t`Mijn post`);
  const { workspace } = myPostRoute.useRouteContext();
  const { ongelezen, afdeling } = myPostRoute.useSearch();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const persons = useQuery(personsQuery(workspace.id));
  const labels = useQuery(labelsQuery(workspace.id));
  const sight = useQuery(visibilityQuery(workspace.id));
  const counts = useQuery(menuCountsQuery(workspace.id));
  const departments = sight.data?.departments_enabled === true;
  const { data, error, isPending } = useQuery(
    documentsQuery(workspace.id, {
      addressed_to_me: true,
      include_departments: departments && afdeling === true,
      unread_only: ongelezen === true,
    }),
  );
  const unread = (data ?? []).filter((document) => document.unread).map((d) => d.id);
  const readAll = useMutation({
    mutationFn: () => markManyRead(workspace.id, unread),
    onSuccess: () => refreshAfterReading(queryClient, workspace.id),
  });

  function go(next: Search) {
    void navigate({
      to: "/w/$workspaceId/mijn-post",
      params: { workspaceId: workspace.id },
      search: next,
    });
  }

  const groupWord = sight.data?.kind === "home" ? t`Ook mijn groep` : t`Ook mijn afdeling`;
  const noPerson = counts.data?.has_person === false;
  return (
    <Shell workspace={workspace}>
      <PageHeader
        title={t`Mijn post`}
        description={t`De brieven op jouw naam. Wat nog nagekeken of verdeeld moet worden, staat in de Inbox.`}
        commands={
          <Button
            className="button secondary"
            isDisabled={unread.length === 0 || readAll.isPending}
            onPress={() => readAll.mutate()}
          >
            <Icon name="checkmark" />
            {t`Alles gelezen`}
          </Button>
        }
      />
      {noPerson ? (
        <MessageBar intent="info">
          {t`Je account hoort nog bij geen persoon in deze werkruimte, dus er staat hier geen post. Vraag een beheerder om je te koppelen bij Mensen.`}
        </MessageBar>
      ) : null}
      <div className="filter-bar">
        <div className="check filter-check">
          <input
            id="filter-unread"
            type="checkbox"
            checked={ongelezen === true}
            onChange={(event) =>
              go({
                ...(event.target.checked ? { ongelezen: true } : {}),
                ...(afdeling ? { afdeling } : {}),
              })
            }
          />
          <label htmlFor="filter-unread">{t`Alleen ongelezen`}</label>
        </div>
        {departments ? (
          <div className="check filter-check">
            <input
              id="filter-department"
              type="checkbox"
              checked={afdeling === true}
              onChange={(event) =>
                go({
                  ...(ongelezen ? { ongelezen } : {}),
                  ...(event.target.checked ? { afdeling: true } : {}),
                })
              }
            />
            <label htmlFor="filter-department">{groupWord}</label>
          </div>
        ) : null}
      </div>
      {isPending ? <Loading /> : null}
      <ErrorAlert error={error ?? persons.error ?? labels.error ?? readAll.error} />
      {data ? (
        <DocumentTable
          workspaceId={workspace.id}
          documents={data}
          persons={persons.data ?? []}
          labels={labels.data ?? []}
          caption={t`Mijn post`}
          showUnread
          letterStatus="all"
          empty={ongelezen ? t`Je hebt alles gelezen.` : t`Er is nog geen post op jouw naam.`}
          hint={
            ongelezen
              ? t`Zet Alleen ongelezen uit om al je post te zien.`
              : t`Post op jouw naam staat hier zodra hij is gescand en verdeeld.`
          }
        />
      ) : null}
    </Shell>
  );
}

export const myPostRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "mijn-post",
  validateSearch: (search: Record<string, unknown>): Search => ({
    ...(search.ongelezen === true || search.ongelezen === "true"
      ? { ongelezen: true as const }
      : {}),
    ...(search.afdeling === true || search.afdeling === "true" ? { afdeling: true as const } : {}),
  }),
  component: MyPostPage,
});
