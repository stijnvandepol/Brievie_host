from datetime import datetime
from uuid import UUID

from ninja import Schema
from pydantic import Field

from apps.accounts.schemas import UserOut


class PairingCodeOut(Schema):
    code: str
    expires_at: datetime
    # JSON string: {"v": 1, "urls": [...], "code": "XXXX-XXXX", "ca_sha256": <hex> | null}
    qr_payload: str


class PairIn(Schema):
    code: str = Field(max_length=20)
    device_name: str = Field(min_length=1, max_length=100)


class PairOut(Schema):
    device_id: UUID
    token: str
    user: UserOut


class DeviceOut(Schema):
    id: UUID
    name: str
    created_at: datetime
    last_seen_at: datetime | None
