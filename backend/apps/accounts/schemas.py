from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from ninja import Schema
from pydantic import Field

from apps.common.schemas import InputSchema
from apps.workspaces.schemas import WorkspaceOut


class UserOut(Schema):
    id: UUID
    email: str
    name: str
    # The chosen language of the interface and the server's messages: "nl", "en", or ""
    # when the account has not chosen (then the caller's Accept-Language decides).
    language: str
    explanation_language: str
    is_server_admin: bool = False


class LanguageIn(InputSchema):
    # "" removes the choice: the caller's Accept-Language decides again.
    language: Literal["", "nl", "en"]


class SetupStatusOut(Schema):
    needs_setup: bool


class SetupIn(InputSchema):
    # The installation code from the server log or BRIEVIE_SETUP_TOKEN.
    setup_token: str = Field(min_length=1, max_length=200)
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)
    name: str = Field(min_length=1, max_length=150)
    workspace_name: str = Field(min_length=1, max_length=150)
    workspace_kind: Literal["home", "business"]
    # Own labels next to the start set (for a business: its departments). Trimmed;
    # names that exist already are skipped.
    extra_labels: list[Annotated[str, Field(min_length=1, max_length=50)]] = Field(
        default=[], max_length=20
    )


class SetupOut(Schema):
    user: UserOut
    workspace: WorkspaceOut


class AcceptedInvitationOut(Schema):
    """After accepting an invitation: the signed-in account and the workspace it joined.
    (Here, not in workspaces.schemas, which this module imports.)"""

    user: UserOut
    workspace: WorkspaceOut


class LoginIn(InputSchema):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


class SessionOut(Schema):
    """A web session of the caller ("Waar je bent ingelogd")."""

    id: UUID
    method: str
    started_at: datetime
    last_used_at: datetime
    client_ip: str = ""
    # A short description such as "Firefox op Windows".
    browser: str
    # The session this request came with.
    current: bool


class ReauthenticateIn(InputSchema):
    password: str = Field(max_length=128)


class PasswordChangeIn(InputSchema):
    current_password: str = Field(max_length=128)
    new_password: str = Field(max_length=128)


class NoticeOut(Schema):
    id: UUID
    code: str
    # The notice as a Dutch sentence.
    message: str
    created_at: datetime
    # False for the "password was reset" notice in the session that the reset started.
    can_dismiss: bool = True


class NoticesSeenIn(InputSchema):
    notice_ids: list[UUID] = Field(max_length=50)


class PasswordResetOut(Schema):
    # The reset link, with its one-time token in the fragment. Shown once.
    link: str
    expires_at: datetime


class PasswordResetIn(InputSchema):
    # The fragment of the reset link.
    token: str = Field(max_length=100)
    password: str = Field(max_length=128)
