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
    language: str
    explanation_language: str
    is_server_admin: bool = False


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


class LoginIn(InputSchema):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)
