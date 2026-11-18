"""URLs mounted only by tests/test_accounts_sessions.py: the API plus one plain view that
ends the caller's own sessions inside the request, as Task 5's endpoints will."""

from django.contrib.sessions.models import Session
from django.http import HttpRequest, HttpResponse
from django.urls import path

from apps.accounts.models import User
from apps.accounts.sessions import end_sessions
from brievie.urls import urlpatterns as brievie_urlpatterns


def end_all_my_sessions(request: HttpRequest) -> HttpResponse:
    user = request.user
    if not isinstance(user, User):
        return HttpResponse(status=401)
    end_sessions(user, current=request)
    return HttpResponse(status=204)


def interrupt(request: HttpRequest) -> HttpResponse:
    """Another request ends this session while this one changes it."""
    Session.objects.filter(session_key=request.session.session_key).delete()
    request.session["changed"] = True
    return HttpResponse(status=200)


urlpatterns = [
    path("test/end-all-my-sessions", end_all_my_sessions),
    path("api/v1/test/interrupt", interrupt),
    path("test/interrupt", interrupt),
    *brievie_urlpatterns,
]
