from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from ninja import Schema
from pydantic import Field

from apps.accounts.models import SecondFactor, User
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
    # Whether the account has two-step verification on (a confirmed authenticator app).
    has_second_factor: bool = False

    @staticmethod
    def resolve_has_second_factor(obj: User) -> bool:
        return SecondFactor.objects.filter(user_id=obj.id, confirmed_at__isnull=False).exists()


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


class SignInStepOut(Schema):
    """202: the password (or the invitation, the reset, the setup) was right, and a second
    step follows before the browser is signed in. `code`: send the code from the
    authenticator app or a recovery code to `verifySignInCode`. `setup`: two-step
    verification is required and must be set up first (`beginSecondFactorSetup`). The
    browser holds the pending step for `expires_in_seconds`; it is not signed in."""

    step: str
    expires_in_seconds: int = 300


class SignInCodeIn(InputSchema):
    """Exactly one of the two: the six digits from the authenticator app, or a recovery
    code (XXXXX-XXXXX; case, spaces and hyphens do not matter)."""

    code: str = Field(default="", max_length=20)
    recovery_code: str = Field(default="", max_length=30)


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
    # The sign-in passed the second step (a code, a recovery code) or came through the
    # organisation's sign-in ("met tweestapsverificatie").
    second_factor: bool = False


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
    # The workspace the notice is about (show it there only); null for one about the
    # account itself, shown everywhere.
    workspace_id: UUID | None = None


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


class SecondFactorOut(Schema):
    """The caller's two-step verification ("Beveiliging")."""

    enabled: bool = False
    required: bool = False
    # What asks it: none, server (the server's rule) or workspace.
    required_by: str = "none"
    # The names of the caller's workspaces that ask it.
    required_by_workspaces: list[str] = Field(default=[])
    # While a grace period runs: its end (ISO 8601). After it, signing in leads straight
    # to setting it up.
    grace_until: str | None = None
    recovery_codes_left: int = 0


class SetupSecretOut(Schema):
    """Shown once: the secret for an authenticator app, as text in groups of four and as
    the otpauth link a QR code carries."""

    secret: str
    otpauth_uri: str


class SecondFactorCodeIn(InputSchema):
    # The six digits the authenticator app shows.
    code: str = Field(max_length=20)


class RecoveryCodesOut(Schema):
    """Shown once: ten recovery codes, each usable once instead of a code."""

    recovery_codes: list[str]


class RemoveSecondFactorIn(InputSchema):
    password: str = Field(max_length=128)
    code: str = Field(max_length=20)
