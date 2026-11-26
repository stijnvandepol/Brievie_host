"""The Scanmap page (intake spec S 3.1, 3.2a): a workspace admin switches the scan folder on,
names it, and manages the folders per person or department. Each switch that changes is one
audit row; a folder's or person's name is never in one."""

from collections import Counter
from datetime import datetime, timedelta
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Min
from django.db.models.functions import Lower

from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, record
from apps.common.privacy import mask_bsns
from apps.intake.models import ScanFile, ScanFolder, ScanFolderSettings
from apps.intake.scan_rules import (
    default_folder_name,
    person_folder_names,
    valid_folder_name,
    valid_person_folder_name,
)
from apps.intake.schemas import (
    ScanFailureOut,
    ScanFolderOut,
    ScanSubfolderOut,
    ScanUnknownFolderOut,
)
from apps.people.models import Person
from apps.workspaces.models import Workspace

# Without a look this recent, the intake service does not run.
CHECK_FRESH = timedelta(minutes=5)


class ScanFolderError(Exception):
    """`reason`: unavailable, taken, invalid_name, person_not_found, folder_not_found,
    subfolder_taken or departments_off."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def scan_folder_status(workspace_id: UUID, *, now: datetime) -> ScanFolderOut:
    """What the Scanmap page shows. Needs the workspace's RLS context."""
    workspace = Workspace.objects.get(id=workspace_id)
    stored = ScanFolderSettings.objects.filter(workspace_id=workspace_id).first()
    base = ScanFolderOut(
        available=settings.BRIEVIE_SCAN_DIR_SET,
        suggested_name=default_folder_name(workspace.name),
        departments_enabled=workspace.departments_enabled,
    )
    if stored is None:
        return base
    fresh = stored.checked_at is not None and now - stored.checked_at <= CHECK_FRESH
    seen = ScanFile.objects.filter(state=ScanFile.State.SEEN)
    waiting = seen.aggregate(count=Count("id"), oldest=Min("created_at"))
    oldest = waiting["oldest"]
    failed_rows = ScanFile.objects.filter(state=ScanFile.State.FAILED).order_by("-updated_at")
    per_folder: Counter[str] = Counter()
    for path in seen.values_list("relative_path", flat=True):
        parts = path.split("/")
        if len(parts) == 3 and parts[0] == stored.folder_name:
            per_folder[parts[1].casefold()] += 1
    folders = []
    for folder in ScanFolder.objects.select_related("person").order_by(Lower("name")):
        person = folder.person
        folders.append(
            ScanSubfolderOut(
                id=folder.id,
                name=folder.name,
                person_id=folder.person_id,
                person_name=person.display_name if person is not None else "",
                kind=person.kind if person is not None else "",
                state=folder.state,
                waiting_count=per_folder[folder.name.casefold()],
            )
        )
    return base.model_copy(
        update={
            "enabled": stored.enabled,
            "folder_name": stored.folder_name,
            "per_person": stored.per_person,
            "check": stored.check_state if fresh else "unknown",
            "owner_uid": stored.owner_uid,
            "owner_gid": stored.owner_gid,
            "waiting_count": waiting["count"],
            "skipped_count": stored.skipped_count,
            "oldest_waiting_minutes": int((now - oldest).total_seconds() // 60)
            if oldest is not None
            else 0,
            "failed": [
                ScanFailureOut(
                    name=mask_bsns(row.relative_path.rsplit("/", 1)[-1]),
                    reason=row.reason,
                    failed_at=row.updated_at,
                )
                for row in failed_rows[:200]
            ],
            "folders": folders,
            "unknown_folders": [
                ScanUnknownFolderOut(
                    name=str(item.get("name", "")), files=int(item.get("files", 0))
                )
                for item in stored.unknown_folders
                if isinstance(item, dict)
            ],
        }
    )


def configure_scan_folder(
    *, workspace_id: UUID, enabled: bool, folder_name: str, per_person: bool, actor: AuditActor
) -> None:
    """Switch the scan folder and its folders per person; name the workspace's folder.

    An empty name keeps the stored one, or takes the suggested one when there is none.
    A name another workspace uses is `taken` (the unique index, across workspaces)."""
    if enabled and not settings.BRIEVIE_SCAN_DIR_SET:
        raise ScanFolderError("unavailable")
    with transaction.atomic():
        workspace = Workspace.objects.select_for_update().get(id=workspace_id)
        stored, _created = ScanFolderSettings.objects.select_for_update().get_or_create(
            workspace_id=workspace_id
        )
        name = folder_name.strip() or stored.folder_name
        if not name and enabled:
            name = default_folder_name(workspace.name)
        if name and not valid_folder_name(name):
            raise ScanFolderError("invalid_name")
        changes: dict[str, bool | str] = {
            "scan_folder": enabled,
            "scan_folder_per_person": per_person,
        }
        before: dict[str, bool | str] = {
            "scan_folder": stored.enabled,
            "scan_folder_per_person": stored.per_person,
        }
        if stored.folder_name and name != stored.folder_name:
            # Another folder on the share: audited, the name itself never (review of PR #64).
            changes["scan_folder_name"] = "changed"
            before["scan_folder_name"] = ""
        stored.enabled, stored.per_person, stored.folder_name = enabled, per_person, name
        try:
            with transaction.atomic():
                stored.save()
        except IntegrityError:
            raise ScanFolderError("taken") from None
        for setting, value in changes.items():
            if before[setting] != value:
                record(
                    AuditCode.WORKSPACE_SETTING_CHANGED,
                    actor=actor,
                    target=AuditTarget(TargetKind.WORKSPACE, workspace_id),
                    workspace_id=workspace_id,
                    values={"setting": setting, "value": value},
                )


def add_scan_folder(*, workspace_id: UUID, person_id: UUID, name: str, actor: AuditActor) -> None:
    """ "Map maken" or "Koppel aan …": a folder for this person or department, made by an
    admin (also for a person without an account). A stopped folder is read again."""
    with transaction.atomic():
        workspace = Workspace.objects.get(id=workspace_id)
        person = Person.objects.filter(id=person_id, archived_at__isnull=True).first()
        if person is None:
            raise ScanFolderError("person_not_found")
        if person.kind == Person.Kind.DEPARTMENT and not workspace.departments_enabled:
            raise ScanFolderError("departments_off")
        folder = ScanFolder.objects.select_for_update().filter(person=person).first()
        others = ScanFolder.objects.exclude(id=folder.id) if folder else ScanFolder.objects.all()
        wanted = name.strip()
        if wanted:
            if not valid_person_folder_name(wanted):
                raise ScanFolderError("invalid_name")
        elif folder is not None:
            wanted = folder.name
        else:
            taken = {row.person_id or row.id: row.name for row in others}
            wanted = person_folder_names({person.id: person.display_name}, taken=taken)[person.id]
        # The job matches folders with casefold() (SMB ignores case; "Straße" is "STRASSE").
        folded = wanted.casefold()
        if any(name.casefold() == folded for name in others.values_list("name", flat=True)):
            raise ScanFolderError("subfolder_taken")
        if folder is None:
            folder = ScanFolder(workspace_id=workspace_id, person=person)
        folder.name, folder.added_by_admin, folder.state = wanted, True, ScanFolder.State.ACTIVE
        try:
            with transaction.atomic():
                folder.save()
        except IntegrityError:
            raise ScanFolderError("subfolder_taken") from None
        record(
            AuditCode.WORKSPACE_SETTING_CHANGED,
            actor=actor,
            target=AuditTarget(TargetKind.WORKSPACE, workspace_id),
            workspace_id=workspace_id,
            values={"setting": "scan_folder_person", "value": "added"},
        )


def remove_scan_folder(*, workspace_id: UUID, folder_id: UUID, actor: AuditActor) -> None:
    """ "Niet meer lezen": the folder is stopped. Nothing on the share is touched; the folder
    and its files stay and it is listed as an unknown folder."""
    with transaction.atomic():
        folder = ScanFolder.objects.select_for_update().filter(id=folder_id).first()
        if folder is None:
            raise ScanFolderError("folder_not_found")
        if folder.state == ScanFolder.State.STOPPED:
            return
        folder.state = ScanFolder.State.STOPPED
        folder.save(update_fields=["state", "updated_at"])
        record(
            AuditCode.WORKSPACE_SETTING_CHANGED,
            actor=actor,
            target=AuditTarget(TargetKind.WORKSPACE, workspace_id),
            workspace_id=workspace_id,
            values={"setting": "scan_folder_person", "value": "removed"},
        )
