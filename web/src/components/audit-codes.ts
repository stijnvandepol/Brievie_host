import { t } from "@lingui/core/macro";

/**
 * The closed list of what the log records, in the order of the server's list, with a Dutch
 * label each. A code the web does not know yet (a newer server) shows the label the server
 * sent with the row.
 */
/** What the server log holds: accounts, sessions, devices and server settings. */
export const SERVER_CODES = [
  "sign_in_succeeded",
  "sign_in_failed",
  "sign_in_locked_out",
  "second_factor_added",
  "second_factor_removed",
  "second_factor_cleared",
  "password_changed",
  "password_reset_created",
  "session_ended",
  "device_paired",
  "device_revoked",
  "device_expired",
  "server_admin_granted",
  "server_admin_revoked",
  "sso_settings_changed",
] as const;

/** What a workspace log holds: members, settings, rules, labels, people, exports and the trash. */
export const WORKSPACE_CODES = [
  "invitation_created",
  "invitation_revoked",
  "invitation_accepted",
  "member_role_changed",
  "member_removed",
  "member_left",
  "workspace_setting_changed",
  "rule_created",
  "rule_changed",
  "rule_deleted",
  "rule_run",
  "label_deleted",
  "person_deleted",
  "visibility_changed",
  "person_linked",
  "person_unlinked",
  "department_member_added",
  "department_member_removed",
  "department_lead_changed",
  "emergency_access_switched_on",
  "emergency_access_switched_off",
  "emergency_access_used",
  "export_created",
  "export_downloaded",
  "document_trashed",
  "document_restored",
  "document_purged",
] as const;

export const AUDIT_CODES = [...SERVER_CODES, ...WORKSPACE_CODES] as const;

export type AuditCodeName = (typeof AUDIT_CODES)[number];

function isKnown(code: string): code is AuditCodeName {
  return (AUDIT_CODES as readonly string[]).includes(code);
}

function knownLabel(code: AuditCodeName): string {
  switch (code) {
    case "sign_in_succeeded":
      return t`Ingelogd`;
    case "sign_in_failed":
      return t`Inloggen mislukt`;
    case "sign_in_locked_out":
      return t`Inloggen tijdelijk geblokkeerd`;
    case "second_factor_added":
      return t`Tweestapsverificatie aangezet`;
    case "second_factor_removed":
      return t`Tweestapsverificatie uitgezet`;
    case "second_factor_cleared":
      return t`Tweestapsverificatie gewist door beheerder`;
    case "password_changed":
      return t`Wachtwoord gewijzigd`;
    case "password_reset_created":
      return t`Herstellink gemaakt`;
    case "session_ended":
      return t`Uitgelogd`;
    case "device_paired":
      return t`Apparaat gekoppeld`;
    case "device_revoked":
      return t`Apparaat ontkoppeld`;
    case "device_expired":
      return t`Apparaat verlopen`;
    case "server_admin_granted":
      return t`Serverbeheerder gemaakt`;
    case "server_admin_revoked":
      return t`Geen serverbeheerder meer`;
    case "sso_settings_changed":
      return t`Instellingen voor inloggen via organisatie gewijzigd`;
    case "invitation_created":
      return t`Uitnodiging gemaakt`;
    case "invitation_revoked":
      return t`Uitnodiging ingetrokken`;
    case "invitation_accepted":
      return t`Uitnodiging aangenomen`;
    case "member_role_changed":
      return t`Rol gewijzigd`;
    case "member_removed":
      return t`Lid verwijderd`;
    case "member_left":
      return t`Lid vertrokken`;
    case "workspace_setting_changed":
      return t`Instelling gewijzigd`;
    case "rule_created":
      return t`Regel gemaakt`;
    case "rule_changed":
      return t`Regel gewijzigd`;
    case "rule_deleted":
      return t`Regel verwijderd`;
    case "rule_run":
      return t`Regel met de hand uitgevoerd`;
    case "label_deleted":
      return t`Label verwijderd`;
    case "person_deleted":
      return t`Persoon verwijderd`;
    case "visibility_changed":
      return t`Zichtbaarheid gewijzigd`;
    case "person_linked":
      return t`Persoon gekoppeld aan een account`;
    case "person_unlinked":
      return t`Persoon losgekoppeld van een account`;
    case "department_member_added":
      return t`Lid aan afdeling toegevoegd`;
    case "department_member_removed":
      return t`Lid uit afdeling gehaald`;
    case "department_lead_changed":
      return t`Leidinggevende van afdeling gewijzigd`;
    case "emergency_access_switched_on":
      return t`Noodtoegang aangezet`;
    case "emergency_access_switched_off":
      return t`Noodtoegang uitgezet`;
    case "emergency_access_used":
      return t`Noodtoegang gebruikt`;
    case "export_created":
      return t`Export gemaakt`;
    case "export_downloaded":
      return t`Export gedownload`;
    case "document_trashed":
      return t`Brief naar de prullenbak`;
    case "document_restored":
      return t`Brief teruggezet`;
    case "document_purged":
      return t`Brief definitief verwijderd`;
  }
}

/** The Dutch label of an event code; `fallback` (the server's label) for an unknown one. */
export function auditLabel(code: string, fallback: string): string {
  return isKnown(code) ? knownLabel(code) : fallback || code;
}

/** Who did it, for a row without a name: the kind of actor. */
export function actorKindLabel(kind: string): string {
  if (kind === "user") return t`Gebruiker`;
  if (kind === "device") return t`Apparaat`;
  if (kind === "system") return t`Systeem`;
  if (kind === "sso") return t`Inloggen via organisatie`;
  return kind;
}

/** What it was about, for a row without a name (a label, a rule, a document keeps only its id). */
export function targetKindLabel(kind: string): string {
  switch (kind) {
    case "user":
      return t`Gebruiker`;
    case "invitation":
      return t`Uitnodiging`;
    case "device":
      return t`Apparaat`;
    case "session":
      return t`Sessie`;
    case "rule":
      return t`Regel`;
    case "label":
      return t`Label`;
    case "person":
      return t`Persoon`;
    case "document":
      return t`Document`;
    case "workspace":
      return t`Werkruimte`;
    case "export":
      return t`Export`;
    default:
      return kind;
  }
}
