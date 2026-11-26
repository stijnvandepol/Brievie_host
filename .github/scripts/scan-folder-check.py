"""Reads the state of the CI job `scan-folder-proof` (.github/workflows/ci.yml).

Run inside the api container: `... exec -T api python manage.py shell -c "$(cat this)"`
with SCAN_WORKSPACE set. Prints one JSON line: how many letters came from the scan folder
in that workspace and the intake service's stored check of the folder.
"""

import json
import os
import uuid

from apps.documents.models import Document
from apps.intake.models import ScanFolderSettings
from apps.workspaces.context import SYSTEM, workspace_context

workspace_id = uuid.UUID(os.environ["SCAN_WORKSPACE"])
with workspace_context(workspace_id, actor=SYSTEM):
    letters = Document.objects.filter(source=Document.Source.WATCH_FOLDER).count()
    stored = ScanFolderSettings.objects.get()
print(json.dumps({"letters": letters, "check": stored.check_state}))
