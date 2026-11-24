from datetime import datetime
from uuid import UUID

from ninja import Schema


class BackupStatusOut(Schema):
    """What the web interface shows of the back-up. No path, key, credential or address."""

    # none (nothing set up), folder, remote, or invalid (the settings contradict each other).
    destination: str
    last_success_at: datetime | None
    last_success_size_bytes: int | None
    last_failure_at: datetime | None
    # The server's own sentence (static, in the language of the request); "" when none.
    last_failure_reason: str
    last_check_at: datetime | None
    running: bool
    # A destination is set but the last good back-up is older than 36 hours (or none yet).
    late: bool


class BackupRunOut(Schema):
    run_id: UUID
