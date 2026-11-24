"""`prepare.run` against a scratch database: the gate, the freeze, the order of the steps.

The pieces outside the database (migrate, back-up, record) are fakes that log their calls,
so every branch is driven; the freeze is the real one on a real PostgreSQL.
"""

import os
from dataclasses import dataclass, field, replace
from uuid import UUID, uuid4

import psycopg
import pytest

from apps.installer import freeze, prepare
from apps.installer.database import Connect, Names, ensure_roles_and_database
from apps.installer.prepare import BackupResult, Deps, MigrateError, unknown_migrations
from apps.updates.release import Release, Version
from tests.installer_support import PASSWORDS, connect_as

IMAGE = Version.parse("0.5.0")
RELEASE = Release(IMAGE, Version.parse("0.3.0"), 18)
RUN_ID = uuid4()


def v(text: str) -> Version:
    return Version.parse(text)


@dataclass
class Fakes:
    """What `run` did, in order, and what the fakes answer."""

    calls: list[str] = field(default_factory=list)
    said: list[str] = field(default_factory=list)
    complained: list[str] = field(default_factory=list)
    frozen_during: dict[str, bool] = field(default_factory=dict)
    backup_ok: bool = True
    migrate_error: MigrateError | None = None
    earlier: UUID | None = None
    recorded: list[tuple[Version | None, Version, UUID | None]] = field(default_factory=list)
    connect: Connect | None = None
    names: Names | None = None

    def is_frozen(self) -> bool:
        assert self.connect is not None
        assert self.names is not None
        return freeze.is_frozen(self.connect, self.names)

    auto_init_note: str = ""

    def auto_init(self) -> str:
        self.calls.append("auto_init")
        return self.auto_init_note

    def migrate(self) -> None:
        self.calls.append("migrate")
        self.frozen_during["migrate"] = self.is_frozen()
        if self.migrate_error is not None:
            raise self.migrate_error

    def backup(self, label: str) -> BackupResult:
        self.calls.append(f"backup:{label}")
        self.frozen_during["backup"] = self.is_frozen()
        return BackupResult(ok=self.backup_ok, run_id=RUN_ID if self.backup_ok else None)

    def earlier_backup(self, label: str) -> UUID | None:
        self.calls.append(f"earlier:{label}")
        return self.earlier

    def record(self, installed: Version | None, image: Version, run_id: UUID | None) -> None:
        self.calls.append("record")
        self.frozen_during["record"] = self.is_frozen()
        self.recorded.append((installed, image, run_id))


def make_deps(
    connect: Connect,
    names: Names,
    fakes: Fakes,
    *,
    hostname: str = "mijnnas.local",
    compose_revision: int = 1,
    adopt: Version | None = None,
    free: int = 10**12,
    needed: int = 1,
    known: frozenset[tuple[str, str]] = frozenset({("documents", "0001_initial")}),
) -> Deps:
    fakes.connect, fakes.names = connect, names
    return Deps(
        connect=connect,
        names=names,
        passwords=PASSWORDS,
        image=IMAGE,
        release=RELEASE,
        compose_revision=compose_revision,
        image_compose_revision=1,
        adopt_version=adopt,
        hostname=hostname,
        known_migrations=known,
        own_apps=frozenset({"documents", "updates"}),
        auto_init=fakes.auto_init,
        migrate=fakes.migrate,
        backup=fakes.backup,
        earlier_backup=fakes.earlier_backup,
        record=fakes.record,
        store_settings_copy=lambda: fakes.calls.append("copy"),
        preflight=lambda: (free, needed),
        say=fakes.said.append,
        complain=fakes.complained.append,
    )


def seed(connect: Connect, names: Names, *, installed: str | None, applied: bool = True) -> None:
    """A database as an earlier version left it: migrations, and a recorded version."""
    with connect(names.database) as conn:
        if applied:
            conn.execute("CREATE TABLE django_migrations (app text, name text)")
            conn.execute("INSERT INTO django_migrations VALUES ('documents', '0001_initial')")
        if installed is not None:
            conn.execute(
                "CREATE TABLE updates_installedversion (version text, updated_at timestamptz)"
            )
            conn.execute("INSERT INTO updates_installedversion VALUES (%s, now())", [installed])
        for role in (names.app, names.owner):
            conn.execute(f'GRANT ALL ON ALL TABLES IN SCHEMA public TO "{role}"')


def database_exists(connect: Connect, names: Names) -> bool:
    with connect("postgres") as server:
        return (
            server.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", [names.database]
            ).fetchone()
            is not None
        )


def prepared(connect: Connect, names: Names) -> None:
    ensure_roles_and_database(connect, PASSWORDS, names)


# --- the pure part ---


def test_unknown_migrations_only_counts_own_apps() -> None:
    known = frozenset({("documents", "0001_initial")})
    own = frozenset({"documents"})
    assert not unknown_migrations([("documents", "0001_initial")], known, own)
    assert unknown_migrations([("documents", "0002_newer")], known, own)
    assert not unknown_migrations([("axes", "0099_third_party")], known, own)
    assert not unknown_migrations([], known, own)


# --- the host name refuses before the database is touched ---


@pytest.mark.parametrize("hostname", ["verander-mij.local", "my nas"])
def test_a_bad_host_name_refuses_before_anything_is_made(
    connect: Connect, scratch: Names, hostname: str
) -> None:
    fakes = Fakes()
    code = prepare.run(make_deps(connect, scratch, fakes, hostname=hostname))
    assert code == 1
    assert "Er is niets veranderd" in fakes.complained[0]
    assert not database_exists(connect, scratch)
    assert fakes.calls == []


def test_a_loopback_name_warns_and_goes_on(connect: Connect, scratch: Names) -> None:
    fakes = Fakes()
    assert prepare.run(make_deps(connect, scratch, fakes, hostname="localhost")) == 0
    assert any("localhost" in line for line in fakes.said)


# --- the outcomes ---


def test_a_fresh_server_migrates_and_records_without_a_back_up(
    connect: Connect, scratch: Names
) -> None:
    fakes = Fakes()
    assert prepare.run(make_deps(connect, scratch, fakes)) == 0
    assert fakes.calls == ["auto_init", "migrate", "record", "copy"]
    assert fakes.recorded == [(None, IMAGE, None)]
    assert not fakes.is_frozen()


def test_the_same_version_migrates_without_a_back_up_or_a_freeze(
    connect: Connect, scratch: Names
) -> None:
    prepared(connect, scratch)
    seed(connect, scratch, installed="0.5.0")
    fakes = Fakes()
    assert prepare.run(make_deps(connect, scratch, fakes)) == 0
    assert fakes.calls == ["auto_init", "migrate", "record", "copy"]
    assert fakes.frozen_during == {"migrate": False, "record": False}


def test_an_update_freezes_backs_up_migrates_records_and_thaws(
    connect: Connect, scratch: Names
) -> None:
    prepared(connect, scratch)
    seed(connect, scratch, installed="0.4.0")
    fakes = Fakes()
    assert prepare.run(make_deps(connect, scratch, fakes)) == 0
    assert fakes.calls == ["auto_init", "backup:pre-update-0.5.0", "migrate", "record", "copy"]
    assert fakes.frozen_during == {"backup": True, "migrate": True, "record": True}
    assert fakes.recorded == [(v("0.4.0"), IMAGE, RUN_ID)]
    assert not fakes.is_frozen()


def test_data_without_a_recorded_version_needs_the_adopt_version(
    connect: Connect, scratch: Names
) -> None:
    prepared(connect, scratch)
    seed(connect, scratch, installed=None)
    refused = Fakes()
    assert prepare.run(make_deps(connect, scratch, refused)) == 1
    assert "BRIEVIE_ADOPT_VERSION=0.5.0" in refused.complained[0]
    assert refused.calls == []
    adopted = Fakes()
    assert prepare.run(make_deps(connect, scratch, adopted, adopt=IMAGE)) == 0
    assert adopted.calls == ["auto_init", "migrate", "record", "copy"]
    assert adopted.recorded == [(None, IMAGE, None)]


# --- refusals change nothing ---


@pytest.mark.parametrize(
    ("installed", "options", "words"),
    [
        ("0.6.0", {}, "oudere versie"),
        ("0.2.0", {}, "niet in één keer"),
        ("0.4.0", {"compose_revision": 2}, "nieuwer dan versie"),
        ("0.4.0", {"free": 0, "needed": 10}, "te weinig vrije ruimte"),
    ],
)
def test_a_refusal_changes_nothing(
    connect: Connect, scratch: Names, installed: str, options: dict[str, int], words: str
) -> None:
    prepared(connect, scratch)
    seed(connect, scratch, installed=installed)
    fakes = Fakes()
    assert prepare.run(make_deps(connect, scratch, fakes, **options)) == 1  # type: ignore[arg-type]
    assert words in fakes.complained[0]
    assert fakes.complained[0].endswith("Er is niets veranderd.")
    assert fakes.calls == []
    assert not fakes.is_frozen()


def test_migrations_the_image_does_not_know_refuse_even_at_the_same_version(
    connect: Connect, scratch: Names
) -> None:
    prepared(connect, scratch)
    seed(connect, scratch, installed="0.5.0")
    with connect(scratch.database) as conn:
        conn.execute("INSERT INTO django_migrations VALUES ('documents', '0099_from_the_future')")
    fakes = Fakes()
    assert prepare.run(make_deps(connect, scratch, fakes)) == 1
    assert "nieuwere gegevens" in fakes.complained[0]
    assert fakes.calls == []


def test_another_postgres_major_refuses(connect: Connect, scratch: Names) -> None:
    prepared(connect, scratch)
    seed(connect, scratch, installed="0.4.0")
    fakes = Fakes()
    deps = make_deps(connect, scratch, fakes)
    other = replace(deps, release=Release(IMAGE, Version.parse("0.3.0"), 17))
    assert prepare.run(other) == 1
    assert "PostgreSQL 17" in fakes.complained[0]
    assert fakes.calls == []


def test_a_refusal_prints_no_secret_and_no_connection_string(
    connect: Connect, scratch: Names
) -> None:
    prepared(connect, scratch)
    seed(connect, scratch, installed="0.6.0")
    fakes = Fakes()
    prepare.run(make_deps(connect, scratch, fakes))
    text = " ".join(fakes.said + fakes.complained)
    for secret in (PASSWORDS.owner, PASSWORDS.app, PASSWORDS.backup):
        assert secret not in text
    assert "postgres://" not in text


# --- failures after the freeze ---


def test_a_failed_back_up_thaws_and_stops_before_migrating(
    connect: Connect, scratch: Names
) -> None:
    prepared(connect, scratch)
    seed(connect, scratch, installed="0.4.0")
    fakes = Fakes(backup_ok=False)
    assert prepare.run(make_deps(connect, scratch, fakes)) == 1
    assert "De back-up vóór het bijwerken is mislukt" in fakes.complained[0]
    assert "Er is niets veranderd" in fakes.complained[0]
    assert fakes.calls == ["auto_init", "backup:pre-update-0.5.0"]
    assert not fakes.is_frozen()


def test_a_migration_failing_before_any_applied_thaws(connect: Connect, scratch: Names) -> None:
    prepared(connect, scratch)
    seed(connect, scratch, installed="0.4.0")
    fakes = Fakes(migrate_error=MigrateError(applied_any=False))
    assert prepare.run(make_deps(connect, scratch, fakes)) == 1
    assert "Er is niets veranderd" in fakes.complained[0]
    assert "record" not in fakes.calls
    assert not fakes.is_frozen()


def test_a_migration_failing_after_one_applied_stays_frozen_with_the_way_back(
    connect: Connect, scratch: Names
) -> None:
    prepared(connect, scratch)
    seed(connect, scratch, installed="0.4.0")
    fakes = Fakes(migrate_error=MigrateError(applied_any=True))
    assert prepare.run(make_deps(connect, scratch, fakes)) == 1
    assert "pre-update-0.5.0" in fakes.complained[0]
    assert "alleen-lezen" in fakes.complained[0]
    assert "restore_install pre-update-0.5.0 --replace" in fakes.complained[0]
    assert fakes.is_frozen()
    assert "record" not in fakes.calls


def test_a_second_start_resumes_when_the_first_back_up_exists(
    connect: Connect, scratch: Names
) -> None:
    prepared(connect, scratch)
    seed(connect, scratch, installed="0.4.0")
    first = Fakes(migrate_error=MigrateError(applied_any=True))
    prepare.run(make_deps(connect, scratch, first))
    second = Fakes(earlier=RUN_ID)
    assert prepare.run(make_deps(connect, scratch, second)) == 0
    assert second.calls == ["auto_init", "earlier:pre-update-0.5.0", "migrate", "record", "copy"]
    assert second.recorded == [(v("0.4.0"), IMAGE, RUN_ID)]
    assert not second.is_frozen()


def test_a_second_start_without_the_first_back_up_refuses_and_stays_frozen(
    connect: Connect, scratch: Names
) -> None:
    prepared(connect, scratch)
    seed(connect, scratch, installed="0.4.0")
    prepare.run(make_deps(connect, scratch, Fakes(migrate_error=MigrateError(applied_any=True))))
    second = Fakes(earlier=None)
    assert prepare.run(make_deps(connect, scratch, second)) == 1
    assert "pre-update-0.5.0" in second.complained[0]
    assert second.calls == ["auto_init", "earlier:pre-update-0.5.0"]
    assert second.is_frozen()


# --- the freeze itself ---


def test_a_frozen_database_stops_writes_of_the_app_role_only(
    connect: Connect, scratch: Names
) -> None:
    prepared(connect, scratch)
    seed(connect, scratch, installed="0.4.0")
    existing = connect_as(scratch, scratch.app, PASSWORDS.app)
    existing.execute("INSERT INTO django_migrations VALUES ('documents', 'a_before')")

    freeze.freeze(connect, scratch)

    # The session that existed is ended: the old api reconnects, and then reads only.
    with pytest.raises(psycopg.OperationalError):
        existing.execute("SELECT 1")
    fresh = connect_as(scratch, scratch.app, PASSWORDS.app)
    assert fresh.execute("SELECT 1").fetchone() == (1,)
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        fresh.execute("INSERT INTO django_migrations VALUES ('documents', 'a_frozen')")
    fresh.close()
    assert freeze.is_frozen(connect, scratch)


def test_init_connections_write_while_frozen_and_a_fifth_does_not(
    connect: Connect, scratch: Names
) -> None:
    prepared(connect, scratch)
    seed(connect, scratch, installed="0.4.0")
    freeze.freeze(connect, scratch)
    insert = "INSERT INTO django_migrations VALUES ('documents', %s)"
    # The superuser connection of `init`, and the app, owner and back-up roles with the option.
    with connect(scratch.database) as superuser:
        superuser.execute("SET default_transaction_read_only = off")
        superuser.execute(insert, ["by_superuser"])
    for role, password in (
        (scratch.app, PASSWORDS.app),
        (scratch.owner, PASSWORDS.owner),
    ):
        with psycopg.connect(
            dbname=scratch.database,
            user=role,
            password=password,
            host=os.environ.get("PGHOST", "localhost"),
            autocommit=True,
            options="-c default_transaction_read_only=off",
        ) as writable:
            writable.execute(insert, [f"by_{role}"])
    # Every other connection, whatever version of the api opened it, is read-only.
    for role, password in ((scratch.app, PASSWORDS.app), (scratch.owner, PASSWORDS.owner)):
        other = connect_as(scratch, role, password)
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            other.execute(insert, ["by_other"])
        other.close()


def test_thaw_removes_the_setting(connect: Connect, scratch: Names) -> None:
    prepared(connect, scratch)
    freeze.freeze(connect, scratch)
    freeze.thaw(connect, scratch)
    assert not freeze.is_frozen(connect, scratch)
    with connect("postgres") as server:
        rows = server.execute(
            "SELECT count(*) FROM pg_db_role_setting s JOIN pg_database d ON d.oid = s.setdatabase"
            " WHERE d.datname = %s",
            [scratch.database],
        ).fetchone()
    assert rows == (0,)


def test_the_back_up_folder_is_prepared_before_the_back_up_of_an_update(
    connect: Connect, scratch: Names
) -> None:
    prepared(connect, scratch)
    seed(connect, scratch, installed="0.4.0")
    fakes = Fakes(auto_init_note="De back-upmap is klaargezet voor de back-ups.")
    assert prepare.run(make_deps(connect, scratch, fakes)) == 0
    assert fakes.calls.index("auto_init") < fakes.calls.index("backup:pre-update-0.5.0")
    assert "De back-upmap is klaargezet voor de back-ups." in fakes.said
