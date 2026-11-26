"""The Scanmap page's schemas (intake plan Task 13)."""

from datetime import datetime
from uuid import UUID

from ninja import Schema
from pydantic import Field

from apps.common.schemas import InputSchema


class ScanFailureOut(Schema):
    # The file's name in mislukt/ (BSN-masked); the admin finds it there.
    name: str
    # The refusal code: file_type_not_supported, file_too_large, too_many_pages,
    # file_encrypted, file_unreadable, image_too_large, person_gone or unreadable.
    reason: str
    failed_at: datetime


class ScanSubfolderOut(Schema):
    id: UUID
    # The folder's name on the share.
    name: str
    person_id: UUID | None = None
    # The current name of the person or department (it may differ from the folder's).
    person_name: str = ""
    # "person" or "department"; empty when the person was deleted.
    kind: str = ""
    # "active", "paused" (a department while departments are off), "stopped" or "gone".
    state: str
    waiting_count: int = 0


class ScanUnknownFolderOut(Schema):
    # A folder in the workspace folder that maps to nobody (BSN-masked); not read.
    name: str
    files: int = 0


class ScanFolderOut(Schema):
    # The server has a scan folder (BRIEVIE_SCAN_DIR); without it the page is not offered.
    available: bool = False
    enabled: bool = False
    folder_name: str = ""
    # The name the page proposes when none is chosen yet (from the workspace's name).
    suggested_name: str = ""
    per_person: bool = False
    # The intake service's own look: ok, missing, not_readable, not_writable, failing (the
    # last round broke off), taken (the
    # folder belongs to another workspace: `.brievie-werkruimte` names another), or unknown
    # (no look in the last 5 minutes: the service does not run).
    check: str = "unknown"
    owner_uid: int = 0
    owner_gid: int = 0
    # Files seen and not yet taken, and how long the oldest has waited.
    waiting_count: int = 0
    oldest_waiting_minutes: int = 0
    # Files the last round left out: too many in a folder or in the workspace, or a file
    # whose name or date did not fit.
    skipped_count: int = 0
    failed: list[ScanFailureOut] = Field(default_factory=list)
    folders: list[ScanSubfolderOut] = Field(default_factory=list)
    unknown_folders: list[ScanUnknownFolderOut] = Field(default_factory=list)
    departments_enabled: bool = False


class ScanFolderIn(InputSchema):
    enabled: bool
    # Empty: keep the stored name, or (switching on without one) the suggested name.
    folder_name: str = ""
    per_person: bool = False


class ScanSubfolderIn(InputSchema):
    person_id: UUID
    # Empty: a name made from the person's name. Given: that name (it may be the name of
    # an unknown folder, which then belongs to this person).
    name: str = ""
