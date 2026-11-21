import { t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { createRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";
import { ApiFailure } from "../api/client";
import { workspacesQuery } from "../api/queries";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import { Badge, Loading } from "../components/ui";
import { requireUser } from "../guards";
import { rootRoute } from "./root";

function kindLabel(kind: string): string {
  if (kind === "home") return t`Thuis`;
  if (kind === "business") return t`Organisatie`;
  return kind;
}

function roleLabel(role: string): string {
  if (role === "admin") return t`Beheerder`;
  if (role === "member") return t`Lid`;
  if (role === "viewer") return t`Alleen lezen`;
  return role;
}

function HomePage() {
  usePageTitle(t`Werkruimtes`);
  const { data, error, isPending } = useQuery(workspacesQuery);
  const navigate = useNavigate();

  // The session ended while this page was open.
  useEffect(() => {
    if (error instanceof ApiFailure && error.code === "not_authenticated") {
      void navigate({ to: "/login" });
    }
  }, [error, navigate]);

  // With one workspace there is nothing to choose.
  const only = data?.length === 1 ? data[0] : undefined;
  useEffect(() => {
    if (only) {
      void navigate({
        to: "/w/$workspaceId/inbox",
        params: { workspaceId: only.id },
        replace: true,
      });
    }
  }, [only, navigate]);

  return (
    <Shell>
      <PageHeader
        title={t`Werkruimtes`}
        description={t`Een werkruimte heeft eigen post en eigen leden, bijvoorbeeld Thuis of je bedrijf. Kies welke je wilt openen.`}
      />
      {isPending || only ? <Loading /> : null}
      <ErrorAlert error={error} />
      {data && !only ? (
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
                      <Link to="/w/$workspaceId/inbox" params={{ workspaceId: workspace.id }}>
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

export const homeRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: ({ context }) => requireUser(context.queryClient),
  component: HomePage,
});
