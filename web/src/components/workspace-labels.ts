import { t } from "@lingui/core/macro";

/** "Thuis" or "Organisatie": the kind of a workspace in words. */
export function kindLabel(kind: string): string {
  if (kind === "home") return t`Thuis`;
  if (kind === "business") return t`Organisatie`;
  return kind;
}

export function roleLabel(role: string): string {
  if (role === "admin") return t`Beheerder`;
  if (role === "member") return t`Lid`;
  if (role === "viewer") return t`Alleen lezen`;
  return role;
}
