"""A workspace's own settings (D 6.3), changed by its admins.

Part B adds its settings as further optional fields: a field left out (None) leaves the
setting as it is, so an older client never wipes a newer setting. The AI setting has its
own service in `apps.pipeline.settings_service` because it needs consent.
"""

from uuid import UUID

from django.db import transaction
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, record
from apps.common.errors import ApiError
from apps.workspaces.models import Membership, Workspace

SUMMARY_LANGUAGES = ("nl", "en")


def update_settings(
    *,
    workspace: Workspace,
    actor: AuditActor,
    allow_member_export: bool | None = None,
    summary_language: str | None = None,
    care_is_private: bool | None = None,
    lingering_enabled: bool | None = None,
    lingering_inbox_days: int | None = None,
    lingering_unopened_days: int | None = None,
) -> Workspace:
    """Change the given settings and log each one that really changed, in the same
    transaction. The workspace row is locked, so two admins act one after the other.
    "Blijft liggen" is an organisation's: switching it on at home is 422."""
    if summary_language is not None and summary_language not in SUMMARY_LANGUAGES:
        raise ValueError(f"Unknown summary language {summary_language!r}")
    for days, most in ((lingering_inbox_days, 30), (lingering_unopened_days, 60)):
        if days is not None and not 1 <= days <= most:
            raise ValueError(f"Lingering days out of range: {days!r}")
    changes: dict[str, bool | str | int] = {}
    if allow_member_export is not None:
        changes["allow_member_export"] = allow_member_export
    if summary_language is not None:
        changes["summary_language"] = summary_language
    if care_is_private is not None:
        changes["care_is_private"] = care_is_private
    if lingering_enabled is not None:
        changes["lingering_enabled"] = lingering_enabled
    if lingering_inbox_days is not None:
        changes["lingering_inbox_days"] = lingering_inbox_days
    if lingering_unopened_days is not None:
        changes["lingering_unopened_days"] = lingering_unopened_days
    with transaction.atomic():
        locked = Workspace.objects.select_for_update().get(id=workspace.id)
        if lingering_enabled and locked.kind != Workspace.Kind.BUSINESS:
            raise ApiError(422, "validation_error", _("Dit kan alleen in een organisatie."))
        for setting, value in changes.items():
            if getattr(locked, setting) == value:
                continue
            setattr(locked, setting, value)
            locked.save(update_fields=[setting, "updated_at"])
            record(
                AuditCode.WORKSPACE_SETTING_CHANGED,
                actor=actor,
                target=AuditTarget(TargetKind.WORKSPACE, locked.id, locked.name),
                workspace_id=locked.id,
                values={"setting": setting, "value": value},
            )
        return locked


def set_emergency_access(
    *, workspace_id: UUID, enabled: bool, user: User, actor: AuditActor
) -> Workspace:
    """Switch "Noodtoegang voor de beheerder" (S 4.7, D 8.5): a server admin only, member of
    the workspace or not (403 `forbidden`, 404 `workspace_not_found`). A change is audited
    and every member gets a notice; the same value again writes nothing."""
    from apps.accounts.notices import notify  # notices import the services that import this

    if not user.is_server_admin:
        raise ApiError(403, "forbidden", _("Dit kan alleen de serverbeheerder."))
    with transaction.atomic():
        locked = Workspace.objects.select_for_update(no_key=True).filter(id=workspace_id).first()
        if locked is None:
            raise ApiError(404, "workspace_not_found", _("We kunnen deze werkruimte niet vinden."))
        if locked.emergency_access == enabled:
            return locked
        locked.emergency_access = enabled
        locked.save(update_fields=["emergency_access", "updated_at"])
        if not enabled:
            # Off means off: an hour that is running ends with the switch.
            from apps.documents import visibility

            visibility.end_emergency_access(locked.id)
        code = (
            AuditCode.EMERGENCY_ACCESS_SWITCHED_ON
            if enabled
            else AuditCode.EMERGENCY_ACCESS_SWITCHED_OFF
        )
        record(
            code,
            actor=actor,
            target=AuditTarget(TargetKind.WORKSPACE, locked.id, locked.name),
            workspace_id=locked.id,
        )
        for membership in Membership.objects.filter(workspace=locked).select_related("user"):
            notify(
                membership.user,
                str(code),
                actor_name=user.name,
                values={"workspace": locked.name},
                workspace_id=locked.id,
            )
        return locked
