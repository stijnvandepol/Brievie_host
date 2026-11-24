"""Background job of the accounts app: removing web sessions nobody comes back to, and
keeping two-step verification's rules (unfinished set-ups, grace periods)."""

import logging
from datetime import datetime

from django.conf import settings
from django.contrib.sessions.models import Session
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from procrastinate.contrib.django import app

from apps.accounts.models import User, UserSession
from apps.accounts.second_factor import purge_unconfirmed
from apps.accounts.second_factor_policy import (
    has_factor,
    in_grace,
    note_required_since,
    requirement_for,
)
from apps.accounts.sessions import end_sessions_without_second_step, session_policy
from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, record
from apps.pipeline.jobs import QUEUE

logger = logging.getLogger(__name__)


@app.periodic(cron="23 * * * *")
# Every worker defers every periodic task; the queueing lock keeps at most one waiting.
@app.task(name="remove_stale_sessions", queue=QUEUE, queueing_lock="remove_stale_sessions")
def remove_stale_sessions(timestamp: int) -> None:
    """Delete session records whose Django session is gone or expired, and sessions past
    their idle or absolute limit together with Django's row (D 7.2), so a browser that
    never returns is not listed forever. Expired Django sessions go too (what Django's
    `clearsessions` would do; nothing else runs it)."""
    now = timezone.now()
    with transaction.atomic():
        Session.objects.filter(expire_date__lte=now).delete()
        live = Session.objects.values("session_key")
        orphans, _ = UserSession.objects.exclude(session_key__in=live).delete()
    past_limits = 0
    for method in UserSession.Method.values:
        policy = session_policy(method)
        with transaction.atomic():
            stale = UserSession.objects.filter(method=method).filter(
                Q(created_at__lt=now - policy.maximum) | Q(last_used_at__lt=now - policy.idle)
            )
            keys = list(stale.values_list("session_key", flat=True))
            Session.objects.filter(session_key__in=keys).delete()
            deleted, _ = UserSession.objects.filter(session_key__in=keys).delete()
        past_limits += deleted
    logger.info("Removed %d stale web sessions", orphans + past_limits)
    keep_second_factor_rules(now)


def keep_second_factor_rules(now: datetime) -> None:
    """Two-step verification (S 5), each step in its own short transaction: unfinished
    set-ups older than an hour go; a rule set in `.env` starts the grace period of every
    account it asks, without waiting for a sign-in (and one taken away forgets it); once a
    grace period is over, the sessions that did not pass the second step end. No notice:
    the next sign-in explains."""
    purged = purge_unconfirmed(now=now)
    if settings.BRIEVIE_REQUIRE_SECOND_FACTOR == "none":
        accounts = User.objects.filter(second_factor_required_since__isnull=False)
    else:
        accounts = User.objects.filter(is_active=True)
    ended = 0
    for user in accounts.order_by("id").iterator():
        note_required_since(user, now=now)
        if user.second_factor_required_since is None or has_factor(user):
            continue
        requirement = requirement_for(user, now=now)
        if not requirement.required or in_grace(requirement, now=now):
            continue
        count = end_sessions_without_second_step(user)
        if count:
            with transaction.atomic():
                record(
                    AuditCode.SESSION_ENDED,
                    actor=AuditActor.system(),
                    target=AuditTarget(kind=TargetKind.USER, id=user.id, name=user.name),
                    values={"count": count, "reason": "second_factor_required"},
                )
        ended += count
    logger.info(
        "Removed %d unfinished second-factor set-ups; ended %d sessions after a grace period",
        purged,
        ended,
    )
