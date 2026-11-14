from typing import Literal
from uuid import UUID

from ninja import Schema
from pydantic import Field

from apps.workspaces.schemas import WorkspaceOut


class UserOut(Schema):
    id: UUID
    email: str
    name: str
    language: str
    explanation_language: str


class SetupStatusOut(Schema):
    needs_setup: bool


class SetupIn(Schema):
    # The installation code from the server log or BRIEVIE_SETUP_TOKEN.
    setup_token: str = Field(min_length=1, max_length=200)
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)
    name: str = Field(min_length=1, max_length=150)
    workspace_name: str = Field(min_length=1, max_length=150)
    workspace_kind: Literal["home", "business"]


class SetupOut(Schema):
    user: UserOut
    workspace: WorkspaceOut


class LoginIn(Schema):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)
