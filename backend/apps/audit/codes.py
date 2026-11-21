"""The closed list of what the audit log records (spec 1d, D 11).

A code is added here, with its allowed values, before any service may write it. A
row never holds a letter's sender, summary, text or a label name: rows about labels
and rules carry ids only, because a rule's name can hold a label name.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _


class AuditCode(models.TextChoices):
    # Server: accounts, sessions, devices, server settings.
    SIGN_IN_SUCCEEDED = "sign_in_succeeded", _("Ingelogd")
    SIGN_IN_FAILED = "sign_in_failed", _("Inloggen mislukt")
    SIGN_IN_LOCKED_OUT = "sign_in_locked_out", _("Inloggen tijdelijk geblokkeerd")
    SECOND_FACTOR_ADDED = "second_factor_added", _("Tweestapsverificatie aangezet")
    SECOND_FACTOR_REMOVED = "second_factor_removed", _("Tweestapsverificatie uitgezet")
    SECOND_FACTOR_CLEARED = "second_factor_cleared", _("Tweestapsverificatie gewist door beheerder")
    PASSWORD_CHANGED = "password_changed", _("Wachtwoord gewijzigd")
    PASSWORD_RESET_CREATED = "password_reset_created", _("Herstellink gemaakt")
    SESSION_ENDED = "session_ended", _("Uitgelogd")
    DEVICE_PAIRED = "device_paired", _("Apparaat gekoppeld")
    DEVICE_REVOKED = "device_revoked", _("Apparaat ontkoppeld")
    DEVICE_EXPIRED = "device_expired", _("Apparaat verlopen")
    SERVER_ADMIN_GRANTED = "server_admin_granted", _("Serverbeheerder gemaakt")
    SERVER_ADMIN_REVOKED = "server_admin_revoked", _("Geen serverbeheerder meer")
    SSO_SETTINGS_CHANGED = (
        "sso_settings_changed",
        _("Instellingen voor inloggen via organisatie gewijzigd"),
    )
    # Workspace: members, settings, rules, labels, people, private post, exports.
    INVITATION_CREATED = "invitation_created", _("Uitnodiging gemaakt")
    INVITATION_REVOKED = "invitation_revoked", _("Uitnodiging ingetrokken")
    INVITATION_ACCEPTED = "invitation_accepted", _("Uitnodiging aangenomen")
    MEMBER_ROLE_CHANGED = "member_role_changed", _("Rol gewijzigd")
    MEMBER_REMOVED = "member_removed", _("Lid verwijderd")
    MEMBER_LEFT = "member_left", _("Lid vertrokken")
    WORKSPACE_SETTING_CHANGED = "workspace_setting_changed", _("Instelling gewijzigd")
    RULE_CREATED = "rule_created", _("Regel gemaakt")
    RULE_CHANGED = "rule_changed", _("Regel gewijzigd")
    RULE_DELETED = "rule_deleted", _("Regel verwijderd")
    RULE_RUN = "rule_run", _("Regel met de hand uitgevoerd")
    LABEL_DELETED = "label_deleted", _("Label verwijderd")
    PERSON_DELETED = "person_deleted", _("Persoon verwijderd")
    VISIBILITY_CHANGED = "visibility_changed", _("Zichtbaarheid gewijzigd")
    PERSON_LINKED = "person_linked", _("Persoon gekoppeld aan een account")
    PERSON_UNLINKED = "person_unlinked", _("Persoon losgekoppeld van een account")
    DEPARTMENT_MEMBER_ADDED = "department_member_added", _("Lid aan afdeling toegevoegd")
    DEPARTMENT_MEMBER_REMOVED = "department_member_removed", _("Lid uit afdeling gehaald")
    EMERGENCY_ACCESS_SWITCHED_ON = "emergency_access_switched_on", _("Noodtoegang aangezet")
    EMERGENCY_ACCESS_SWITCHED_OFF = "emergency_access_switched_off", _("Noodtoegang uitgezet")
    EMERGENCY_ACCESS_USED = "emergency_access_used", _("Noodtoegang gebruikt")
    EXPORT_CREATED = "export_created", _("Export gemaakt")
    EXPORT_DOWNLOADED = "export_downloaded", _("Export gedownload")
    # The trash: a letter's id only, never its sender, summary or text.
    DOCUMENT_TRASHED = "document_trashed", _("Brief naar de prullenbak")
    DOCUMENT_RESTORED = "document_restored", _("Brief teruggezet")
    DOCUMENT_PURGED = "document_purged", _("Brief definitief verwijderd")


class ActorKind(models.TextChoices):
    USER = "user", _("Gebruiker")
    DEVICE = "device", _("Apparaat")
    SYSTEM = "system", _("Systeem")
    SSO = "sso", _("Inloggen via organisatie")


class TargetKind(models.TextChoices):
    USER = "user", _("Gebruiker")
    INVITATION = "invitation", _("Uitnodiging")
    DEVICE = "device", _("Apparaat")
    SESSION = "session", _("Sessie")
    RULE = "rule", _("Regel")
    LABEL = "label", _("Label")
    PERSON = "person", _("Persoon")
    DOCUMENT = "document", _("Document")
    WORKSPACE = "workspace", _("Werkruimte")
    EXPORT = "export", _("Export")


# Targets whose row keeps a name: people and accounts (the name as it was then), a
# phone's name, an invitation's invitee and the workspace. Every other kind (a label,
# a rule, a document, an export, a session) is an id only: their names can hold a label
# name or a letter's subject.
NAMED_TARGETS: frozenset[str] = frozenset(
    {
        TargetKind.USER,
        TargetKind.PERSON,
        TargetKind.INVITATION,
        TargetKind.DEVICE,
        TargetKind.WORKSPACE,
    }
)

_NONE: frozenset[str] = frozenset()

# The only value keys a row of each code may carry. Values are plain text, numbers or
# flags (a role, a reason, a count), never content.
ALLOWED_VALUES: dict[AuditCode, frozenset[str]] = {
    AuditCode.SIGN_IN_SUCCEEDED: frozenset({"via"}),
    AuditCode.SIGN_IN_FAILED: _NONE,
    # via: password (signing in), password_change (wrong current passwords), invitation
    # (accepting with an existing account), reauthenticate (typing the password again).
    AuditCode.SIGN_IN_LOCKED_OUT: frozenset({"via"}),
    AuditCode.SECOND_FACTOR_ADDED: _NONE,
    AuditCode.SECOND_FACTOR_REMOVED: _NONE,
    AuditCode.SECOND_FACTOR_CLEARED: _NONE,
    # via: self, reset, command; reset_by: the id of the reset link's maker (via reset).
    AuditCode.PASSWORD_CHANGED: frozenset({"via", "reset_by"}),
    AuditCode.PASSWORD_RESET_CREATED: _NONE,
    # reason: self, admin, password_changed, reset, deactivated.
    AuditCode.SESSION_ENDED: frozenset({"count", "reason"}),
    # via: code, proof.
    AuditCode.DEVICE_PAIRED: frozenset({"via"}),
    AuditCode.DEVICE_REVOKED: _NONE,
    AuditCode.DEVICE_EXPIRED: _NONE,
    AuditCode.SERVER_ADMIN_GRANTED: _NONE,
    AuditCode.SERVER_ADMIN_REVOKED: _NONE,
    AuditCode.SSO_SETTINGS_CHANGED: _NONE,
    AuditCode.INVITATION_CREATED: frozenset({"role", "expires_in_days"}),
    # reason: admin, renewed, creator_lost_role.
    AuditCode.INVITATION_REVOKED: frozenset({"reason"}),
    AuditCode.INVITATION_ACCEPTED: frozenset({"role", "new_account", "account_reactivated"}),
    AuditCode.MEMBER_ROLE_CHANGED: frozenset({"from", "to"}),
    AuditCode.MEMBER_REMOVED: frozenset({"account_deactivated"}),
    AuditCode.MEMBER_LEFT: frozenset({"account_deactivated"}),
    AuditCode.WORKSPACE_SETTING_CHANGED: frozenset({"setting", "value"}),
    AuditCode.RULE_CREATED: _NONE,
    AuditCode.RULE_CHANGED: _NONE,
    AuditCode.RULE_DELETED: _NONE,
    AuditCode.RULE_RUN: _NONE,
    AuditCode.LABEL_DELETED: _NONE,
    AuditCode.PERSON_DELETED: _NONE,
    # A share added or removed is a visibility change with `share` and `member`.
    AuditCode.VISIBILITY_CHANGED: frozenset({"visibility", "origin", "share", "member"}),
    AuditCode.PERSON_LINKED: _NONE,
    AuditCode.PERSON_UNLINKED: _NONE,
    AuditCode.DEPARTMENT_MEMBER_ADDED: frozenset({"member"}),
    AuditCode.DEPARTMENT_MEMBER_REMOVED: frozenset({"member"}),
    AuditCode.EMERGENCY_ACCESS_SWITCHED_ON: _NONE,
    AuditCode.EMERGENCY_ACCESS_SWITCHED_OFF: _NONE,
    AuditCode.EMERGENCY_ACCESS_USED: frozenset({"reason", "document_count"}),
    AuditCode.EXPORT_CREATED: frozenset({"document_count"}),
    AuditCode.EXPORT_DOWNLOADED: _NONE,
    # reason: user (later duplicate).
    AuditCode.DOCUMENT_TRASHED: frozenset({"reason"}),
    AuditCode.DOCUMENT_RESTORED: _NONE,
    # reason: admin, emptied, expired.
    AuditCode.DOCUMENT_PURGED: frozenset({"reason"}),
}
