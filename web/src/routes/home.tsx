import { t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { createRoute, useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";
import { ApiFailure } from "../api/client";
import { workspacesQuery } from "../api/queries";
import { ErrorAlert, Shell, usePageTitle } from "../components/page";
import { requireUser } from "../guards";
import { rootRoute } from "./root";

function kindLabel(kind: string): string {
  if (kind === "home") return t`Thuis`;
  if (kind === "business") return t`Zakelijk`;
  return kind;
}

function roleLabel(role: string): string {
  if (role === "admin") return t`Beheerder`;
  if (role === "member") return t`Lid`;
  if (role === "viewer") return t`Meekijker`;
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

  return (
    <Shell>
      <header className="page-header">
        <h1>{t`Werkruimtes`}</h1>
      </header>
      {isPending ? <p>{t`Even laden...`}</p> : null}
      <ErrorAlert error={error} />
      {data ? (
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
                      {workspace.name}
                    </th>
                    <td>{kindLabel(workspace.kind)}</td>
                    <td>
                      <span className="tag">{roleLabel(workspace.role)}</span>
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
