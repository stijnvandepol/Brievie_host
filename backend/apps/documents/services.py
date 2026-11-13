from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _
from psycopg import errors as pg_errors

from apps.common.errors import ApiError
from apps.documents.models import Folder
from apps.workspaces.models import Workspace

_PATH_UNIQUE = "documents_folder_workspace_path_unique"


def list_folders(workspace: Workspace) -> list[Folder]:
    """Folders of the workspace whose RLS context the caller has entered."""
    return list(Folder.objects.filter(workspace=workspace).order_by("path"))


def create_folder(*, workspace: Workspace, name: str, parent_id: UUID | None) -> Folder:
    """Create a folder; needs the workspace's RLS context (see workspaces.deps).

    A parent is looked up under RLS, so one from another workspace is not found.
    """
    with transaction.atomic():
        parent = None
        if parent_id is not None:
            parent = Folder.objects.filter(workspace=workspace, id=parent_id).first()
            if parent is None:
                raise ApiError(404, "folder_not_found", _("We kunnen deze map niet vinden."))
        folder = Folder(workspace=workspace, parent=parent, name=name)
        try:
            with transaction.atomic():
                folder.save()
        except ValidationError as exc:
            raise ApiError(422, "validation_error", exc.messages[0]) from exc
        except IntegrityError as exc:
            cause = exc.__cause__
            if (
                isinstance(cause, pg_errors.UniqueViolation)
                and cause.diag.constraint_name == _PATH_UNIQUE
            ):
                raise ApiError(
                    409, "folder_exists", _("Er is al een map met deze naam op deze plek.")
                ) from exc
            raise
    return folder
