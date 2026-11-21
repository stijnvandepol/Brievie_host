import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Button, Dialog, DialogTrigger, Popover, Switch } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { myExportQuery, personPrivatePostQuery, workspacesQuery } from "../api/queries";
import type { components } from "../api/schema";
import { dateFormat } from "../format";
import { putSortsPost } from "../routes/visibility";
import { ConfirmAction, ConfirmDialog } from "./confirm-dialog";
import { ExportPanel } from "./export-panel";
import { Icon } from "./icons";
import { type CreatedInvitation, InvitationLinkDialog, InviteDialog } from "./invite-dialog";
import { ErrorAlert, focusPageHeading } from "./page";
import { refreshPeople } from "./person-dialog";
import { ResetLinkDialog } from "./reset-link-dialog";
import { RoleDialog } from "./role-dialog";
import { Badge, type Intent, MessageBar } from "./ui";
import { previewBody, useSightChange } from "./visibility-preview-dialog";

export type PersonAccount = components["schemas"]["PersonAccountOut"];

const dateTime = dateFormat({ dateStyle: "long", timeStyle: "short" });

/** The account state in words; an unknown state reads as "Niet actief". */
export function stateLabel(state: string): string {
  if (state === "active") return t`Actief`;
  if (state === "invited") return t`Uitgenodigd`;
  if (state === "deactivated") return t`Uitgeschakeld`;
  return t`Niet actief`;
}

const STATE_INTENT: Record<string, Intent | "brand" | undefined> = {
  active: "success",
  invited: "info",
  deactivated: "warning",
};

/** The state as a badge, "Verlopen" beside an invitation that expired. */
export function StateBadge({ person }: { person: PersonAccount }) {
  return (
    <span className="cell-badges">
      <Badge intent={STATE_INTENT[person.state]}>{stateLabel(person.state)}</Badge>
      {person.state === "invited" && person.invitation_expired ? (
        <Badge intent="warning">{t`Verlopen`}</Badge>
      ) : null}
      {person.state !== "invited" && person.invitation_id ? (
        <Badge intent="warning">{t`Open uitnodiging`}</Badge>
      ) : null}
    </span>
  );
}

/** "Laatst ingelogd op ..." or "Nooit ingelogd" (admins only get the times). */
export function lastSignIn(person: PersonAccount): string {
  // The account is in another workspace too: its activity is not this workspace's.
  if (person.activity_hidden) return t`Niet te zien`;
  return person.last_sign_in_at
    ? t`Laatst ingelogd op ${dateTime.format(new Date(person.last_sign_in_at))}`
    : t`Nooit ingelogd`;
}

/** "Verdeelt post" for an active person, as a switch for an admin. */
export function SortsPostSwitch({
  workspaceId,
  person,
}: {
  workspaceId: string;
  person: PersonAccount;
}) {
  const sight = useSightChange(workspaceId);
  const userId = person.user_id ?? "";
  const sortsPost = useMutation({
    mutationFn: (on: boolean) =>
      sight.tryFirst({
        preview: previewBody({
          member_change: "sorts_post",
          member_user_id: userId,
          member_sorts_post: on,
        }),
        confirmLabel: t`Ja, ${person.display_name} verdeelt post`,
        write: (confirmation) => putSortsPost(workspaceId, userId, on, confirmation),
      }),
  });
  return (
    <>
      <Switch
        className="switch"
        isSelected={person.sorts_post ?? false}
        isDisabled={person.role === "viewer" || sortsPost.isPending}
        aria-label={t`Verdeelt post: ${person.display_name}`}
        onChange={(on) => sortsPost.mutate(on)}
      >
        <span className="switch-track" aria-hidden="true" />
      </Switch>
      {person.role === "viewer" ? <span className="cell-hint">{t`Alleen lezen`}</span> : null}
      <ErrorAlert error={sortsPost.error} />
      {sight.dialog}
    </>
  );
}

/** In the leave dialog: the export, while the workspace allows it. After leaving nothing
 * can be downloaded any more. */
function LeaveExportOffer({ workspaceId, isAdmin }: { workspaceId: string; isAdmin: boolean }) {
  const { data } = useQuery(myExportQuery(workspaceId));
  if (!data?.allowed) return null;
  return (
    <>
      <p>{t`Exporteer eerst je post: na het verlaten kun je niet meer downloaden.`}</p>
      <ExportPanel workspaceId={workspaceId} isAdmin={isAdmin} />
    </>
  );
}

/** Said in the "Uitschakelen" dialog when private letters are on the name (asked when it
 * opens, one person at a time: a list column would show it for everybody at once). */
function PrivatePostWarning({
  workspaceId,
  person,
  managers,
  manager,
  onManager,
}: {
  workspaceId: string;
  person: PersonAccount;
  /** Who could manage the post (active members other than this person). */
  managers: PersonAccount[];
  manager: string;
  onManager: (userId: string) => void;
}) {
  const { data } = useQuery(personPrivatePostQuery(workspaceId, person.id));
  if (!data?.has_private_post) return null;
  const name = person.display_name;
  return (
    <>
      <MessageBar intent="warning">
        {t`Op naam van ${name} staan privébrieven. Na uitschakelen leest ${name} ze niet meer. Wie een brief gedeeld kreeg, leest hem nog. Staat noodtoegang aan, dan kan een beheerder ze met een reden openen. Moet iemand anders ze kunnen lezen, laat dan de post beheren of nodig ${name} opnieuw uit.`}
      </MessageBar>
      {managers.length > 0 ? (
        <div className="field">
          <label htmlFor={`manager-after-${person.id}`}>{t`Laat de post beheren door`}</label>
          <select
            id={`manager-after-${person.id}`}
            value={manager}
            onChange={(event) => onManager(event.target.value)}
          >
            <option value="">{t`Niemand`}</option>
            {managers.map((candidate) => (
              <option key={candidate.id} value={candidate.user_id ?? ""}>
                {candidate.display_name}
              </option>
            ))}
          </select>
        </div>
      ) : null}
    </>
  );
}

/** Puts `userId` in charge of the person's post ("Beheerd door"), or nobody (`null`):
 * a gain asks through the preview first; ending it needs none. */
export function useSetManager(workspaceId: string, person: PersonAccount) {
  const queryClient = useQueryClient();
  const sight = useSightChange(workspaceId);
  const [askAcknowledgement, setAskAcknowledgement] = useState<{
    userId: string;
    name: string;
  } | null>(null);
  const mutation = useMutation({
    mutationFn: ({
      userId,
      name,
      acknowledged = false,
    }: {
      userId: string | null;
      name: string;
      acknowledged?: boolean;
    }) =>
      sight.tryFirst({
        preview: previewBody({
          member_change: "manage",
          member_user_id: userId,
          member_person_id: person.id,
        }),
        confirmLabel: t`Ja, ${name} beheert de post van ${person.display_name}`,
        write: async (confirmation) => {
          const { error } = await api.PUT(
            "/api/v1/workspaces/{workspace_id}/persons/{person_id}/manager",
            {
              params: { path: { workspace_id: workspaceId, person_id: person.id } },
              body: {
                user_id: userId,
                private_post_acknowledged: acknowledged,
                ...confirmation,
              },
            },
          );
          if (error !== undefined) throw new ApiFailure(error);
        },
      }),
    onError: (reason, { userId, name }) => {
      // Private letters on the name: the admin agrees to that on its own first.
      if (
        reason instanceof ApiFailure &&
        reason.code === "private_post_acknowledgement_required" &&
        userId !== null
      ) {
        setAskAcknowledgement({ userId, name });
      }
    },
    onSettled: () => refreshPeople(queryClient, workspaceId),
  });
  const personName = person.display_name;
  const dialog = (
    <>
      {sight.dialog}
      <ConfirmDialog
        isOpen={askAcknowledgement !== null}
        onOpenChange={(open) => {
          if (!open) setAskAcknowledgement(null);
        }}
        title={t`Privébrieven laten lezen?`}
        confirmLabel={t`Ja, ${askAcknowledgement?.name ?? ""} leest ze`}
        tone="primary"
        onConfirm={async () => {
          const asked = askAcknowledgement;
          setAskAcknowledgement(null);
          if (asked) mutation.mutate({ ...asked, acknowledged: true });
        }}
      >
        <p>
          {t`Op naam van ${personName} staan privébrieven. Als ${askAcknowledgement?.name ?? ""} de post beheert, leest die deze privébrieven ook.`}
        </p>
      </ConfirmDialog>
    </>
  );
  return { mutation, dialog };
}

/** "Beheerd door" of a person without an active account: a choice of member, or nobody. */
export function ManagerControl({
  workspaceId,
  person,
  managers,
}: {
  workspaceId: string;
  person: PersonAccount;
  managers: PersonAccount[];
}) {
  const { mutation, dialog } = useSetManager(workspaceId, person);
  const name = person.display_name;
  return (
    <>
      <select
        className="inline-select"
        aria-label={t`Beheerd door: ${name}`}
        value={person.managed_by_user_id ?? ""}
        disabled={mutation.isPending}
        onChange={(event) => {
          const userId = event.target.value || null;
          const chosen = managers.find((candidate) => candidate.user_id === userId);
          mutation.mutate({ userId, name: chosen?.display_name ?? "" });
        }}
      >
        <option value="">{t`Niemand`}</option>
        {managers.map((candidate) => (
          <option key={candidate.id} value={candidate.user_id ?? ""}>
            {candidate.display_name}
          </option>
        ))}
      </select>
      <ErrorAlert error={mutation.error} />
      {dialog}
    </>
  );
}

/**
 * What an admin can do with a person's account, on the row of Mensen and on the person's
 * page: invite (again), send the invitation again or withdraw it, change the role, switch
 * the account off, and the account's devices and reset link where the server allows them.
 * One's own row offers leaving the workspace. Somebody who is no admin gets nothing here.
 */
export function PersonActions({
  workspaceId,
  workspaceName,
  person,
  isAdmin,
  isOwn,
  isOnlyAdmin,
  isAlone = false,
  managers = [],
}: {
  workspaceId: string;
  workspaceName: string;
  person: PersonAccount;
  isAdmin: boolean;
  isOwn: boolean;
  /** This person is the workspace's only admin: no leaving, no stepping down. */
  isOnlyAdmin: boolean;
  /** Nobody else has an account here: the only admin has nothing to explain. */
  isAlone?: boolean;
  /** The active members who could manage this person's post once it is switched off. */
  managers?: PersonAccount[];
}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const sight = useSightChange(workspaceId);
  const [inviting, setInviting] = useState(false);
  const [roleOpen, setRoleOpen] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [renewed, setRenewed] = useState<CreatedInvitation | null>(null);
  const name = person.display_name;
  const refresh = () => refreshPeople(queryClient, workspaceId);

  const renew = useMutation({
    // The new link goes to the dialog and is kept nowhere else.
    gcTime: 0,
    mutationFn: async () => {
      const invitationId = person.invitation_id;
      if (!invitationId) return null;
      let made: CreatedInvitation | null = null;
      // A renewal gives its role and person again, in the state of now: what that widens
      // is confirmed again.
      await sight.tryFirst({
        preview: previewBody({
          member_change: "invite",
          member_role: person.invitation_role || "member",
          member_person_id: person.id,
        }),
        confirmLabel: t`Ja, maak een nieuwe link`,
        write: async (confirmation) => {
          const { data, error } = await api.POST(
            "/api/v1/workspaces/{workspace_id}/invitations/{invitation_id}/renew",
            {
              params: { path: { workspace_id: workspaceId, invitation_id: invitationId } },
              body: confirmation,
            },
          );
          if (error !== undefined || !data) throw new ApiFailure(error);
          made = data;
        },
      });
      return made;
    },
    onSuccess: async (data) => {
      if (data === null) return;
      setRenewed(data);
      renew.reset();
      await refresh();
    },
  });

  async function withdraw() {
    if (!person.invitation_id) return;
    const { error, response } = await api.DELETE(
      "/api/v1/workspaces/{workspace_id}/invitations/{invitation_id}",
      { params: { path: { workspace_id: workspaceId, invitation_id: person.invitation_id } } },
    );
    if (!response.ok) throw new ApiFailure(error);
    await refresh();
  }

  const [managerAfter, setManagerAfter] = useState("");
  const manage = useSetManager(workspaceId, person);

  async function switchOff() {
    if (!person.user_id) return;
    const { error, response } = await api.DELETE(
      "/api/v1/workspaces/{workspace_id}/members/{user_id}",
      { params: { path: { workspace_id: workspaceId, user_id: person.user_id } } },
    );
    if (!response.ok) throw new ApiFailure(error);
    await refresh();
    // The dialog gave focus back to a button that is gone now.
    focusPageHeading();
    if (managerAfter) {
      // A separate change of sight with its own preview, once the account is off.
      const chosen = managers.find((candidate) => candidate.user_id === managerAfter);
      manage.mutation.mutate({ userId: managerAfter, name: chosen?.display_name ?? "" });
    }
  }

  async function leave() {
    const { error, response } = await api.POST("/api/v1/workspaces/{workspace_id}/leave", {
      params: { path: { workspace_id: workspaceId } },
    });
    if (!response.ok) throw new ApiFailure(error);
    // The list of workspaces has no observer here: drop it so the next page asks again.
    queryClient.removeQueries({ queryKey: workspacesQuery.queryKey, exact: true });
    // Leave the page first, so nothing on it asks the server again: with another workspace
    // left the list opens, with none the account is off and the guard sends me to sign-in.
    await navigate({ to: "/" });
    await queryClient.cancelQueries({ queryKey: ["workspaces", workspaceId] });
    queryClient.removeQueries({ queryKey: ["workspaces", workspaceId] });
  }

  const ownAndLast = isOwn && isOnlyAdmin;
  const active = person.state === "active" && person.user_id;

  return (
    <span className="row-actions">
      {isAdmin && (person.state === "inactive" || person.state === "deactivated") ? (
        <>
          <Button
            className="button-text"
            aria-label={
              person.state === "deactivated"
                ? t`Opnieuw uitnodigen: ${name}`
                : t`Uitnodigen: ${name}`
            }
            onPress={() => setInviting(true)}
          >
            <Icon name="personAdd" />
            {person.state === "deactivated" ? t`Opnieuw uitnodigen` : t`Uitnodigen`}
          </Button>
          <InviteDialog
            workspaceId={workspaceId}
            person={{ id: person.id, name, email: person.email ?? "" }}
            isOpen={inviting}
            onOpenChange={setInviting}
          />
        </>
      ) : null}
      {isAdmin && person.state === "invited" && person.invitation_id ? (
        <Button
          className="button-text"
          aria-label={t`Opnieuw versturen: ${name}`}
          isPending={renew.isPending}
          onPress={() => renew.mutate()}
        >
          <Icon name="refresh" />
          {t`Opnieuw versturen`}
        </Button>
      ) : null}
      {/* Any open link can be withdrawn, also one left on an active person (review of
          PR #43, point 1). */}
      {isAdmin && person.invitation_id ? (
        <ConfirmAction
          label={t`Intrekken`}
          ariaLabel={t`Intrekken: ${name}`}
          icon="dismiss"
          title={t`Uitnodiging van ${name} intrekken?`}
          confirmLabel={t`Intrekken`}
          onConfirm={withdraw}
        >
          <p>{t`De link werkt daarna niet meer. Wie hem al heeft, kan er niet meer mee binnenkomen.`}</p>
        </ConfirmAction>
      ) : null}
      {isAdmin && active && !ownAndLast ? (
        <>
          <Button
            className="button-text"
            aria-label={t`Rol wijzigen: ${name}`}
            onPress={() => setRoleOpen(true)}
          >
            <Icon name="edit" />
            {t`Rol wijzigen`}
          </Button>
          <RoleDialog
            workspaceId={workspaceId}
            member={{ user_id: person.user_id ?? "", name, role: person.role ?? "member" }}
            isOpen={roleOpen}
            onOpenChange={setRoleOpen}
          />
        </>
      ) : null}
      {isAdmin && active && !isOwn ? (
        <ConfirmAction
          label={t`Uitschakelen`}
          ariaLabel={t`Uitschakelen: ${name}`}
          icon="dismiss"
          title={t`Account van ${name} uitschakelen?`}
          confirmLabel={t`Uitschakelen`}
          onConfirm={switchOff}
        >
          <p>
            {t`${name} kan daarna niet meer bij deze werkruimte. De post blijft hier. Taken van ${name} hebben daarna niemand. Je kunt ${name} later opnieuw uitnodigen.`}
          </p>
          <PrivatePostWarning
            workspaceId={workspaceId}
            person={person}
            managers={managers.filter((candidate) => candidate.id !== person.id)}
            manager={managerAfter}
            onManager={setManagerAfter}
          />
        </ConfirmAction>
      ) : null}
      {isAdmin && active && person.can_manage_account && !isOwn ? (
        <>
          <DialogTrigger>
            <Button className="button-text" aria-label={t`Account: ${name}`}>
              {t`Account`}
              <Icon name="chevronDown" />
            </Button>
            <Popover className="popover account-popover" placement="bottom end" offset={4}>
              <Dialog className="account-dialog" aria-label={t`Account van ${name}`}>
                {({ close }) => (
                  <>
                    <Link
                      to="/w/$workspaceId/leden/$userId"
                      params={{ workspaceId, userId: person.user_id ?? "" }}
                      className="menu-item"
                      onClick={close}
                    >
                      <Icon name="phone" />
                      {t`Apparaten en sessies`}
                    </Link>
                    <Button
                      className="menu-item"
                      onPress={() => {
                        close();
                        setResetOpen(true);
                      }}
                    >
                      <Icon name="link" />
                      {t`Herstellink maken`}
                    </Button>
                  </>
                )}
              </Dialog>
            </Popover>
          </DialogTrigger>
          <ResetLinkDialog
            userId={person.user_id ?? ""}
            name={name}
            isOpen={resetOpen}
            onOpenChange={setResetOpen}
          />
        </>
      ) : null}
      {ownAndLast && !isAlone ? (
        <span className="muted">
          {t`Je bent de enige beheerder. Maak eerst iemand anders beheerder als je wilt stoppen.`}
        </span>
      ) : null}
      {isOwn && !ownAndLast ? (
        <ConfirmAction
          label={t`Werkruimte verlaten`}
          icon="signOut"
          title={t`Werkruimte verlaten?`}
          confirmLabel={t`Verlaten`}
          onConfirm={leave}
        >
          <p>
            {t`Je kunt daarna niet meer bij ${workspaceName}. De post blijft hier voor de andere leden.`}
          </p>
          <LeaveExportOffer workspaceId={workspaceId} isAdmin={isAdmin} />
        </ConfirmAction>
      ) : null}
      {renew.isError ? <ErrorAlert error={renew.error} /> : null}
      {manage.mutation.isError ? <ErrorAlert error={manage.mutation.error} /> : null}
      {sight.dialog}
      {manage.dialog}
      <InvitationLinkDialog created={renewed} onClose={() => setRenewed(null)} renewed />
    </span>
  );
}
