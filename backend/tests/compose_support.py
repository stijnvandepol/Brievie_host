"""Compares the two compose definitions (`docker compose config --format json` output).

Pure functions over the parsed config, so the test of the comparison needs no Docker; the
test that feeds it the real files runs in CI, where Docker is.
"""

Config = dict[str, object]

# The wrapper's one-shot `migrate` is `init` in the compose-only file, which also has
# `init-secrets` (nothing in the wrapper makes secrets inside a container).
RENAMED = {"migrate": "init"}
ONLY_COMPOSE = frozenset({"init-secrets"})
# Variables the compose-only file may have beyond the wrapper's: its settings (every
# service reads the host name, the revision of the file...), the passwords that live in
# files (`X_FILE` is counted as `X`), and the back-up variables `init` needs for the
# emergency back-up.
EXTRA_ENVIRONMENT = frozenset(
    {
        "BRIEVIE_COMPOSE_REVISION",
        "BRIEVIE_COMPOSE_INSTALL",
        "BRIEVIE_STRICT_SECRET_FILES",
        "BRIEVIE_HOSTNAME",
        "BRIEVIE_INIT_WRITABLE",
        "BRIEVIE_ADOPT_VERSION",
        "BRIEVIE_VERSION",
        "BRIEVIE_HTTP_PORT",
        "BRIEVIE_HTTPS_PORT",
        "BRIEVIE_BACKUP_DIR_SET",
        "BRIEVIE_BACKUP_REPOSITORY",
        "BRIEVIE_BACKUP_DIR_HOST",
        "BRIEVIE_BACKUP_AUTO_INIT",
        "BRIEVIE_BACKUP_HOUR",
        "BRIEVIE_BACKUP_APPEND_ONLY",
        "BRIEVIE_BACKUP_S3_ACCESS_KEY_ID",
        "BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY",
        "BRIEVIE_BACKUP_REST_USERNAME",
        "BRIEVIE_BACKUP_REST_PASSWORD",
        "BRIEVIE_BACKUP_PASSWORD",
        "BRIEVIE_DB_ROLE",
        "DATABASE_BACKUP_URL",
        "DATABASE_OWNER_URL",
        "DATABASE_SUPERUSER_URL",
        "DATABASE_URL",
        "DATABASE_PASSWORD",
        "DATABASE_OWNER_PASSWORD",
        "DATABASE_BACKUP_PASSWORD",
        "DATABASE_SUPERUSER_PASSWORD",
    }
)
# The wrapper's database passwords are variables of its `postgres` service; the compose-only
# file hands that service the superuser password as a file only (the others are made in
# the database by `init`).
POSTGRES_PASSWORDS = frozenset(
    {
        "BRIEVIE_OWNER_PASSWORD",
        "BRIEVIE_APP_PASSWORD",
        "BRIEVIE_BACKUP_DB_PASSWORD",
    }
)


def _services(config: Config) -> dict[str, dict[str, object]]:
    raw = config.get("services", {})
    assert isinstance(raw, dict)
    return {str(name): value for name, value in raw.items() if isinstance(value, dict)}


def environment_keys(service: dict[str, object]) -> set[str]:
    """The names of a service's variables, each `X_FILE` counted as `X`."""
    raw = service.get("environment", {})
    assert isinstance(raw, dict)
    return {str(name).removesuffix("_FILE") for name in raw}


def _published(service: dict[str, object]) -> set[str]:
    raw = service.get("ports", [])
    assert isinstance(raw, list)
    return {
        f"{port.get('published')}:{port.get('target')}" for port in raw if isinstance(port, dict)
    }


def _depends(service: dict[str, object]) -> dict[str, str]:
    raw = service.get("depends_on", {})
    assert isinstance(raw, dict)
    return {
        RENAMED.get(str(name), str(name)): str(value.get("condition", ""))
        for name, value in raw.items()
        if isinstance(value, dict)
    }


def drift(wrapper: Config, compose: Config) -> list[str]:
    """What differs between the wrapper's file and the compose-only file, as sentences.

    Empty when they agree: the same services (the wrapper's `migrate` is `init`), every
    environment key of the wrapper in the compose-only service (the `_FILE` spelling
    counts), no key beyond the known extras, the same published ports, the same
    `depends_on` (apart from `init-secrets`), the same api healthcheck and Caddy environment.
    """
    problems: list[str] = []
    old, new = _services(wrapper), _services(compose)
    expected = {RENAMED.get(name, name) for name in old} | ONLY_COMPOSE
    if set(new) != expected:
        problems.append(f"services differ: {sorted(set(new) ^ expected)}")
    for name, service in old.items():
        twin = new.get(RENAMED.get(name, name))
        if twin is None:
            continue
        want, have = environment_keys(service), environment_keys(twin)
        if name == "postgres":
            want -= POSTGRES_PASSWORDS
        if want - have:
            problems.append(f"{name}: environment keys missing: {sorted(want - have)}")
        if have - want - EXTRA_ENVIRONMENT:
            problems.append(f"{name}: environment keys only in the compose file: "
                            f"{sorted(have - want - EXTRA_ENVIRONMENT)}")  # fmt: skip
        if name != "migrate" and _published(service) != _published(twin):
            problems.append(f"{name}: published ports differ")
        twin_depends = {k: v for k, v in _depends(twin).items() if k != "init-secrets"}
        if name != "postgres" and _depends(service) != twin_depends:
            problems.append(f"{name}: depends_on differs")
    if "api" in new and old["api"].get("healthcheck") != new["api"].get("healthcheck"):
        problems.append("api: the healthcheck differs")
    if "caddy" in new and old["caddy"].get("environment") != new["caddy"].get("environment"):
        problems.append("caddy: the environment differs")
    return problems
