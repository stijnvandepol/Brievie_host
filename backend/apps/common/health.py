"""The health check: is the server up, and can it reach its database?"""

import logging

from django.db import DEFAULT_DB_ALIAS, DatabaseError, connection
from django.urls import URLPattern, URLResolver

logger = logging.getLogger(__name__)

UrlConf = tuple[list[URLPattern | URLResolver], str, str]


def database_reachable() -> bool:
    """`SELECT 1` as the app role. A failure is logged by its type only: the message of a
    connection error names the database's host."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError as error:
        logger.warning("Health: database unreachable (%s)", type(error).__name__)
        return False
    return True


def mark_non_atomic(urls: UrlConf, route: str) -> UrlConf:
    """`urls` with the view of `route` taken out of ATOMIC_REQUESTS.

    The request transaction opens the database connection before the view runs, so an
    unreachable database would become Django's 500 before the health view could answer.
    """
    patterns = urls[0]
    for pattern in patterns:
        if isinstance(pattern, URLPattern) and str(pattern.pattern) == route:
            pattern.callback._non_atomic_requests = {DEFAULT_DB_ALIAS}  # type: ignore[attr-defined]
            return urls
    raise LookupError(route)
