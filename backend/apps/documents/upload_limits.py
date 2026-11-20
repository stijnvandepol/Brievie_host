"""How many uploads a member and a workspace may make (security review 2026-11-20).

Every upload stores up to 32 MB and queues a processing job on the one worker all
workspaces share, with a paid AI call. Without a limit one member (or a stolen phone)
could fill the queue ahead of every other workspace's post and run up the AI bill.

Checked after the membership check, so somebody outside the workspace cannot use up its
count. A fresh throttle per check: Ninja's throttles keep state on the instance, which
gunicorn's threads would share.
"""

from uuid import UUID

from django.conf import settings
from django.http import HttpRequest
from django.utils.translation import gettext as _
from ninja.throttling import SimpleRateThrottle

from apps.common.errors import ApiError


class _CountedThrottle(SimpleRateThrottle):
    def __init__(self, scope: str, rate: str, ident: str) -> None:
        self.scope = scope
        self._ident = ident
        super().__init__(rate)

    def get_cache_key(self, request: HttpRequest) -> str:
        return self.cache_format % {"scope": self.scope, "ident": self._ident}


def check_upload_limits(request: HttpRequest, *, workspace_id: UUID, user_id: UUID) -> None:
    """429 `too_many_uploads` when this member, or the whole workspace, uploaded too much
    in the window; `Retry-After` says when the next one fits."""
    checks = (
        _CountedThrottle(
            "document_upload_member",
            settings.BRIEVIE_UPLOADS_PER_MEMBER,
            f"{workspace_id}:{user_id}",
        ),
        _CountedThrottle(
            "document_upload_workspace", settings.BRIEVIE_UPLOADS_PER_WORKSPACE, str(workspace_id)
        ),
    )
    for throttle in checks:
        if not throttle.allow_request(request):
            wait = throttle.wait()
            raise ApiError(
                429,
                "too_many_uploads",
                _(
                    "Er zijn net heel veel brieven verstuurd. "
                    "Wacht even, dan gaat het vanzelf verder."
                ),
                headers={"Retry-After": str(max(1, int(wait or 1)))},
            )
