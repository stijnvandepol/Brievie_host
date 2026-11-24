"""Account notices: what somebody else did to your account, or a device paired with it,
shown until you dismiss it."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Q
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
    Code.DEVICE_PAIRED: _(
        "Op %(date)s is het apparaat \u2018%(device)s\u2019 aan je account gekoppeld. Was jij "
        "dat niet? Trek het in bij Apparaten en wijzig je wachtwoord."
    ),
    Code.SERVER_ADMIN_GRANTED: _("Je bent op %(date)s serverbeheerder gemaakt op de server."),
    Code.SERVER_ADMIN_REVOKED: _(
        "Je bent op %(date)s geen serverbeheerder meer. Dat is op de server geregeld."
    ),
    Code.SECOND_FACTOR_CLEARED: _("%(name)s heeft op %(date)s je tweestapsverificatie gewist."),
    Code.SECOND_FACTOR_CLEARED_BY_COMMAND: _(
        "Je tweestapsverificatie is op %(date)s gewist op de server."
    ),
    # Fixed by the design (two-step sign-in, S 4 and S 5).
    Code.SECOND_FACTOR_LOCKED: _(
        "Iemand heeft op %(date)s met jouw wachtwoord geprobeerd in te loggen, maar had de "
        "code niet. Verander je wachtwoord als jij dat niet was."
    ),
    Code.SECOND_FACTOR_REQUIRED: _(
        "Zet tweestapsverificatie aan vóór %(until)s. Daarna kun je pas inloggen als je het "
        "hebt aangezet."
    ),
    Code.SECOND_FACTOR_RECOVERY_USED: _(
        "Op %(date)s is met een herstelcode op je account ingelogd. Je hebt nog %(count)s "
        "herstelcodes."
    ),
    Code.EMERGENCY_ACCESS_SWITCHED_ON: _(
        "%(name)s heeft op %(date)s noodtoegang voor de beheerder aangezet in %(workspace)s."
    ),
    Code.EMERGENCY_ACCESS_SWITCHED_OFF: _(
        "%(name)s heeft op %(date)s noodtoegang voor de beheerder uitgezet in %(workspace)s."
    ),
    Code.EMERGENCY_ACCESS_USED: _(
        "%(name)s heeft op %(date)s met noodtoegang %(count)s brieven van %(person)s "
        "geopend. De reden staat bij elke brief."
    ),
    Code.PRIVATE_WITHOUT_READERS: _(
        "Er is post die privé had moeten worden, maar niemand zou hem kunnen lezen. Hij is "
        "zichtbaar gebleven voor iedereen in %(workspace)s (document %(document_id)s)."
    ),
    Code.PRIVATE_WITHOUT_READERS_UNNAMED: _(
        "Er is post in %(workspace)s die privé had moeten worden, maar niemand zou hem "
        "kunnen lezen. Hij is zichtbaar gebleven voor wie hem mag zien."
    ),
    # The sentence of the category `all`; `CATEGORY_MESSAGES` has the others.
    Code.SIGHT_WIDENED: _(
        "Je kunt nu alle post zien die niet privé is. Dat heeft %(name)s op %(date)s ingesteld."
    ),
    Code.SIGHT_NARROWED: _("Je ziet de post van anderen niet meer."),
    Code.ADMINS_READ_ALL_ON: _(
        "De beheerders kunnen nu alle post lezen die niet privé is, ook de jouwe. Dat heeft "
        "%(name)s op %(date)s ingesteld."
    ),
    Code.SELF_GRANT: _("%(name)s heeft zichzelf toegang gegeven tot post zonder naam."),
}

# Notices about sight say which kind of post (`category`, visibility_rules): one sentence
# per kind. A kind not listed here falls back to `MESSAGES`.
CATEGORY_MESSAGES: dict[str, dict[str, Promise]] = {
    Code.SIGHT_WIDENED: {
        "all": MESSAGES[Code.SIGHT_WIDENED],
        "all_as_admin": _(
            "Je kunt als beheerder nu alle post zien die niet privé is. Dat heeft %(name)s op "
            "%(date)s ingesteld."
        ),
        "department": _(
            "Je ziet nu ook de post van %(department)s. Dat heeft %(name)s op %(date)s ingesteld."
        ),
        "person": _(
            "Je ziet nu ook de post van %(person)s. Dat heeft %(name)s op %(date)s ingesteld."
        ),
        "unassigned": _(
            "Je ziet nu post zonder naam, om er een naam op te zetten. Dat heeft %(name)s op "
            "%(date)s ingesteld."
        ),
    },
    Code.SIGHT_NARROWED: {
        "all": MESSAGES[Code.SIGHT_NARROWED],
        "all_as_admin": _("Je leest als beheerder niet meer alle post."),
        "department": _("Je ziet de post van %(department)s niet meer."),
        "person": _("Je ziet de post van %(person)s niet meer."),
        "unassigned": _("Je ziet post zonder naam niet meer."),
    },
    Code.SELF_GRANT: {
        "unassigned": MESSAGES[Code.SELF_GRANT],
        "department": _("%(name)s heeft zichzelf toegang gegeven tot de post van %(department)s."),
        "person": _("%(name)s heeft zichzelf toegang gegeven tot de post van %(person)s."),
    },
}


# The values each notice may carry (S 4.14). A notice outlives the member's sight of a
# letter, so none holds letter content: a letter is named by its id, a person, department,
# workspace or device by its name (a directory, not post). `notify` refuses any other key.
NOTICE_VALUE_KEYS: Final[dict[str, frozenset[str]]] = {
    Code.PASSWORD_RESET_CREATED: frozenset(),
    Code.PASSWORD_RESET_COMPLETED: frozenset(),
    Code.PASSWORD_RESET_BY_COMMAND: frozenset(),
    Code.SESSIONS_ENDED: frozenset(),
    Code.DEVICE_REVOKED: frozenset({"device"}),
    Code.DEVICE_PAIRED: frozenset({"device"}),
    Code.SERVER_ADMIN_GRANTED: frozenset(),
    Code.SERVER_ADMIN_REVOKED: frozenset(),
    Code.SECOND_FACTOR_CLEARED: frozenset(),
    Code.SECOND_FACTOR_CLEARED_BY_COMMAND: frozenset(),
    Code.SECOND_FACTOR_LOCKED: frozenset(),
    # until: the last day of the grace period, as "4 december".
    Code.SECOND_FACTOR_REQUIRED: frozenset({"until"}),
    # count: the recovery codes left; never a code.
    Code.SECOND_FACTOR_RECOVERY_USED: frozenset({"count"}),
    Code.EMERGENCY_ACCESS_SWITCHED_ON: frozenset({"workspace"}),
    Code.EMERGENCY_ACCESS_SWITCHED_OFF: frozenset({"workspace"}),
    Code.EMERGENCY_ACCESS_USED: frozenset({"count", "person"}),
    Code.PRIVATE_WITHOUT_READERS: frozenset({"workspace", "document_id"}),
    Code.PRIVATE_WITHOUT_READERS_UNNAMED: frozenset({"workspace"}),
    Code.SIGHT_WIDENED: frozenset({"category", "department", "person"}),
    Code.SIGHT_NARROWED: frozenset({"category", "department", "person"}),
    Code.ADMINS_READ_ALL_ON: frozenset(),
    Code.SELF_GRANT: frozenset({"category", "department", "person"}),
}


# The notices about one workspace (navigation spec S 8, B7): made with that workspace and
# shown only there. The others are about the account and shown everywhere.
WORKSPACE_CODES: Final = frozenset(
    {
        Code.EMERGENCY_ACCESS_SWITCHED_ON,
        Code.EMERGENCY_ACCESS_SWITCHED_OFF,
        Code.EMERGENCY_ACCESS_USED,
        Code.PRIVATE_WITHOUT_READERS,
        Code.PRIVATE_WITHOUT_READERS_UNNAMED,
        Code.SIGHT_WIDENED,
        Code.SIGHT_NARROWED,
        Code.ADMINS_READ_ALL_ON,
        Code.SELF_GRANT,
    }
)


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
    workspace_id: UUID | None


def notify(
    user: User,
    code: str,
    *,
    actor_name: str,
    values: Mapping[str, str] | None = None,
    workspace_id: UUID | None = None,
) -> None:
    """Leave `user` a notice that `actor_name` did `code` (values fill in the sentence).

    Only the value keys `NOTICE_VALUE_KEYS` allows for the code: never letter content. A
    code about a workspace (`WORKSPACE_CODES`) needs that workspace and an account code
    takes none, so no notice shows in the wrong place."""
    if code not in Code.values:
        raise ValueError(f"Unknown account notice code: {code!r}")
    unknown = sorted(set(values or {}) - NOTICE_VALUE_KEYS[code])
    if unknown:
        raise ValueError(f"Account notice {code!r} takes no value {', '.join(unknown)}")
    if (code in WORKSPACE_CODES) != (workspace_id is not None):
        raise ValueError(f"Account notice {code!r}: workspace {workspace_id!r} does not fit")
    with transaction.atomic():
        AccountNotice.objects.create(
            user=user,
            code=code,
            actor_name=actor_name,
            values=dict(values or {}),
            workspace_id=workspace_id,
        )


def notice_message(notice: AccountNotice) -> str:
    """The notice as a Dutch sentence, with the day as "3 mei"."""
    day = formats.date_format(timezone.localtime(notice.created_at, NOTICE_ZONE), "j F")
    values = _Values({str(k): str(v) for k, v in notice.values.items()})
    values.update(name=notice.actor_name, date=day)
    message = CATEGORY_MESSAGES.get(notice.code, {}).get(values["category"], MESSAGES[notice.code])
    return str(message) % values


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
    """The caller's notices not dismissed yet, oldest first: about the account, or about a
    workspace they are still a member of (one they left may name its people, security
    review of PR #44, point 4)."""
    user = current_user(request)
    session = _current_session(request)
    notices = (
        AccountNotice.objects.filter(user=user, seen_at=None)
        .filter(Q(workspace__isnull=True) | Q(workspace__memberships__user=user))
        .distinct()
        .order_by("created_at", "id")
    )
    return [
        NoticeView(
            id=notice.id,
            code=notice.code,
            message=notice_message(notice),
            created_at=notice.created_at,
            can_dismiss=can_dismiss(notice, session),
            workspace_id=notice.workspace_id,
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
