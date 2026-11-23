"""The write stop of an update: the database goes read-only for everyone but `init`.

In compose the old api and worker keep running until `init` has finished, and the
emergency back-up and the migrations must not race a write. `ALTER DATABASE ... SET
default_transaction_read_only = on` makes every NEW session read-only, whichever version
of the api opens it; the sessions of the app role that exist are ended, so the old api
reconnects read-only. A connection that must write (`init`'s own) sets
`default_transaction_read_only=off` in its options (settings: BRIEVIE_INIT_WRITABLE).
Superuser only, through a connection to the `postgres` database.
"""

from psycopg import sql

from apps.installer.database import Connect, Names

SETTING = "default_transaction_read_only"


def freeze(connect: Connect, names: Names) -> None:
    with connect("postgres") as server:
        server.execute(
            sql.SQL("ALTER DATABASE {} SET default_transaction_read_only = on").format(
                sql.Identifier(names.database)
            )
        )
        server.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
            " WHERE datname = %s AND usename = %s AND pid <> pg_backend_pid()",
            [names.database, names.app],
        )


def thaw(connect: Connect, names: Names) -> None:
    with connect("postgres") as server:
        server.execute(
            sql.SQL("ALTER DATABASE {} RESET default_transaction_read_only").format(
                sql.Identifier(names.database)
            )
        )


def is_frozen(connect: Connect, names: Names) -> bool:
    with connect("postgres") as server:
        rows = server.execute(
            "SELECT s.setconfig FROM pg_db_role_setting s JOIN pg_database d"
            " ON d.oid = s.setdatabase WHERE d.datname = %s AND s.setrole = 0",
            [names.database],
        ).fetchall()
    return any(
        str(entry).replace(" ", "").lower() == f"{SETTING}=on"
        for (config,) in rows
        for entry in (config or [])
    )
