from datetime import datetime

from ninja import Schema


class AiStatusOut(Schema):
    # `off`, `external` or `local`.
    mode: str
    # For `external` with consent in force: `mistral`, `openai`, `claude` or `custom`.
    supplier: str | None = None
    # Host of the external service the text of letters goes to; never for `local`.
    host: str | None = None
    # When somebody agreed to this external service, or when `local` was set. Empty for
    # `external` means no consent is recorded: no letter is read until there is one.
    since: datetime | None = None
