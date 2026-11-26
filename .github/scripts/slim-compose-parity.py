"""Proves the short Synology compose file is the full one: same config, bar a named list.

    python3 .github/scripts/slim-compose-parity.py FULL.json SLIM.json

Both files are `docker compose config --format json` of the same default environment (the
two port variables set to 8080 and 8443, the defaults of the short file). Everything must be
equal, a service's environment apart, which may lack the variables of EXPECTED_OMITTED and
nothing else. This list is kept here on purpose, apart from the generator's own: a variable
the generator drops silently is a test failure, not a quiet change. Standard library only.
"""

import json
import sys
from pathlib import Path

EXPECTED_OMITTED = frozenset(
    {
        "ALLOWED_HOSTS",
        "BRIEVIE_AI_AUTOPILOT_ALLOWED",
        "BRIEVIE_AUDIT_RETENTION_DAYS",
        "BRIEVIE_BACKUP_APPEND_ONLY",
        "BRIEVIE_BACKUP_HOUR",
        "BRIEVIE_BACKUP_REPOSITORY",
        "BRIEVIE_BACKUP_REST_PASSWORD",
        "BRIEVIE_BACKUP_REST_PASSWORD_FILE",
        "BRIEVIE_BACKUP_REST_USERNAME",
        "BRIEVIE_BACKUP_S3_ACCESS_KEY_ID",
        "BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY",
        "BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY_FILE",
        "BRIEVIE_PUBLIC_HOSTNAME",
        "BRIEVIE_PUBLIC_HOST_MATCH",
        "BRIEVIE_PUBLIC_URLS",
        "BRIEVIE_PUBLIC_WEB",
        "BRIEVIE_REQUIRE_SECOND_FACTOR",
        "BRIEVIE_SECOND_FACTOR_GRACE_DAYS",
        "BRIEVIE_SESSION_IDLE_DAYS",
        "BRIEVIE_SESSION_MAX_DAYS",
        "BRIEVIE_SETUP_TOKEN",
        "BRIEVIE_TRUSTED_PROXIES",
        "BRIEVIE_TRUSTED_PROXIES_WIDE",
        "CSRF_COOKIE_SECURE",
        "CSRF_TRUSTED_ORIGINS",
        "DEBUG",
        "SESSION_COOKIE_SECURE",
    }
)


def _config(data: dict) -> dict:
    """The compose model without the `x-` extension fields."""
    return {key: value for key, value in data.items() if not key.startswith("x-")}


def differences(full: dict, slim: dict) -> list[str]:
    full, slim = _config(full), _config(slim)
    problems: list[str] = []
    for key in sorted(set(full) | set(slim)):
        if key != "services" and full.get(key) != slim.get(key):
            problems.append(f"top-level {key} differs")
    full_services, slim_services = full.get("services", {}), slim.get("services", {})
    if set(full_services) != set(slim_services):
        problems.append(
            f"services differ: {sorted(set(full_services) ^ set(slim_services))}"
        )
    for name in sorted(set(full_services) & set(slim_services)):
        a, b = dict(full_services[name]), dict(slim_services[name])
        env_a, env_b = a.pop("environment", {}), b.pop("environment", {})
        if a != b:
            fields = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
            problems.append(f"{name}: {fields} differ")
        extra = sorted(set(env_b) - set(env_a))
        if extra:
            problems.append(f"{name}: the short file adds {extra}")
        kept = {k: v for k, v in env_a.items() if k not in EXPECTED_OMITTED}
        if kept != env_b:
            changed = sorted(
                k for k in set(kept) | set(env_b) if kept.get(k) != env_b.get(k)
            )
            problems.append(f"{name}: environment differs in {changed}")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: slim-compose-parity.py FULL.json SLIM.json", file=sys.stderr)
        return 2
    full, slim = (json.loads(Path(path).read_text(encoding="utf-8")) for path in argv)
    problems = differences(full, slim)
    for problem in problems:
        print(f"parity: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
