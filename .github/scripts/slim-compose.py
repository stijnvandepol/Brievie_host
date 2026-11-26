"""Writes docker-compose.synology.yml: the compose file, generated short for a NAS user.

    python3 .github/scripts/slim-compose.py FILE OUT

Same architecture, same services, networks, volumes and hardening as FILE; what goes is
what a household NAS never sets (comments, optional pass-through variables, tuning) and what
repeats (cap_drop and security_opt, the logging block). The two files must stay
interchangeable on the same data, so `docker compose config` of both is compared in CI
(.github/scripts/slim-compose-parity.py). The source is read as text (no YAML parser is a
dependency) and a source that no longer has the expected shape is an error, never a guess.
Standard library only.
"""

import re
import sys
from pathlib import Path

HEADER = """\
# Brievie for a Synology, generated from deploy/compose/docker-compose.yml (do not edit by hand).
# Container Manager > Project > Create (Maken): paste this file and change only the lines marked <--.
# No secret is in this file: Brievie makes its keys on the first start.
# The ports are 8080 and 8443 (a Synology uses 80 and 443 itself); the https port is part of the
# address in the QR code. Variables of the stack (BRIEVIE_HTTPS_PORT, ...) still override.
"""

# Environment variables left out of the short file (a household NAS never sets them). The
# service then runs with the image's own default, which the full file's default equals; the
# parity check names the same list on its own. Never a variable that holds a path, a secret
# or a mount (BRIEVIE_BACKUP_DIR, BRIEVIE_SCAN_DIR) or that the Caddyfile needs.
OMITTED = (
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
)

# A `# <--` comment block stays when the line under it names one of these (the address, the
# version, the backup folder and the scan folder, plus the one switch for a first backup).
KEEP_COMMENT_FOR = (
    "BRIEVIE_HOSTNAME",
    "BRIEVIE_VERSION",
    "BRIEVIE_BACKUP_DIR",
    "BRIEVIE_BACKUP_AUTO_INIT",
    "BRIEVIE_SCAN_GID",
)
PORTS = {"BRIEVIE_HTTP_PORT": ("80", "8080"), "BRIEVIE_HTTPS_PORT": ("443", "8443")}
REQUIRED_TOP = (
    "name",
    "x-backend",
    "x-settings",
    "x-backup",
    "services",
    "networks",
    "volumes",
)
REQUIRED_SERVICES = ("init-secrets", "postgres", "init", "api", "caddy")
HARDENING = ("    cap_drop: [ALL]", '    security_opt: ["no-new-privileges:true"]')
HARDENED_ANCHOR = 'x-hardened: &hardened\n  cap_drop: [ALL]\n  security_opt: ["no-new-privileges:true"]'
BACKEND_MERGE = "    <<: *backend"
LOGGING = re.compile(r"^  logging: (\{.*\})$")


class SlimError(Exception):
    pass


def _strip(lines: list[str]) -> list[str]:
    """Drops blank lines and comments, except the `<--` blocks about a setting that stays."""
    kept: list[str] = []
    block: list[str] = []
    for line in lines:
        if line.strip().startswith("#"):
            block.append(line)
            continue
        if (
            block
            and "<--" in block[0]
            and any(name in line for name in KEEP_COMMENT_FOR)
        ):
            kept.extend(
                line.replace(", or BRIEVIE_BACKUP_REPOSITORY to a remote place", "")
                for line in block
            )
        block = []
        if line.strip():
            kept.append(line)
    return kept


def _check_shape(lines: list[str]) -> None:
    tops = {m[1] for line in lines if (m := re.match(r"^([\w-]+):", line))}
    missing = [key for key in REQUIRED_TOP if key not in tops]
    if missing:
        raise SlimError(f"the source has no top-level {missing}")
    services = set(
        re.findall(r"^  ([\w-]+):\s*$", "\n".join(_section(lines, "services")), re.M)
    )
    missing = [name for name in REQUIRED_SERVICES if name not in services]
    if missing:
        raise SlimError(f"the source has no service {missing}")
    if not any(
        line.startswith("  image: ") and "${BRIEVIE_VERSION:-" in line for line in lines
    ):
        raise SlimError(
            "x-backend no longer names the image with ${BRIEVIE_VERSION:-...}"
        )
    if any(re.match(r"^\s+[\w-]+: [|>]", line) for line in lines):
        raise SlimError(
            "the source uses a block scalar; the generator does not read those"
        )


def _section(lines: list[str], name: str) -> list[str]:
    start = lines.index(f"{name}:")
    end = next(
        (i for i in range(start + 1, len(lines)) if re.match(r"^[\w-]+:", lines[i])),
        len(lines),
    )
    return lines[start + 1 : end]


def _omit(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for index, line in enumerate(lines):
        match = re.match(r"^\s+([A-Za-z0-9_]+): ", line)
        if match and match[1] in OMITTED:
            indent = len(line) - len(line.lstrip())
            nxt = lines[index + 1] if index + 1 < len(lines) else ""
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) > indent:
                raise SlimError(f"{match[1]} has a value over more than one line")
            seen.add(match[1])
            continue
        out.append(line)
    absent = sorted(set(OMITTED) - seen)
    if absent:
        raise SlimError(
            f"the source no longer has {absent}; update OMITTED and the parity list"
        )
    return out


def _ports(text: str) -> str:
    for name, (old, new) in PORTS.items():
        text, count = re.subn(rf"\$\{{{name}:-{old}\}}", f"${{{name}:-{new}}}", text)
        if not count:
            raise SlimError(f"the source has no default for {name}")
    return text


def _services(lines: list[str]) -> list[tuple[int, int, str]]:
    """(first line, end, name) of every service block."""
    start = lines.index("services:") + 1
    end = next(
        (i for i in range(start, len(lines)) if re.match(r"^[\w-]+:", lines[i])),
        len(lines),
    )
    heads = [i for i in range(start, end) if re.match(r"^  [\w-]+:\s*$", lines[i])]
    bounds = [*heads[1:], end]
    return [(h, b, lines[h].strip()[:-1]) for h, b in zip(heads, bounds)]


def _collapse(lines: list[str]) -> list[str]:
    """cap_drop and security_opt once (x-hardened), the logging block once (an alias)."""
    hardened = [
        (h, b)
        for h, b, _ in _services(lines)
        if all(line in lines[h:b] for line in HARDENING) and BACKEND_MERGE in lines[h:b]
    ]
    if len(hardened) < 2:
        raise SlimError("fewer than two services repeat cap_drop and security_opt")
    drop = {i for h, b in hardened for i in range(h, b) if lines[i] in HARDENING}
    merges = {i for h, b in hardened for i in range(h, b) if lines[i] == BACKEND_MERGE}
    out: list[str] = []
    for index, line in enumerate(lines):
        if index in drop:
            continue
        if index in merges:
            line = "    <<: [*backend, *hardened]"
        out.append(line)
    # The logging block: anchored in x-backend, an alias wherever a service repeats it.
    first = next((i for i, line in enumerate(out) if LOGGING.match(line)), None)
    if first is None:
        raise SlimError("x-backend has no logging block")
    value = LOGGING.match(out[first])[1]  # type: ignore[index]
    out[first] = f"  logging: &logging {value}"
    out = [
        f"    logging: *logging" if line == f"    logging: {value}" else line
        for line in out
    ]
    # x-hardened goes right under x-backend.
    after = next(
        i
        for i in range(out.index("x-backend: &backend") + 1, len(out))
        if re.match(r"^[\w-]+:", out[i])
    )
    return [*out[:after], *HARDENED_ANCHOR.split("\n"), *out[after:]]


def generate(text: str) -> str:
    lines = text.split("\n")
    _check_shape(lines)
    lines = _strip(lines)
    if "x-backend: &backend" not in lines:
        raise SlimError("x-backend is no longer the anchor &backend")
    for name in (
        "BRIEVIE_HOSTNAME: &hostname",
        "BRIEVIE_COMPOSE_REVISION:",
        "name: brievie",
    ):
        if not any(name in line for line in lines):
            raise SlimError(f"the source no longer has {name!r}")
    kept = "\n".join(lines)
    for name in ("BRIEVIE_HOSTNAME", "BRIEVIE_VERSION", "BRIEVIE_BACKUP_DIR"):
        if not re.search(rf"^\s*# <--.*\n(\s*#.*\n)*.*{name}", kept, re.M):
            raise SlimError(f"the `<--` comment about {name} is gone from the source")
    lines = _collapse(_omit(lines))
    return HEADER + _ports("\n".join(lines)) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: slim-compose.py FILE OUT", file=sys.stderr)
        return 2
    try:
        Path(argv[1]).write_text(
            generate(Path(argv[0]).read_text(encoding="utf-8")), encoding="utf-8"
        )
    except (SlimError, OSError) as error:
        print(f"slim-compose: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
