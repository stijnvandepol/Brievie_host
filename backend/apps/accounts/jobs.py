"""Background job of the accounts app: removing web sessions nobody comes back to."""

import logging

from django.contrib.sessions.models import Session
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from procrastinate.contrib.django import app

from apps.accounts.models import UserSession
from apps.accounts.sessions import session_policy
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
