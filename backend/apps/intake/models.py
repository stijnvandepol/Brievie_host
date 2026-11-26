"""The scan folder (intake spec S 3): a workspace's switch and folder name, its folders per
person or department, and every file the `intake` service saw on the share."""

from typing import ClassVar

from django.db import models
from django.db.models.functions import Lower

from apps.common.models import WorkspaceModel


class ScanFolderSettings(WorkspaceModel):
    """One per workspace. `folder_name` is the workspace's folder below the scan root;
    unique across the server (one root for all workspaces): the index binds under RLS too,
    so a name another workspace took shows only as an `IntegrityError`."""

    enabled = models.BooleanField(default=False)
    folder_name = models.CharField(max_length=60, blank=True, default="")
    # "Mappen per persoon en afdeling" (spec S 3.2a).
    per_person = models.BooleanField(default=False)
    # The `intake` service's own look at the folder (`scan_files.probe`): ok, missing,
    # not_readable, not_writable, or unknown before the first look.
    check_state = models.CharField(max_length=20, default="unknown")
    owner_uid = models.IntegerField(default=0)
    owner_gid = models.IntegerField(default=0)
    checked_at = models.DateTimeField(null=True, blank=True)
    # The last round's directories that map to nobody (or were stopped):
    # [{"name": <BSN-masked>, "files": <count>}], at most 50.
    unknown_folders = models.JSONField(default=list, blank=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["workspace"], name="intake_scanfoldersettings_workspace_unique"
            ),
            models.UniqueConstraint(
                fields=["folder_name"],
                condition=~models.Q(folder_name=""),
                name="intake_scanfoldersettings_name_unique",
            ),
        ]


class ScanFolder(WorkspaceModel):
    """A folder of one person or department inside the workspace folder, by id: a renamed
    person keeps their folder. `state`: active, paused (a department while departments are
    off), stopped (an admin stopped it) or gone (the person left, or no longer qualifies)."""

    class State(models.TextChoices):
        ACTIVE = "active"
        PAUSED = "paused"
        STOPPED = "stopped"
        GONE = "gone"

    person = models.ForeignKey(
        "people.Person", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    name = models.CharField(max_length=60)
    added_by_admin = models.BooleanField(default=False)
    state = models.CharField(max_length=10, choices=State.choices, default=State.ACTIVE)

    same_workspace_fields = ("person",)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["workspace", "person"],
                condition=models.Q(person__isnull=False),
                name="intake_scanfolder_person_unique",
            ),
            # SMB is case-insensitive: two names that differ only in case are one folder.
            models.UniqueConstraint(
                models.F("workspace"), Lower("name"), name="intake_scanfolder_name_unique"
            ),
        ]


class ScanFile(WorkspaceModel):
    """One file on the share as the job saw it. `relative_path` is relative to the scan
    root and exact (it names the file on disk); it is never logged or shown."""

    class State(models.TextChoices):
        SEEN = "seen"
        IMPORTED = "imported"
        FAILED = "failed"

    relative_path = models.CharField(max_length=400)
    size = models.BigIntegerField()
    mtime_ns = models.BigIntegerField()
    device = models.BigIntegerField()
    inode = models.BigIntegerField()
    # Since when size and mtime are what they are now.
    stable_since = models.DateTimeField()
    state = models.CharField(max_length=10, choices=State.choices, default=State.SEEN)
    # The refusal code of a failed file.
    reason = models.CharField(max_length=40, blank=True, default="")
    document = models.ForeignKey(
        "documents.Document", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    removed_at = models.DateTimeField(null=True, blank=True)

    same_workspace_fields = ("document",)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["workspace", "relative_path", "device", "inode"],
                name="intake_scanfile_unique",
            ),
        ]
