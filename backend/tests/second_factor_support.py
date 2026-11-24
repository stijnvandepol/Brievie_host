"""Helpers for tests of two-step sign-in: an account with a factor, and its codes."""

import base64
from dataclasses import dataclass
from datetime import datetime

from django.test import Client
from django.utils import timezone

from apps.accounts import totp
from apps.accounts.models import User
from apps.accounts.second_factor import begin_setup, confirm_setup
from tests.factories import TEST_PASSWORD
from tests.helpers import post_json


@dataclass(frozen=True)
class Factor:
    secret: bytes
    recovery_codes: list[str]

    def code(self, now: datetime | None = None, *, offset: int = 0) -> str:
        """The app's code at `now` (default: the real clock), `offset` steps away."""
        return totp.code_at(self.secret, totp.step_of(now or timezone.now()) + offset)


def secret_of(grouped: str) -> bytes:
    text = grouped.replace(" ", "")
    return base64.b32decode(text + "=" * (-len(text) % 8))


def give_factor(user: User, *, now: datetime | None = None) -> Factor:
    """Set up and confirm a second factor for `user`, as the set-up pages would. The code
    used to confirm is the one of the step before `now`: a code of `now` stays usable."""
    moment = now or timezone.now()
    setup = begin_setup(user, host="brievie.local")
    secret = secret_of(setup.secret_text)
    earlier = totp.code_at(secret, totp.step_of(moment) - 1)
    confirmed = confirm_setup(user, earlier, now=moment, client_ip=None)
    return Factor(secret=secret, recovery_codes=confirmed.recovery_codes)


def password_step(client: Client, user: User) -> int:
    """POST the password; answers the status (202 when a second step follows)."""
    r = post_json(client, "/auth/login", {"email": user.email, "password": TEST_PASSWORD})
    return r.status_code


def signed_in_with_code(user: User, factor: Factor, *, offset: int = 0) -> Client:
    """A browser that signed in with the password and a code (a session that passed the
    second step)."""
    client = Client()
    assert password_step(client, user) == 202
    r = post_json(client, "/auth/login/code", {"code": factor.code(offset=offset)})
    assert r.status_code == 200, r.json()
    return client
