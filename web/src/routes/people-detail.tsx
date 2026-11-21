import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Button } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import {
  meQuery,
  peopleQuery,
  personsQuery,
  visibilityQuery,
  workspaceSettingsQuery,
} from "../api/queries";
import { ConfirmAction } from "../components/confirm-dialog";
import { EmergencyDialog } from "../components/emergency-dialog";
import { Icon } from "../components/icons";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import {
  lastSignIn,
  ManagerControl,
  PersonActions,
  SortsPostSwitch,
  StateBadge,
} from "../components/person-actions";
import { PersonDialog, refreshPeople } from "../components/person-dialog";
import { roleLabel } from "../components/role-dialog";
import { EmptyState, Loading } from "../components/ui";
import { dateFormat } from "../format";
import { departmentsTitle } from "./departments";
import { workspaceRoute } from "./workspace";

const dateTime = dateFormat({ dateStyle: "long", timeStyle: "short" });

/** Who sees the post of somebody without an active account, from the workspace's switches
 * (spec section 4): the same rule for a person who never had an account and a switched-off one. */
function PostOfInactivePerson({
  name,
  everyoneSeesAll,
  manager,
}: {
  name: string;
  everyoneSeesAll: boolean;
  /** The member who manages the post ("Beheerd door"), if anybody. */
  manager: string | null;
}) {
  return (
    <section className="panel panel-pad" aria-labelledby="who-sees">
      <h2 id="who-sees">{t`Wie kan de post van ${name} zien?`}</h2>
      <p>
        {everyoneSeesAll
          ? t`Iedereen in deze werkruimte ziet de post van ${name}.`
          : manager
            ? t`${manager} beheert de post van ${name} en ziet die als eigen post.`
            : t`De post van ${name} gaat naar wie post verdeelt.`}
      </p>
      <p className="muted">
        {manager
          ? t`Privébrieven van ${name} leest ${manager} ook, net als de mensen met wie ze gedeeld zijn.`
          : t`Privébrieven lezen alleen de mensen met wie ze gedeeld zijn.`}
      </p>
    </section>
  );
}

function PersonDetailPage() {
  const { workspace } = personDetailRoute.useRouteContext();
  const { personId } = personDetailRoute.useParams();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const people = useQuery(peopleQuery(workspace.id));
  const persons = useQuery(personsQuery(workspace.id));
  const visibility = useQuery(visibilityQuery(workspace.id));
  const { data: me } = useQuery(meQuery);
  const isAdmin = workspace.role === "admin";
  const settings = useQuery({ ...workspaceSettingsQuery(workspace.id), enabled: isAdmin });
  const [editing, setEditing] = useState(false);
  const [emergency, setEmergency] = useState(false);
  const person = people.data?.find((candidate) => candidate.id === personId);
  const name = person?.display_name ?? "";
  usePageTitle(person ? name : t`Mensen`);

  const archive = useMutation({
    mutationFn: async (archived: boolean) => {
      if (!person) return;
      const { error } = await api.PUT("/api/v1/workspaces/{workspace_id}/persons/{person_id}", {
        params: { path: { workspace_id: workspace.id, person_id: person.id } },
        body: {
          display_name: person.display_name,
          name_variants: person.name_variants,
          archived,
          kind: "person",
        },
      });
      if (error !== undefined) throw new ApiFailure(error);
    },
    onSuccess: () => refreshPeople(queryClient, workspace.id),
  });

  async function remove() {
    if (!person) return;
    const { error, response } = await api.DELETE(
      "/api/v1/workspaces/{workspace_id}/persons/{person_id}",
      { params: { path: { workspace_id: workspace.id, person_id: person.id } } },
    );
    if (!response.ok) throw new ApiFailure(error);
    await refreshPeople(queryClient, workspace.id);
    await navigate({ to: "/w/$workspaceId/mensen", params: { workspaceId: workspace.id } });
  }

  const breadcrumb = {
    to: "/w/$workspaceId/mensen" as const,
    params: { workspaceId: workspace.id },
    label: t`Mensen`,
  };

  if (people.isPending) {
    return (
      <Shell workspace={workspace}>
        <Loading />
      </Shell>
    );
  }
  if (!person) {
    return (
      <Shell workspace={workspace}>
        <PageHeader title={t`Persoon niet gevonden`} breadcrumb={breadcrumb} />
        <ErrorAlert error={people.error} />
        <EmptyState icon="people">{t`We kunnen deze persoon niet vinden.`}</EmptyState>
      </Shell>
    );
  }

  const active = person.state === "active";
  const adminCount = (people.data ?? []).filter((candidate) => candidate.role === "admin").length;
  // A person with an account is renamed by an admin only (their name steers post).
  const canEdit = workspace.role !== "viewer" && (isAdmin || person.user_id === null);
  const departments = (person.department_ids ?? []).flatMap((id) => {
    const department = persons.data?.find((candidate) => candidate.id === id);
    return department ? [department.display_name] : [];
  });
  const sorting = visibility.data !== undefined && !visibility.data.everyone_sees_all;
  // Who may manage somebody's post: the members (active people) other than this person.
  const managers = (people.data ?? []).filter(
    (candidate) => candidate.state === "active" && candidate.user_id && candidate.id !== person.id,
  );

  return (
    <Shell workspace={workspace}>
      <PageHeader
        title={name}
        breadcrumb={breadcrumb}
        commands={
          <>
            {canEdit ? (
              <Button className="button secondary" onPress={() => setEditing(true)}>
                <Icon name="edit" />
                {t`Naam en schrijfwijzen`}
              </Button>
            ) : null}
            {canEdit ? (
              <Button
                className="button secondary"
                isPending={archive.isPending}
                onPress={() => archive.mutate(!person.archived)}
              >
                <Icon name={person.archived ? "restore" : "archive"} />
                {person.archived ? t`Terugzetten` : t`Archiveren`}
              </Button>
            ) : null}
          </>
        }
      />
      <ErrorAlert error={archive.error} />
      <section className="panel" aria-label={t`Gegevens van ${name}`}>
        <dl className="detail-list">
          <div>
            <dt>{t`Status`}</dt>
            <dd>
              <StateBadge person={person} />
            </dd>
          </div>
          {person.name_variants.length > 0 ? (
            <div>
              <dt>{t`Naamvarianten`}</dt>
              <dd>{person.name_variants.join(", ")}</dd>
            </div>
          ) : null}
          {isAdmin && active && person.role ? (
            <div>
              <dt>{t`Rol`}</dt>
              <dd>{roleLabel(person.role)}</dd>
            </div>
          ) : null}
          {isAdmin && active && sorting ? (
            <div>
              <dt>{t`Verdeelt post`}</dt>
              <dd>
                <SortsPostSwitch workspaceId={workspace.id} person={person} />
              </dd>
            </div>
          ) : null}
          {visibility.data?.departments_enabled && departments.length > 0 ? (
            <div>
              <dt>{departmentsTitle(visibility.data.kind)}</dt>
              <dd>{departments.join(", ")}</dd>
            </div>
          ) : null}
          {isAdmin && !active ? (
            <div>
              <dt>{t`Beheerd door`}</dt>
              <dd>
                <ManagerControl workspaceId={workspace.id} person={person} managers={managers} />
              </dd>
            </div>
          ) : null}
          {isAdmin && (active || person.state === "deactivated") ? (
            <div>
              <dt>{t`Laatst ingelogd`}</dt>
              <dd>{lastSignIn(person)}</dd>
            </div>
          ) : null}
          {isAdmin && person.last_app_use_at ? (
            <div>
              <dt>{t`Laatst in de app`}</dt>
              <dd>{dateTime.format(new Date(person.last_app_use_at))}</dd>
            </div>
          ) : null}
          {isAdmin && person.state === "invited" && person.invitation_expires_at ? (
            <div>
              <dt>{t`Uitgenodigd tot`}</dt>
              <dd>{dateTime.format(new Date(person.invitation_expires_at))}</dd>
            </div>
          ) : null}
          {isAdmin && person.deactivated_at ? (
            <div>
              <dt>{t`Uitgeschakeld op`}</dt>
              <dd>{dateTime.format(new Date(person.deactivated_at))}</dd>
            </div>
          ) : null}
          <div>
            <dt>{t`Brieven`}</dt>
            <dd>
              <Link
                to="/w/$workspaceId/post"
                params={{ workspaceId: workspace.id }}
                search={{ persoon: person.id }}
              >
                {t`Brieven van ${name} (${person.document_count})`}
              </Link>
            </dd>
          </div>
        </dl>
        <div className="panel-pad">
          <PersonActions
            workspaceId={workspace.id}
            workspaceName={workspace.name}
            person={person}
            isAdmin={isAdmin}
            isOwn={person.user_id !== null && person.user_id === me?.id}
            isOnlyAdmin={person.role === "admin" && adminCount === 1}
            managers={managers}
          />
          {isAdmin && settings.data?.emergency_access ? (
            <Button
              className="button-text"
              aria-label={t`Noodtoegang: ${name}`}
              onPress={() => setEmergency(true)}
            >
              <Icon name="shield" />
              {t`Noodtoegang`}
            </Button>
          ) : null}
          {isAdmin && !active ? (
            <ConfirmAction
              label={t`Verwijderen`}
              ariaLabel={t`Verwijderen: ${name}`}
              icon="delete"
              title={t`Persoon verwijderen?`}
              confirmLabel={t`Verwijderen`}
              onConfirm={remove}
            >
              <p>{t`${name} wordt verwijderd. Dit kun je niet ongedaan maken.`}</p>
            </ConfirmAction>
          ) : null}
        </div>
      </section>
      {/* Admins: who manages a person's post is an admin's fact on this page (B4). */}
      {active || !isAdmin || !visibility.data ? null : (
        <PostOfInactivePerson
          name={name}
          everyoneSeesAll={visibility.data.everyone_sees_all}
          manager={
            (people.data ?? []).find(
              (candidate) =>
                candidate.user_id !== null && candidate.user_id === person.managed_by_user_id,
            )?.display_name ?? null
          }
        />
      )}
      <PersonDialog
        workspaceId={workspace.id}
        editing={editing ? { ...person, kind: "person" } : null}
        onClose={() => setEditing(false)}
      />
      {emergency ? (
        <EmergencyDialog
          workspaceId={workspace.id}
          person={{ id: person.id, display_name: name }}
          onClose={() => setEmergency(false)}
        />
      ) : null}
    </Shell>
  );
}

export const personDetailRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "mensen/$personId",
  component: PersonDetailPage,
});
