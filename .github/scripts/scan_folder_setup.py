"""Sets up the scan folder for the CI job `scan-folder-proof` (.github/workflows/ci.yml).

Run inside the api container: `... exec -T api python manage.py shell < this file`.
Makes one user and a workspace whose scan folder `kantoor` is on (folders per person off),
through the app's own models. Prints ONLY one JSON line: {"workspace": "<uuid>"}. The
user's password is random and never printed.
"""

import json
import secrets
import uuid

from apps.accounts.models import User
from apps.intake.models import ScanFolderSettings
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.services import create_workspace

user = User.objects.create_user(
    f"scan-proof-{uuid.uuid4().hex[:8]}@example.com",
    secrets.token_urlsafe(24),
    name="Scanmap proef",
)
workspace = create_workspace(name="Kantoor", kind="business", admin=user)
with workspace_context(workspace.id, actor=SYSTEM):
    ScanFolderSettings.objects.create(workspace=workspace, enabled=True, folder_name="kantoor")
print(json.dumps({"workspace": str(workspace.id)}))
