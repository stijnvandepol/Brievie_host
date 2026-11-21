import { t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { createRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";
import { ApiFailure } from "../api/client";
import { workspacesQuery } from "../api/queries";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import { Badge, EmptyState, Loading } from "../components/ui";
import { kindLabel, roleLabel } from "../components/workspace-labels";
import { requireUser } from "../guards";
import { rootRoute } from "./root";

/** "Alle werkruimtes" (spec S 4.4): every workspace of the user with its kind and role,
 * reached from the switcher. Also with one workspace: this page never bounces. */
function WorkspacesPage() {
  usePageTitle(t`Werkruimtes`);
  const { data, error, isPending } = useQuery(workspacesQuery);
  const navigate = useNavigate();

  // The session ended while this page was open.
  useEffect(() => {
    if (error instanceof ApiFailure && error.code === "not_authenticated") {
      void navigate({ to: "/login" });
    }
  }, [error, navigate]);

  return (
    <Shell>
      <PageHeader
        title={t`Werkruimtes`}
        description={t`Een werkruimte heeft eigen post en eigen leden, bijvoorbeeld Thuis of je bedrijf. Kies welke je wilt openen.`}
      />
      {isPending ? <Loading /> : null}
      <ErrorAlert error={error} />
      {data && data.length === 0 ? (
        <EmptyState icon="grid" hint={t`Vraag een beheerder om een uitnodiging.`}>
          {t`Je hoort nog bij geen werkruimte.`}
        </EmptyState>
      ) : null}
      {data && data.length > 0 ? (
        // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrolling box must be reachable by keyboard (WCAG 2.1.1).
        <section className="panel table-scroll" aria-label={t`Werkruimtes`} tabIndex={0}>
          <div>
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">{t`Naam`}</th>
                  <th scope="col">{t`Soort`}</th>
                  <th scope="col">{t`Rol`}</th>
                </tr>
              </thead>
              <tbody>
                {data.map((workspace) => (
                  <tr key={workspace.id}>
                    <th scope="row" className="cell-strong">
                      <Link to="/w/$workspaceId/mijn-post" params={{ workspaceId: workspace.id }}>
                        {workspace.name}
                      </Link>
                    </th>
                    <td>{kindLabel(workspace.kind)}</td>
                    <td>
                      <Badge>{roleLabel(workspace.role)}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </Shell>
  );
}

export const workspacesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/werkruimtes",
  beforeLoad: ({ context }) => requireUser(context.queryClient),
  component: WorkspacesPage,
});
