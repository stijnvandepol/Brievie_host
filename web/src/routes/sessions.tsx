import { t } from "@lingui/core/macro";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createRoute, useNavigate } from "@tanstack/react-router";
import { ApiFailure, api } from "../api/client";
import { mySessionsQuery } from "../api/queries";
import type { components } from "../api/schema";
import { ConfirmAction } from "../components/confirm-dialog";
import { ErrorAlert, focusPageHeading, PageHeader, Shell, usePageTitle } from "../components/page";
import { Badge, Loading } from "../components/ui";
import { requireUser } from "../guards";
import { rootRoute } from "./root";

type Session = components["schemas"]["SessionOut"];

const dateTime = new Intl.DateTimeFormat("nl-NL", { dateStyle: "medium", timeStyle: "short" });

function methodLabel(method: string): string {
  if (method === "password") return t`Wachtwoord`;
  if (method === "sso") return t`Inloggen via organisatie`;
  return method;
}

function SessionRow({ session }: { session: Session }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  async function end() {
    const { error, response } = await api.DELETE("/api/v1/me/sessions/{session_id}", {
      params: { path: { session_id: session.id } },
    });
    if (!response.ok) throw new ApiFailure(error);
    if (session.current) {
      // This browser is signed out: forget everything and go to the sign-in page.
      queryClient.clear();
      await navigate({ to: "/login" });
      return;
    }
    await queryClient.invalidateQueries({ queryKey: mySessionsQuery.queryKey });
    focusPageHeading();
  }

  return (
    <tr>
      <th scope="row" className="cell-strong">
        <span className="cell-with-badge">
          {session.browser || t`Onbekende browser`}
          {session.current ? <Badge intent="brand">{t`Deze browser`}</Badge> : null}
        </span>
      </th>
      <td>{methodLabel(session.method)}</td>
      <td>{session.client_ip}</td>
      <td className="num">
        <time dateTime={session.started_at}>{dateTime.format(new Date(session.started_at))}</time>
      </td>
      <td className="num">
        <time dateTime={session.last_used_at}>
          {dateTime.format(new Date(session.last_used_at))}
        </time>
      </td>
      <td className="cell-action">
        <ConfirmAction
          label={t`Afmelden`}
          ariaLabel={t`Afmelden: ${session.browser || t`Onbekende browser`}`}
          icon="signOut"
          title={t`Afmelden?`}
          confirmLabel={t`Afmelden`}
          onConfirm={end}
        >
          <p>
            {session.current
              ? t`Je wordt hier uitgelogd en gaat naar de inlogpagina.`
              : t`${session.browser || t`Onbekende browser`} is daarna uitgelogd.`}
          </p>
        </ConfirmAction>
      </td>
    </tr>
  );
}

function SessionsPage() {
  usePageTitle(t`Waar je bent ingelogd`);
  const sessions = useQuery(mySessionsQuery);
  return (
    <Shell>
      <PageHeader
        title={t`Waar je bent ingelogd`}
        description={t`Zie je hier een browser die je niet kent? Meld die af en wijzig je wachtwoord.`}
      />
      {sessions.isPending ? <Loading /> : null}
      <ErrorAlert error={sessions.error} />
      {sessions.data ? (
        // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrolling box must be reachable by keyboard (WCAG 2.1.1).
        <section className="panel table-scroll" aria-label={t`Sessies`} tabIndex={0}>
          <table className="table table-wide">
            <thead>
              <tr>
                <th scope="col">{t`Browser`}</th>
                <th scope="col">{t`Methode`}</th>
                <th scope="col">{t`Adres`}</th>
                <th scope="col">{t`Gestart`}</th>
                <th scope="col">{t`Laatst gebruikt`}</th>
                <th scope="col">
                  <span className="visually-hidden">{t`Acties`}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {sessions.data.map((session) => (
                <SessionRow key={session.id} session={session} />
              ))}
            </tbody>
          </table>
        </section>
      ) : null}
    </Shell>
  );
}

export const sessionsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/account/sessies",
  beforeLoad: ({ context }) => requireUser(context.queryClient),
  component: SessionsPage,
});
