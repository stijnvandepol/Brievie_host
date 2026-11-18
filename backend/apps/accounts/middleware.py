import logging
from collections.abc import Callable
from datetime import timedelta

from django.contrib.auth import SESSION_KEY
from django.contrib.auth.models import AnonymousUser
from django.db import DatabaseError, transaction
from django.http import HttpRequest, HttpResponseBase
from django.utils import timezone

from apps.accounts.models import UserSession
from apps.accounts.sessions import is_past_its_limits, touch_session

logger = logging.getLogger(__name__)

# "Last used" is written at most this often per session.
LAST_USED_INTERVAL = timedelta(minutes=1)


class UserSessionMiddleware:
    """Signs out a web session that has no `UserSession` or is past its limits (D 7.2).

    After `AuthenticationMiddleware`. Fails closed: a session holding a user but no
    record is flushed. Runs outside the request transaction, so its writes have their
    own; a failing "last used" write is logged and the request goes on.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        self.check(request)
        return self.get_response(request)

    def check(self, request: HttpRequest) -> None:
        session = request.session
        user_id = session.get(SESSION_KEY)
        if user_id is None:
            return
        key = session.session_key
        row = UserSession.objects.filter(session_key=key).first() if key else None
        now = timezone.now()
        if row is None or str(row.user_id) != str(user_id):
            self.sign_out(request)
            return
        if is_past_its_limits(row, now):
            with transaction.atomic():
                UserSession.objects.filter(pk=row.pk).delete()
            self.sign_out(request)
            return
        if now - row.last_used_at >= LAST_USED_INTERVAL:
            try:
                touch_session(row, request, now=now)
            except DatabaseError:
                logger.warning("Could not note the last use of a web session", exc_info=True)

    @staticmethod
    def sign_out(request: HttpRequest) -> None:
        request.session.flush()
        request.user = AnonymousUser()
