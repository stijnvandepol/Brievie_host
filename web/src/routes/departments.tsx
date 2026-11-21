import { t } from "@lingui/core/macro";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createRoute, Link } from "@tanstack/react-router";
import { useState } from "react";
import {
  Button,
  Dialog,
  Form,
  Heading,
  Input,
  Label,
  Modal,
  ModalOverlay,
  Text,
  TextArea,
  TextField,
} from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import {
  membersQuery,
  personsQuery,
  visibilityQuery,
  workspaceSettingsQuery,
} from "../api/queries";
import type { components } from "../api/schema";
import { ConfirmAction } from "../components/confirm-dialog";
import { EmergencyDialog } from "../components/emergency-dialog";
import { Icon } from "../components/icons";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import { Badge, CheckRow, EmptyState, Loading } from "../components/ui";
import {
  type Confirmation,
  previewBody,
  useSightChange,
} from "../components/visibility-preview-dialog";
import { workspaceRoute } from "./workspace";

type Person = components["schemas"]["PersonOut"];
type Member = components["schemas"]["MemberOut"];
type SightChange = ReturnType<typeof useSightChange>;

/** "Afdelingen" in an Organisatie, "Groepen" at home (R51). */
export function departmentsTitle(kind: string): string {
  return kind === "home" ? t`Groepen` : t`Afdelingen`;
}

async function addMember(
  workspaceId: string,
  departmentId: string,
  userId: string,
  confirmation: Confirmation,
) {
  const { error } = await api.POST(
    "/api/v1/workspaces/{workspace_id}/persons/{person_id}/members",
    {
      params: { path: { workspace_id: workspaceId, person_id: departmentId } },
      body: { user_id: userId, is_lead: false, ...confirmation },
    },
  );
  if (error !== undefined) throw new ApiFailure(error);
}

async function removeMember(workspaceId: string, departmentId: string, userId: string) {
  const { error, response } = await api.DELETE(
    "/api/v1/workspaces/{workspace_id}/persons/{person_id}/members/{user_id}",
    { params: { path: { workspace_id: workspaceId, person_id: departmentId, user_id: userId } } },
  );
  if (!response.ok) throw new ApiFailure(error);
}

/** Add one member to a department, through the preview when somebody gains sight. */
function joinDepartment(
  sight: SightChange,
  workspaceId: string,
  department: { id: string; display_name: string },
  member: Member,
) {
  return sight.tryFirst({
    preview: previewBody({
      member_change: "department_add",
      member_user_id: member.user_id,
      member_person_id: department.id,
    }),
    confirmLabel: t`Ja, voeg ${member.name} toe aan ${department.display_name}`,
    write: (confirmation) => addMember(workspaceId, department.id, member.user_id, confirmation),
  });
}

function NewDepartmentDialog({
  workspaceId,
  kind,
  members,
  sight,
  onClose,
}: {
  workspaceId: string;
  kind: string;
  members: Member[];
  sight: SightChange;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [variants, setVariants] = useState("");
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [error, setError] = useState<unknown>(undefined);
  const [pending, setPending] = useState(false);
  const title = kind === "home" ? t`Groep maken` : t`Afdeling maken`;

  async function save() {
    setPending(true);
    setError(undefined);
    try {
      const { data, error: failed } = await api.POST("/api/v1/workspaces/{workspace_id}/persons", {
        params: { path: { workspace_id: workspaceId } },
        body: {
          kind: "department",
          display_name: name.trim(),
          name_variants: variants
            .split("\n")
            .map((line) => line.trim())
            .filter((line) => line !== ""),
          archived: false,
        },
      });
      if (failed !== undefined || !data) throw new ApiFailure(failed);
      for (const member of members) {
        if (chosen.has(member.user_id)) await joinDepartment(sight, workspaceId, data, member);
      }
      onClose();
    } catch (reason) {
      setError(reason);
    } finally {
      setPending(false);
      await queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId] });
    }
  }

  return (
    <ModalOverlay
      isOpen
      onOpenChange={(open) => !open && onClose()}
      isDismissable
      className="overlay"
    >
      <Modal className="modal">
        <Dialog className="dialog" aria-label={title}>
          <Heading slot="title" className="dialog-title">
            {title}
          </Heading>
          <Form
            className="form"
            onSubmit={(event) => {
              event.preventDefault();
              void save();
            }}
          >
            <TextField className="field" value={name} onChange={setName} isRequired>
              <Label>{t`Naam`}</Label>
              <Input />
            </TextField>
            <TextField className="field" value={variants} onChange={setVariants}>
              <Label>{t`Naamvarianten`}</Label>
              <Text slot="description">
                {t`Zo staat de naam op brieven, bijvoorbeeld Afd. Financiën. Zet elke variant op een eigen regel.`}
              </Text>
              <TextArea rows={3} />
            </TextField>
            <fieldset className="check-group">
              <legend>{t`Leden`}</legend>
              {members.map((member) => (
                <CheckRow
                  key={member.user_id}
                  checked={chosen.has(member.user_id)}
                  onChange={(on) => {
                    const next = new Set(chosen);
                    if (on) next.add(member.user_id);
                    else next.delete(member.user_id);
                    setChosen(next);
                  }}
                >
                  {member.name}
                </CheckRow>
              ))}
            </fieldset>
            <ErrorAlert error={error} />
            <div className="dialog-actions">
              <Button className="button secondary" onPress={onClose}>
                {t`Annuleren`}
              </Button>
              <Button type="submit" className="button" isPending={pending}>
                {t`Opslaan`}
              </Button>
            </div>
          </Form>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}

/** Who is in a department and who leads it; every tick is saved at once. */
function MembersDialog({
  workspaceId,
  department,
  members,
  sight,
  onClose,
}: {
  workspaceId: string;
  department: Person;
  members: Member[];
  sight: SightChange;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<unknown>(undefined);
  const [busy, setBusy] = useState(false);
  const inside = new Set(department.member_user_ids ?? []);
  const leads = new Set(department.lead_user_ids ?? []);
  const title = t`Leden van ${department.display_name}`;

  async function run(step: () => Promise<unknown>) {
    setBusy(true);
    setError(undefined);
    try {
      await step();
    } catch (reason) {
      setError(reason);
    } finally {
      setBusy(false);
      // A member leaving narrows sight: every list and count of the workspace is stale.
      await queryClient.invalidateQueries({ queryKey: ["workspaces", workspaceId] });
    }
  }

  function toggleMember(member: Member, on: boolean) {
    void run(() =>
      on
        ? joinDepartment(sight, workspaceId, department, member)
        : // Leaving narrows: the post goes to whoever sorts it already, so nobody confirms
          // (P6); the server writes nothing a confirmation would cover.
          removeMember(workspaceId, department.id, member.user_id),
    );
  }

  function toggleLead(member: Member, on: boolean) {
    void run(async () => {
      const { error: failed } = await api.PUT(
        "/api/v1/workspaces/{workspace_id}/persons/{person_id}/members/{user_id}",
        {
          params: {
            path: { workspace_id: workspaceId, person_id: department.id, user_id: member.user_id },
          },
          body: { is_lead: on },
        },
      );
      if (failed !== undefined) throw new ApiFailure(failed);
    });
  }

  return (
    <ModalOverlay
      isOpen
      onOpenChange={(open) => !open && onClose()}
      isDismissable
      className="overlay"
    >
      <Modal className="modal">
        <Dialog className="dialog" aria-label={title}>
          <Heading slot="title" className="dialog-title">
            {title}
          </Heading>
          <p>{t`Wie in ${department.display_name} zit, ziet de post op die naam. Een leidinggevende mag ook de labels van ${department.display_name} beheren.`}</p>
          <ul className="plain-list">
            {members.map((member) => (
              <li key={member.user_id}>
                <CheckRow
                  checked={inside.has(member.user_id)}
                  disabled={busy}
                  onChange={(on) => toggleMember(member, on)}
                >
                  {member.name}
                </CheckRow>
                {inside.has(member.user_id) ? (
                  <CheckRow
                    checked={leads.has(member.user_id)}
                    disabled={busy}
                    onChange={(on) => toggleLead(member, on)}
                  >
                    {t`${member.name} is leidinggevende`}
                  </CheckRow>
                ) : null}
              </li>
            ))}
          </ul>
          <ErrorAlert error={error} />
          <div className="dialog-actions">
            <Button className="button" onPress={onClose}>
              {t`Klaar`}
            </Button>
          </div>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}

function memberNames(department: Person, members: Member[]): string {
  const leads = new Set(department.lead_user_ids ?? []);
  return (department.member_user_ids ?? [])
    .map((id) => {
      const member = members.find((candidate) => candidate.user_id === id);
      if (!member) return null;
      return leads.has(id) ? t`${member.name} (Leidinggevende)` : member.name;
    })
    .filter((name): name is string => name !== null)
    .join(", ");
}

function DepartmentsPage() {
  const { workspace } = departmentsRoute.useRouteContext();
  const queryClient = useQueryClient();
  const visibility = useQuery(visibilityQuery(workspace.id));
  const persons = useQuery(personsQuery(workspace.id));
  const memberList = useQuery(membersQuery(workspace.id));
  const sight = useSightChange(workspace.id);
  const [making, setMaking] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(undefined);
  const [emergency, setEmergency] = useState<Person | null>(null);
  const settings = useQuery({
    ...workspaceSettingsQuery(workspace.id),
    enabled: workspace.role === "admin",
  });
  const emergencyOn = workspace.role === "admin" && settings.data?.emergency_access === true;
  const kind = visibility.data?.kind ?? "business";
  const title = departmentsTitle(kind);
  usePageTitle(title);
  const isAdmin = workspace.role === "admin";
  const members = memberList.data ?? [];
  const departments = (persons.data ?? []).filter((person) => person.kind === "department");
  const editingDepartment = departments.find((department) => department.id === editing);

  async function write(step: () => Promise<unknown>) {
    setError(undefined);
    try {
      await step();
    } catch (reason) {
      setError(reason);
    }
    await queryClient.invalidateQueries({ queryKey: personsQuery(workspace.id).queryKey });
  }

  function archive(department: Person, archived: boolean) {
    void write(async () => {
      const { error: failed } = await api.PUT(
        "/api/v1/workspaces/{workspace_id}/persons/{person_id}",
        {
          params: { path: { workspace_id: workspace.id, person_id: department.id } },
          body: {
            kind: "department",
            display_name: department.display_name,
            name_variants: department.name_variants,
            archived,
          },
        },
      );
      if (failed !== undefined) throw new ApiFailure(failed);
    });
  }

  async function remove(department: Person) {
    const { error: failed, response } = await api.DELETE(
      "/api/v1/workspaces/{workspace_id}/persons/{person_id}",
      { params: { path: { workspace_id: workspace.id, person_id: department.id } } },
    );
    if (!response.ok) throw new ApiFailure(failed);
    await queryClient.invalidateQueries({ queryKey: personsQuery(workspace.id).queryKey });
  }

  return (
    <Shell workspace={workspace}>
      <PageHeader
        title={title}
        description={
          kind === "home"
            ? t`Wil je post scheiden binnen je huishouden? Maak groepen.`
            : t`Wil je post scheiden binnen je organisatie? Maak afdelingen.`
        }
        commands={
          isAdmin ? (
            <Button className="button" onPress={() => setMaking(true)}>
              <Icon name="add" />
              {kind === "home" ? t`Groep maken` : t`Afdeling maken`}
            </Button>
          ) : null
        }
      />
      {persons.isPending || visibility.isPending ? <Loading /> : null}
      <ErrorAlert error={persons.error ?? memberList.error ?? error} />
      {visibility.data && !visibility.data.departments_enabled ? (
        <EmptyState icon="people">
          {kind === "home" ? t`Groepen staan uit.` : t`Afdelingen staan uit.`}
        </EmptyState>
      ) : null}
      {persons.data && visibility.data?.departments_enabled ? (
        // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrolling box must be reachable by keyboard (WCAG 2.1.1).
        <section className="panel table-scroll" aria-label={title} tabIndex={0}>
          <table className="table table-wide">
            <thead>
              <tr>
                <th scope="col">{t`Naam`}</th>
                <th scope="col">{t`Leden`}</th>
                <th scope="col">{t`Aantal brieven`}</th>
                <th scope="col">
                  <span className="visually-hidden">{t`Acties`}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {departments.map((department) => (
                <tr key={department.id}>
                  <th scope="row" className="cell-strong">
                    <span className="cell-with-badge">
                      {department.display_name}
                      {department.archived ? <Badge>{t`Opgeborgen`}</Badge> : null}
                    </span>
                  </th>
                  <td>{memberNames(department, members)}</td>
                  <td className="num">{department.document_count}</td>
                  <td className="cell-action">
                    <span className="row-actions">
                      <Link
                        to="/w/$workspaceId/labels"
                        params={{ workspaceId: workspace.id }}
                        search={{ van: department.id }}
                        className="button-text"
                        aria-label={t`Labels: ${department.display_name}`}
                      >
                        <Icon name="tag" />
                        {t`Labels`}
                      </Link>
                      {isAdmin ? (
                        <>
                          {emergencyOn ? (
                            <Button
                              className="button-text"
                              aria-label={t`Noodtoegang: ${department.display_name}`}
                              onPress={() => setEmergency(department)}
                            >
                              <Icon name="shield" />
                              {t`Noodtoegang`}
                            </Button>
                          ) : null}
                          <Button
                            className="button-text"
                            aria-label={t`Leden: ${department.display_name}`}
                            onPress={() => setEditing(department.id)}
                          >
                            <Icon name="people" />
                            {t`Leden`}
                          </Button>
                          <Button
                            className="button-text"
                            aria-label={
                              department.archived
                                ? t`Terugzetten: ${department.display_name}`
                                : t`Opbergen: ${department.display_name}`
                            }
                            onPress={() => archive(department, !department.archived)}
                          >
                            <Icon name={department.archived ? "restore" : "archive"} />
                            {department.archived ? t`Terugzetten` : t`Opbergen`}
                          </Button>
                          {department.document_count === 0 ? (
                            <ConfirmAction
                              label={t`Verwijderen`}
                              ariaLabel={t`Verwijderen: ${department.display_name}`}
                              icon="delete"
                              title={t`${department.display_name} verwijderen?`}
                              confirmLabel={t`Verwijderen`}
                              onConfirm={() => remove(department)}
                            >
                              <p>{t`${department.display_name} wordt verwijderd, met wie erin zit. Dit kun je niet ongedaan maken.`}</p>
                            </ConfirmAction>
                          ) : null}
                        </>
                      ) : null}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}
      {making ? (
        <NewDepartmentDialog
          workspaceId={workspace.id}
          kind={kind}
          members={members}
          sight={sight}
          onClose={() => setMaking(false)}
        />
      ) : null}
      {editingDepartment ? (
        <MembersDialog
          workspaceId={workspace.id}
          department={editingDepartment}
          members={members}
          sight={sight}
          onClose={() => setEditing(null)}
        />
      ) : null}
      {sight.dialog}
      {emergency ? (
        <EmergencyDialog
          workspaceId={workspace.id}
          person={emergency}
          onClose={() => setEmergency(null)}
        />
      ) : null}
    </Shell>
  );
}

export const departmentsRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "afdelingen",
  component: DepartmentsPage,
});
