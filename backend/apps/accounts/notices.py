"""Account notices: what somebody else did to your account, shown until you dismiss it."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db import transaction
from django.http import HttpRequest
from django.utils import formats, timezone
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import AccountNotice, User, UserSession
from apps.accounts.services import current_user

Code = AccountNotice.Code

# Dates in a notice are days in the Netherlands, whatever the server's clock zone.
NOTICE_ZONE = ZoneInfo("Europe/Amsterdam")

MESSAGES: dict[str, Promise] = {
    Code.PASSWORD_RESET_CREATED: _(
        "%(name)s heeft op %(date)s een herstellink voor je wachtwoord gemaakt."
    ),
    # Fixed by the design (D 6.3).
    Code.PASSWORD_RESET_COMPLETED: _(
        "Je wachtwoord is op %(date)s opnieuw ingesteld door %(name)s."
    ),
    Code.PASSWORD_RESET_BY_COMMAND: _(
        "Je wachtwoord is op %(date)s opnieuw ingesteld op de server."
    ),
    Code.SESSIONS_ENDED: _("%(name)s heeft op %(date)s je sessies beëindigd."),
    Code.DEVICE_REVOKED: _(
        "%(name)s heeft op %(date)s je apparaat \u2018%(device)s\u2019 ingetrokken."
    ),
    Code.SERVER_ADMIN_GRANTED: _("Je bent op %(date)s serverbeheerder gemaakt op de server."),
    Code.SERVER_ADMIN_REVOKED: _(
        "Je bent op %(date)s geen serverbeheerder meer. Dat is op de server geregeld."
    ),
    Code.SECOND_FACTOR_CLEARED: _("%(name)s heeft op %(date)s je tweestapsverificatie gewist."),
    Code.EMERGENCY_ACCESS_SWITCHED_ON: _(
        "%(name)s heeft op %(date)s noodtoegang voor de beheerder aangezet in %(workspace)s."
    ),
    Code.EMERGENCY_ACCESS_SWITCHED_OFF: _(
        "%(name)s heeft op %(date)s noodtoegang voor de beheerder uitgezet in %(workspace)s."
    ),
    Code.EMERGENCY_ACCESS_USED: _(
        "%(name)s heeft op %(date)s met noodtoegang %(count)s privé-brieven van %(person)s "
        "geopend. De reden staat bij elke brief."
    ),
    Code.PRIVATE_WITHOUT_READERS: _(
        "Er is post die privé had moeten worden, maar niemand zou hem kunnen lezen. Hij is "
        "zichtbaar gebleven voor iedereen in %(workspace)s (document %(document_id)s)."
    ),
}


class _Values(dict[str, str]):
    """A value a sentence names but the notice lacks reads as empty, never a crash."""

    def __missing__(self, key: str) -> str:
        return ""


@dataclass(frozen=True)
class NoticeView:
    id: UUID
    code: str
    message: str
    created_at: datetime
    can_dismiss: bool


def notify(
    user: User, code: str, *, actor_name: str, values: Mapping[str, str] | None = None
) -> None:
    """Leave `user` a notice that `actor_name` did `code` (values fill in the sentence)."""
    if code not in Code.values:
        raise ValueError(f"Unknown account notice code: {code!r}")
    with transaction.atomic():
        AccountNotice.objects.create(
            user=user, code=code, actor_name=actor_name, values=dict(values or {})
        )


def notice_message(notice: AccountNotice) -> str:
    """The notice as a Dutch sentence, with the day as "3 mei"."""
    day = formats.date_format(timezone.localtime(notice.created_at, NOTICE_ZONE), "j F")
    values = _Values({str(k): str(v) for k, v in notice.values.items()})
    values.update(name=notice.actor_name, date=day)
    return str(MESSAGES[notice.code]) % values


def can_dismiss(notice: AccountNotice, session: UserSession | None) -> bool:
    """Whoever completed a reset cannot hide that it happened (D 21): a session that the
    reset itself started cannot dismiss the reset notice."""
    return not (
        notice.code == Code.PASSWORD_RESET_COMPLETED
        and session is not None
        and session.created_via == UserSession.Via.RESET
    )


def _current_session(request: HttpRequest) -> UserSession | None:
    return UserSession.objects.filter(session_key=request.session.session_key).first()


def unseen_notices(request: HttpRequest) -> list[NoticeView]:
    """The caller's notices not dismissed yet, oldest first."""
    user = current_user(request)
    session = _current_session(request)
    notices = AccountNotice.objects.filter(user=user, seen_at=None).order_by("created_at", "id")
    return [
        NoticeView(
            id=notice.id,
            code=notice.code,
            message=notice_message(notice),
            created_at=notice.created_at,
            can_dismiss=can_dismiss(notice, session),
        )
        for notice in notices
    ]


def mark_seen(request: HttpRequest, notice_ids: Sequence[UUID]) -> None:
    """Dismiss the caller's notices among `notice_ids` that this session may dismiss.

    Another user's notice, an unknown id or one this session may not dismiss is left as
    it is, without an error.
    """
    user = current_user(request)
    session = _current_session(request)
    with transaction.atomic():
        notices = AccountNotice.objects.select_for_update().filter(
            user=user, id__in=notice_ids, seen_at=None
        )
        dismissable = [n.id for n in notices if can_dismiss(n, session)]
        AccountNotice.objects.filter(id__in=dismissable).update(seen_at=timezone.now())
