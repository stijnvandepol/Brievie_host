from datetime import date, datetime
from uuid import UUID

from ninja import Schema


class TaskItemOut(Schema):
    id: UUID
    type: str
    description: str
    deadline: date | None
    deadline_source: str
    # "open" or "done".
    status: str
    completed_at: datetime | None
    document_id: UUID | None
    # The document's confirmed sender; empty without a document.
    sender: str
    updated_at: datetime
    # True when the autopilot made it while confirming the letter ("Automatisch door AI").
    created_by_ai: bool = False

    @staticmethod
    def resolve_sender(obj: object) -> str:
        document = getattr(obj, "document", None)
        return document.sender if document is not None else ""
