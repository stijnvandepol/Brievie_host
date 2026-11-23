"""`prepare_server`: what a compose install does at every start, before the api runs (S 4.5).

`run` judges the database as the superuser, asks the pure gate (`apps.updates.gate`), and
only then changes anything: freeze, emergency back-up, migrate, record. Everything it
touches outside the database it reads comes in through `Deps`, so the tests drive every
branch; `apps.installer.runtime` builds the real ones.
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from django.utils.translation import gettext as _

from apps.installer import freeze
from apps.installer.database import (
    Connect,
    Connection,
    DatabaseSetupError,
    Names,
    Passwords,
    ensure_roles_and_database,
)
from apps.updates.gate import decide_start
from apps.updates.release import Decision, Release, Version, refusal_message

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


# --- the database steps (S 4.5, steps 2 to 6) -------------------------------------


class MigrateError(Exception):
    """A migration failed. `applied_any`: at least one migration of this start was applied
    before it, so the schema is no longer the old version's."""

    def __init__(self, *, applied_any: bool) -> None:
        super().__init__("migrate failed")
        self.applied_any = applied_any


@dataclass(frozen=True)
class BackupResult:
    ok: bool
    run_id: UUID | None = None


@dataclass(frozen=True)
class Deps:
    """Everything `run` touches outside the database it judges; tests pass fakes."""

    connect: Connect
    names: Names
    passwords: Passwords
    image: Version
    release: Release
    compose_revision: int
    image_compose_revision: int
    adopt_version: Version | None
    hostname: str
    known_migrations: frozenset[tuple[str, str]]
    own_apps: frozenset[str]
    # Prepares an empty back-up folder when BRIEVIE_BACKUP_AUTO_INIT says so; the sentence
    # to show, or "".
    auto_init: Callable[[], str]
    migrate: Callable[[], None]
    backup: Callable[[str], BackupResult]
    earlier_backup: Callable[[str], UUID | None]
    record: Callable[[Version | None, Version, UUID | None], None]
    store_settings_copy: Callable[[], None]
    preflight: Callable[[], tuple[int, int]]
    say: Callable[[str], None]
    complain: Callable[[str], None]


@dataclass(frozen=True)
class State:
    """What the database says about itself, read as the superuser before anything changes."""

    major: int
    has_data: bool
    installed: Version | None
    unknown_migrations: bool


def unknown_migrations(
    applied: Iterable[tuple[str, str]],
    known: frozenset[tuple[str, str]],
    own_apps: frozenset[str],
) -> bool:
    """Whether the database has applied a migration of Brievie's own that this image lacks."""
    return any(app in own_apps and (app, name) not in known for app, name in applied)


def read_state(deps: Deps) -> State:
    with deps.connect(deps.names.database) as conn:
        row = conn.execute("SHOW server_version_num").fetchone()
        major = int(str(row[0])) // 10000 if row else 0
        have = conn.execute("SELECT to_regclass('public.django_migrations') IS NOT NULL").fetchone()
        applied: list[tuple[str, str]] = []
        if have and have[0]:
            applied = [
                (str(app), str(name))
                for app, name in conn.execute("SELECT app, name FROM django_migrations").fetchall()
            ]
        installed = _installed_version(conn)
    return State(
        major=major,
        has_data=bool(applied),
        installed=installed,
        unknown_migrations=unknown_migrations(applied, deps.known_migrations, deps.own_apps),
    )


def _installed_version(conn: Connection) -> Version | None:
    """The recorded version, else the newest finished update's target (as the ORM does)."""
    for table, column, order in (
        ("updates_installedversion", "version", "updated_at"),
        ("updates_serverupdate", "to_version", "finished_at"),
    ):
        present = conn.execute("SELECT to_regclass(%s) IS NOT NULL", [f"public.{table}"]).fetchone()
        if not (present and present[0]):
            continue
        row = conn.execute(
            f"SELECT {column} FROM {table} ORDER BY {order} DESC LIMIT 1"  # noqa: S608 - fixed names
        ).fetchone()
        if row:
            return Version.parse(str(row[0]))
    return None


def way_back(image: Version) -> str:
    return _(
        "De database blijft alleen-lezen. Zet de versie in het compose-bestand terug op de "
        "vorige versie en herstel de back-up pre-update-%(version)s "
        "(docker compose run --rm admin restore pre-update-%(version)s)."
    ) % {"version": image}


def run(deps: Deps) -> int:
    """The start of a compose install. 0: ready for the api; 1: refused or failed (the
    sentence says what; a refusal before the freeze changes nothing)."""
    hostname = check_hostname(deps.hostname)
    if hostname.verdict == "placeholder":
        deps.complain(
            _(
                "Zet BRIEVIE_HOSTNAME op de naam of het IP-adres waarmee je iPhone deze server "
                "bereikt, bijvoorbeeld mijnnas.local. Er is niets veranderd."
            )
        )
        return 1
    if hostname.verdict == "invalid":
        deps.complain(
            _(
                "BRIEVIE_HOSTNAME mag alleen letters, cijfers, punten en streepjes hebben. "
                "Er is niets veranderd."
            )
        )
        return 1
    if hostname.verdict == "loopback":
        deps.say(
            _(
                "Let op: met de naam %(name)s kan een iPhone deze server niet bereiken. Zet "
                "BRIEVIE_HOSTNAME op de naam of het IP-adres van deze server."
            )
            % {"name": deps.hostname}
        )

    try:
        ensure_roles_and_database(deps.connect, deps.passwords, deps.names)
        state = read_state(deps)
    except DatabaseSetupError:
        deps.complain(_("De database kon niet worden ingericht. Er is niets veranderd."))
        return 1

    free, needed = deps.preflight()
    decision = decide_start(
        image=deps.image,
        installed=state.installed,
        release=deps.release,
        database_major=state.major,
        unknown_migrations=state.unknown_migrations,
        compose_revision=deps.compose_revision,
        image_compose_revision=deps.image_compose_revision,
        adopt_version=deps.adopt_version,
        has_data=state.has_data,
        free_bytes=free,
        needed_bytes=needed,
    )
    if decision.outcome == "refused":
        deps.complain(
            refusal_message(
                Decision("refused", decision.code, go_first=decision.go_first),
                installed=state.installed or deps.image,
                target=deps.image,
                postgres_major=deps.release.postgres_major,
            )
        )
        return 1
    note = deps.auto_init()
    if note:
        deps.say(note)
    return _apply(deps, state, decision.outcome)


def _apply(deps: Deps, state: State, outcome: str) -> int:
    label = f"pre-update-{deps.image}"
    frozen = freeze.is_frozen(deps.connect, deps.names)
    backup_run: UUID | None = None
    if outcome == "update":
        if frozen:
            # An earlier start froze the database and went on to migrate: finish that, but
            # only when its back-up exists (a new one would back up a half-migrated schema).
            backup_run = deps.earlier_backup(label)
            if backup_run is None:
                deps.complain(
                    _(
                        "Een eerdere start van dit bijwerken is afgebroken en er is geen "
                        "back-up %(label)s. Herstel eerst een back-up. Er is niets veranderd."
                    )
                    % {"label": label}
                )
                return 1
            deps.say(_("Het bijwerken wordt afgemaakt; de back-up van de eerdere start is er."))
        else:
            freeze.freeze(deps.connect, deps.names)
            deps.say(
                _("Brievie wordt bijgewerkt: eerst een back-up (%(label)s).") % {"label": label}
            )
            result = deps.backup(label)
            if not result.ok:
                freeze.thaw(deps.connect, deps.names)
                deps.complain(_("De back-up vóór het bijwerken is mislukt. Er is niets veranderd."))
                return 1
            backup_run = result.run_id
    try:
        deps.migrate()
    except MigrateError as failure:
        if failure.applied_any:
            deps.complain(_("Het bijwerken is halverwege mislukt.") + " " + way_back(deps.image))
        else:
            freeze.thaw(deps.connect, deps.names)
            deps.complain(_("De database kon niet worden bijgewerkt. Er is niets veranderd."))
        return 1
    try:
        deps.record(state.installed, deps.image, backup_run)
    finally:
        freeze.thaw(deps.connect, deps.names)
    deps.store_settings_copy()
    deps.say(_("Brievie %(version)s is klaar om te starten.") % {"version": deps.image})
    return 0
