"""`prepare_server`: what a compose install does at every start, before the api runs (S 4.5).

Pure parts first (the host name check); the database steps follow in later functions.
"""

import re
from dataclasses import dataclass
from typing import Literal

PLACEHOLDER_HOSTNAME = "verander-mij.local"
_VALID = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}")
_LOOPBACK = re.compile(r"(localhost\.?|[A-Za-z0-9.-]*\.localhost\.?|127(\.[0-9]{1,3}){3})")


@dataclass(frozen=True)
class HostnameCheck:
    verdict: Literal["ok", "placeholder", "loopback", "invalid"]

    @property
    def refused(self) -> bool:
        return self.verdict in ("placeholder", "invalid")


def check_hostname(name: str) -> HostnameCheck:
    """The name or address the iPhone uses for this server.

    The example name and anything but letters, digits, dots and dashes are refused (as
    deploy/init-env.sh refuses them); a name that only reaches this computer is allowed
    with a warning, as ./brievie start does.
    """
    if name.strip().lower() == PLACEHOLDER_HOSTNAME:
        return HostnameCheck("placeholder")
    if _VALID.fullmatch(name) is None:
        return HostnameCheck("invalid")
    if _LOOPBACK.fullmatch(name) is not None:
        return HostnameCheck("loopback")
    return HostnameCheck("ok")
