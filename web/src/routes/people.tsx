import { t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { createRoute, Link, redirect } from "@tanstack/react-router";
import { useState } from "react";
import { Button, Input, Label, SearchField } from "react-aria-components";
import { meQuery, peopleQuery, personsQuery, visibilityQuery } from "../api/queries";
import { ExportPanel } from "../components/export-panel";
import { ExternalAiNotice } from "../components/external-ai-notice";
import { Icon } from "../components/icons";
import {
  type CreatedInvitation,
  InvitationLinkDialog,
  InviteDialog,
} from "../components/invite-dialog";
import { OpenInvitations } from "../components/open-invitations";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import {
  lastSignIn,
  type PersonAccount,
  PersonActions,
  SortsPostSwitch,
  StateBadge,
} from "../components/person-actions";
import { PersonDialog } from "../components/person-dialog";
import { roleLabel } from "../components/role-dialog";
import { Badge, EmptyState, Loading, Persona } from "../components/ui";
import { departmentsTitle } from "./departments";
import { workspaceRoute } from "./workspace";

/** The status filter: every state, or archived people only. */
const FILTERS = ["all", "active", "invited", "inactive", "deactivated", "archived"] as const;
type Filter = (typeof FILTERS)[number];
/** Somebody who is no admin sees only whether a person is active or not (B4). */
const MEMBER_FILTERS: readonly Filter[] = ["all", "active", "inactive", "archived"];

function filterLabel(filter: Filter): string {
  if (filter === "active") return t`Actief`;
  if (filter === "invited") return t`Uitgenodigd`;
  if (filter === "inactive") return t`Niet actief`;
  if (filter === "deactivated") return t`Uitgeschakeld`;
  if (filter === "archived") return t`Gearchiveerd`;
  return t`Alle`;
}

function isFilter(value: string): value is Filter {
  return (FILTERS as readonly string[]).includes(value);
}

/** Lower case without accents: "José" is found by "jose". */
export function folded(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .toLocaleLowerCase()
    .replace(/\s+/g, " ")
    .trim();
}

function shown(person: PersonAccount, filter: Filter, search: string): boolean {
  if (filter === "archived" ? !person.archived : person.archived) return false;
  if (filter !== "all" && filter !== "archived" && person.state !== filter) return false;
  if (!search) return true;
  return [person.display_name, ...person.name_variants].some((name) =>
    folded(name).includes(search),
  );
}

function PeoplePage() {
  usePageTitle(t`Mensen`);
  const { workspace } = peopleRoute.useRouteContext();
  const people = useQuery(peopleQuery(workspace.id));
  const persons = useQuery(personsQuery(workspace.id));
  const visibility = useQuery(visibilityQuery(workspace.id));
  const { data: me } = useQuery(meQuery);
  const isAdmin = workspace.role === "admin";
  const canEdit = workspace.role !== "viewer";
  // At home nothing about roles shows unless somebody opens "Rol wijzigen".
  const showRole = isAdmin && workspace.kind !== "home";
  const sorting = isAdmin && visibility.data !== undefined && !visibility.data.everyone_sees_all;
  const departmentsOn = visibility.data?.departments_enabled === true;
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const [adding, setAdding] = useState(false);
  const [inviting, setInviting] = useState(false);
  const [renewed, setRenewed] = useState<CreatedInvitation | null>(null);
  const all = people.data ?? [];
  const adminCount = all.filter((person) => person.role === "admin").length;
  const activeCount = all.filter((person) => person.state === "active").length;
  const rows = all.filter((person) => shown(person, filter, folded(search)));
  const departmentNames = (person: PersonAccount) =>
    (person.department_ids ?? []).flatMap((id) => {
      const department = persons.data?.find((candidate) => candidate.id === id);
      return department ? [department.display_name] : [];
    });

  return (
    <Shell workspace={workspace}>
      <PageHeader
        title={t`Mensen`}
        description={
          isAdmin
            ? t`Iedereen die in ${workspace.name} post kan krijgen. Wie een account heeft, logt zelf in; anderen nodig je uit.`
            : t`Iedereen die in ${workspace.name} post kan krijgen.`
        }
        commands={
          <>
            {canEdit ? (
              <Button
                className={isAdmin ? "button secondary" : "button"}
                onPress={() => setAdding(true)}
              >
                <Icon name="add" />
                {t`Persoon toevoegen`}
              </Button>
            ) : null}
            {isAdmin ? (
              <Button className="button" onPress={() => setInviting(true)}>
                <Icon name="personAdd" />
                {t`Iemand uitnodigen`}
              </Button>
            ) : null}
          </>
        }
      />
      <ExternalAiNotice workspaceId={workspace.id} />
      <div className="filter-bar">
        <div className="filter">
          <label htmlFor="people-state">{t`Status`}</label>
          <select
            id="people-state"
            value={filter}
            onChange={(event) => {
              if (isFilter(event.target.value)) setFilter(event.target.value);
            }}
          >
            {(isAdmin ? FILTERS : MEMBER_FILTERS).map((option) => (
              <option key={option} value={option}>
                {filterLabel(option)}
              </option>
            ))}
          </select>
        </div>
        <SearchField className="filter" value={search} onChange={setSearch}>
          <Label>{t`Zoek op naam`}</Label>
          <Input />
        </SearchField>
      </div>
      {people.isPending ? <Loading /> : null}
      <ErrorAlert error={people.error} />
      {people.data && rows.length === 0 ? (
        <EmptyState icon="people">{t`Niemand gevonden.`}</EmptyState>
      ) : null}
      {people.data && rows.length > 0 ? (
        // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrolling box must be reachable by keyboard (WCAG 2.1.1).
        <section className="panel table-scroll" aria-label={t`Mensen`} tabIndex={0}>
          <table className="table table-wide">
            <thead>
              <tr>
                <th scope="col">{t`Naam`}</th>
                <th scope="col">{t`Status`}</th>
                {showRole ? <th scope="col">{t`Rol`}</th> : null}
                {sorting ? <th scope="col">{t`Verdeelt post`}</th> : null}
                {departmentsOn ? (
                  <th scope="col">{departmentsTitle(visibility.data?.kind ?? "business")}</th>
                ) : null}
                {isAdmin ? <th scope="col">{t`Laatst ingelogd`}</th> : null}
                <th scope="col">{t`Brieven`}</th>
                <th scope="col">
                  <span className="visually-hidden">{t`Acties`}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((person) => (
                <tr key={person.id}>
                  <th scope="row" className="cell-strong">
                    <span className="cell-with-badge">
                      <Link
                        to="/w/$workspaceId/mensen/$personId"
                        params={{ workspaceId: workspace.id, personId: person.id }}
                      >
                        <Persona name={person.display_name} />
                      </Link>
                      {person.archived ? <Badge>{t`Gearchiveerd`}</Badge> : null}
                    </span>
                  </th>
                  <td>
                    <StateBadge person={person} />
                  </td>
                  {showRole ? <td>{person.role ? roleLabel(person.role) : ""}</td> : null}
                  {sorting ? (
                    <td>
                      {person.state === "active" && person.user_id ? (
                        <SortsPostSwitch workspaceId={workspace.id} person={person} />
                      ) : null}
                    </td>
                  ) : null}
                  {departmentsOn ? <td>{departmentNames(person).join(", ")}</td> : null}
                  {isAdmin ? (
                    <td>
                      {person.state === "active" || person.state === "deactivated"
                        ? lastSignIn(person)
                        : t`Nooit ingelogd`}
                    </td>
                  ) : null}
                  <td className="num">{person.document_count}</td>
                  <td className="cell-action">
                    <PersonActions
                      workspaceId={workspace.id}
                      workspaceName={workspace.name}
                      person={person}
                      isAdmin={isAdmin}
                      isOwn={person.user_id !== null && person.user_id === me?.id}
                      isOnlyAdmin={person.role === "admin" && adminCount === 1}
                      isAlone={activeCount === 1}
                      managers={all.filter(
                        (candidate) => candidate.state === "active" && candidate.user_id,
                      )}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}
      {isAdmin ? (
        <OpenInvitations workspaceId={workspace.id} showRole={showRole} onRenewed={setRenewed} />
      ) : null}
      <section className="panel panel-pad" aria-labelledby="my-export">
        <h2 id="my-export">{t`Exporteren`}</h2>
        <p className="muted">{t`Maak een ZIP-bestand met een PDF per brief of document en de gegevens erbij, om te bewaren of naar een ander programma te brengen. Het staat 24 uur klaar om te downloaden.`}</p>
        <ExportPanel workspaceId={workspace.id} isAdmin={isAdmin} offerAll={isAdmin} />
      </section>
      <PersonDialog
        workspaceId={workspace.id}
        editing={adding ? "new" : null}
        onClose={() => setAdding(false)}
      />
      {isAdmin ? (
        <>
          <InviteDialog workspaceId={workspace.id} isOpen={inviting} onOpenChange={setInviting} />
          <InvitationLinkDialog created={renewed} onClose={() => setRenewed(null)} renewed />
        </>
      ) : null}
    </Shell>
  );
}

export const peopleRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "mensen",
  component: PeoplePage,
});

/** The old pages "Personen" and "Leden" are one section now. */
export const personsRedirectRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "personen",
  beforeLoad: ({ params }) => {
    throw redirect({ to: "/w/$workspaceId/mensen", params, replace: true });
  },
});

export const membersRedirectRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "leden",
  beforeLoad: ({ params }) => {
    throw redirect({ to: "/w/$workspaceId/mensen", params, replace: true });
  },
});
