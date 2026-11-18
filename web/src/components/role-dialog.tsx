import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "@tanstack/react-router";
import { useId, useState } from "react";
import {
  Button,
  Dialog,
  Form,
  Heading,
  Label,
  Modal,
  ModalOverlay,
  Radio,
  RadioGroup,
} from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { membersQuery, meQuery, workspacesQuery } from "../api/queries";
import { ErrorAlert } from "./page";

export const ROLES = ["admin", "member", "viewer"] as const;
export type Role = (typeof ROLES)[number];

export function isRole(value: string): value is Role {
  return (ROLES as readonly string[]).includes(value);
}

export function roleLabel(role: string): string {
  if (role === "admin") return t`Beheerder`;
  if (role === "member") return t`Lid`;
  if (role === "viewer") return t`Meekijker`;
  return role;
}

function roleSentence(role: Role): string {
  if (role === "admin") return t`Kan leden uitnodigen en alles instellen.`;
  if (role === "member") return t`Kan post scannen, controleren en ordenen.`;
  return t`Kan alles lezen, maar niets wijzigen.`;
}

/** Three roles, each with one plain sentence. The name of a choice is the role alone;
 * the sentence is its description. */
export function RoleChoice({
  value,
  onChange,
  label,
}: {
  value: Role;
  onChange: (role: Role) => void;
  label: string;
}) {
  const prefix = useId();
  return (
    <RadioGroup
      className="choice"
      value={value}
      onChange={(next) => {
        if (isRole(next)) onChange(next);
      }}
    >
      <Label className="choice-label">{label}</Label>
      <div className="cards cards-stack">
        {ROLES.map((role) => (
          <Radio
            key={role}
            value={role}
            className="card"
            aria-label={roleLabel(role)}
            aria-describedby={`${prefix}-${role}`}
          >
            <span className="card-top" aria-hidden="true">
              <strong>{roleLabel(role)}</strong>
              <span className="radio-indicator" />
            </span>
            <span id={`${prefix}-${role}`}>{roleSentence(role)}</span>
          </Radio>
        ))}
      </div>
    </RadioGroup>
  );
}

type RoleMember = { user_id: string; name: string; role: string };

function RoleForm({
  workspaceId,
  member,
  onClose,
}: {
  workspaceId: string;
  member: RoleMember;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const router = useRouter();
  const { data: me } = useQuery(meQuery);
  const [role, setRole] = useState<Role>(isRole(member.role) ? member.role : "member");
  const save = useMutation({
    mutationFn: async () => {
      const { error } = await api.PUT("/api/v1/workspaces/{workspace_id}/members/{user_id}/role", {
        params: { path: { workspace_id: workspaceId, user_id: member.user_id } },
        body: { role },
      });
      if (error !== undefined) throw new ApiFailure(error);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: membersQuery(workspaceId).queryKey });
      if (member.user_id === me?.id) {
        // My own role changed: the pages know it from the route, so ask the server again
        // and let every admin control and menu entry go at once.
        await queryClient.invalidateQueries({ queryKey: workspacesQuery.queryKey });
        await router.invalidate();
      }
      onClose();
    },
  });
  return (
    <>
      <Heading slot="title" className="dialog-title">
        {t`Rol van ${member.name}`}
      </Heading>
      <Form
        className="form"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <RoleChoice value={role} onChange={setRole} label={t`Rol`} />
        <ErrorAlert error={save.error} />
        <div className="dialog-actions">
          <Button className="button secondary" onPress={onClose}>
            {t`Annuleren`}
          </Button>
          <Button type="submit" className="button" isPending={save.isPending}>
            {t`Opslaan`}
          </Button>
        </div>
      </Form>
    </>
  );
}

/** Changes the role of one member: three plain choices and Opslaan. */
export function RoleDialog({
  workspaceId,
  member,
  isOpen,
  onOpenChange,
}: {
  workspaceId: string;
  member: RoleMember;
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <ModalOverlay isOpen={isOpen} onOpenChange={onOpenChange} isDismissable className="overlay">
      <Modal className="modal">
        <Dialog className="dialog" aria-label={t`Rol van ${member.name}`}>
          <RoleForm workspaceId={workspaceId} member={member} onClose={() => onOpenChange(false)} />
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}
