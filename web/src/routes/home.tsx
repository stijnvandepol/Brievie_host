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
      <h1>{t`Werkruimtes`}</h1>
      {isPending ? <p>{t`Even laden...`}</p> : null}
      <ErrorAlert error={error} />
      {data ? (
        <ul className="list">
          {data.map((workspace) => (
            <li key={workspace.id}>
              <strong>{workspace.name}</strong>
              <span>{kindLabel(workspace.kind)}</span>
            </li>
          ))}
        </ul>
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
