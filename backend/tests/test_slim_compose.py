"""The short Synology compose file: its generator and its parity check, read as files only.

`docker compose config` of the short and the full file is compared in CI (job
`compose-slim`) with .github/scripts/slim-compose-parity.py; these tests hold the rules of
the generator and the wiring of the release and CI.
"""

import importlib.util
import re
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "deploy" / "compose" / "docker-compose.yml").read_text()


def _load(name: str, file: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / ".github" / "scripts" / file)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


slim = _load("slim_compose", "slim-compose.py")
parity = _load("slim_compose_parity", "slim-compose-parity.py")
OUT = slim.generate(SOURCE)


@pytest.fixture(autouse=True)
def _database() -> None:
    """Overrides the suite's database fixture: these tests read files only."""


@pytest.fixture(scope="session", autouse=True)
def _test_role_is_subject_to_rls() -> None:
    """Overrides the suite's session check: no database is used here."""


def _names(text: str, top: str) -> list[str]:
    body = re.search(rf"^{top}:\n(.*?)(?=^[\w-]+:|\Z)", text, re.M | re.S)
    assert body
    return re.findall(r"^  ([\w-]+):", body[1], re.M)


def test_the_generator_and_the_parity_check_name_the_same_omitted_variables() -> None:
    assert set(slim.OMITTED) == set(parity.EXPECTED_OMITTED)
    assert len(slim.OMITTED) == len(set(slim.OMITTED))


def test_it_is_much_shorter_and_has_nothing_to_edit_by_hand() -> None:
    assert len(OUT.splitlines()) < len(SOURCE.splitlines()) * 0.7
    assert OUT.startswith("# Brievie for a Synology, generated from")
    assert "Container Manager" in OUT.splitlines()[1]
    assert OUT.endswith("\n")
    assert not OUT.endswith("\n\n")


def test_every_comment_is_the_header_or_a_marked_line() -> None:
    header = slim.HEADER.splitlines()
    body = OUT.splitlines()[len(header) :]
    assert OUT.splitlines()[: len(header)] == header
    block_marked = False
    for line in body:
        if not line.strip().startswith("#"):
            block_marked = False
            continue
        block_marked = block_marked or "<--" in line
        assert block_marked, line
    marked = "\n".join(line for line in body if "<--" in line)
    for what in ("The version", "name or IP address", "back-up folder outside"):
        assert what in marked or what.replace("back-up folder outside", "folder outside") in marked


def test_the_settings_that_stay_are_still_there() -> None:
    for text in (
        "name: brievie",
        "BRIEVIE_HOSTNAME: &hostname ${BRIEVIE_HOSTNAME:-verander-mij.local}",
        "BRIEVIE_HOSTNAME: *hostname",
        "${BRIEVIE_VERSION:-",
        "BRIEVIE_VPN_HOSTNAME: ${BRIEVIE_VPN_HOSTNAME:-}",
        "${BRIEVIE_BACKUP_DIR:-backup-unset}:/backup",
        "${BRIEVIE_SCAN_DIR:-scan-unset}:/scan",
        "BRIEVIE_SCAN_GID",
    ):
        assert text in OUT, text
    assert OUT.count("BRIEVIE_HOSTNAME: &hostname") == 1
    revision = r'BRIEVIE_COMPOSE_REVISION: "(\d+)"'
    assert re.findall(revision, OUT) == re.findall(revision, SOURCE) != []


def test_services_networks_and_volumes_are_those_of_the_full_file() -> None:
    for top in ("services", "networks", "volumes"):
        assert _names(OUT, top) == _names(SOURCE, top), top
    assert 'profiles: ["admin"]' in OUT
    assert "internal: true" in OUT


def test_the_omitted_variables_are_gone_and_nothing_else_changed_in_the_environment() -> None:
    def keys(text: str) -> set[str]:
        return set(re.findall(r"^\s+([A-Z][A-Z0-9_]+): ", text, re.M))

    assert keys(SOURCE) - keys(OUT) == set(slim.OMITTED)
    assert not keys(OUT) & set(slim.OMITTED)
    assert keys(OUT) - keys(SOURCE) == set()


def test_the_defaults_for_the_ports_are_8080_and_8443() -> None:
    assert "${BRIEVIE_HTTP_PORT:-80}" not in OUT
    assert '"${BRIEVIE_HTTP_PORT:-8080}:80"' in OUT
    assert '"${BRIEVIE_HTTPS_PORT:-8443}:443"' in OUT


def test_the_hardening_is_kept_by_merge_keys_not_by_dropping_it() -> None:
    def hardened(text: str) -> set[str]:
        return {
            name
            for name, body in re.findall(r"^  ([\w-]+):\n((?:    .*\n|      .*\n)*)", text, re.M)
            if ("cap_drop: [ALL]" in body and "no-new-privileges:true" in body)
            or "*hardened" in body
        }

    assert hardened(OUT) == hardened(SOURCE)
    assert len(hardened(OUT)) >= 5
    assert OUT.count("cap_drop: [ALL]") == 1
    assert OUT.count("no-new-privileges") == 1
    assert OUT.count("max-size") == 1
    assert "logging: *logging" in OUT
    for service in ("init-secrets", "intake", "keeper", "api", "worker", "init"):
        assert re.search(rf"^  {service}:\n    <<: \[\*backend, \*hardened\]", OUT, re.M), service
    assert "read_only: true" in OUT
    assert "cap_add: [CHOWN, DAC_OVERRIDE, FOWNER]" in OUT
    assert re.search(r"^  admin:\n    <<: \*backend\n", OUT, re.M)


def test_the_output_carries_no_secret() -> None:
    assert not re.search(
        r"(PASSWORD|SECRET_KEY|MASTER_KEY|TOKEN)(?!\w*_FILE)\w*: [A-Za-z0-9+/]{12,}", OUT
    )


@pytest.mark.parametrize(
    "broken",
    [
        lambda s: s.replace("x-backup:", "x-backups:"),
        lambda s: s.replace("  caddy:\n", "  proxy:\n"),
        lambda s: s.replace("BRIEVIE_PUBLIC_WEB:", "BRIEVIE_PUBLIC_WEB_NEW:"),
        lambda s: s.replace("x-backend: &backend", "x-backend: &base"),
        lambda s: s.replace("BRIEVIE_HOSTNAME: &hostname", "BRIEVIE_HOSTNAME:"),
        lambda s: s.replace("# <-- The version.", "# The version."),
        lambda s: s.replace("${BRIEVIE_HTTP_PORT:-80}", "80"),
        lambda s: s.replace("    cap_drop: [ALL]\n", ""),
    ],
)
def test_a_source_of_another_shape_is_an_error(broken: Callable[[str], str]) -> None:
    with pytest.raises(slim.SlimError):
        slim.generate(broken(SOURCE))


def test_a_new_service_in_the_source_is_kept_without_changing_the_generator() -> None:
    extra = SOURCE.replace(
        "\nnetworks:\n",
        '\n  extra:\n    <<: *backend\n    command: ["true"]\n    cap_drop: [ALL]\n'
        '    security_opt: ["no-new-privileges:true"]\n\nnetworks:\n',
        1,
    )
    out = slim.generate(extra)
    assert "extra" in _names(out, "services")
    assert re.search(r"^  extra:\n    <<: \[\*backend, \*hardened\]", out, re.M)


def test_the_release_attaches_the_generated_file() -> None:
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert "slim-compose.py deploy/compose/docker-compose.yml" in release
    assert '"$RUNNER_TEMP/docker-compose.synology.yml"' in release


def test_ci_compares_the_two_files_and_runs_the_secrets_check_on_the_short_one() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    match = re.search(r"^  compose-slim:\n(.*?)(?=^  [\w-]+:\n|^  # )", ci, re.M | re.S)
    assert match
    job = match[1]
    assert "!= 'schedule'" in job
    assert "timeout-minutes:" in job
    steps = job.split("steps:", 1)[1]
    assert steps.index('test "$RUNNER_ENVIRONMENT" = github-hosted') < steps.index("docker")
    assert "slim-compose-parity.py" in job
    assert "config --format json" in job
    assert "compose-secrets-check.sh check" in job
    assert "down --volumes" in job.rsplit("- name: Stop", 1)[1]


def test_the_parity_check_accepts_only_the_listed_omissions() -> None:
    full = {"services": {"api": {"image": "x", "environment": {"A": "1", "DEBUG": "false"}}}}
    same = {"services": {"api": {"image": "x", "environment": {"A": "1"}}}}
    assert parity.differences(full, same) == []
    other = {"services": {"api": {"image": "x", "environment": {"A": "2"}}}}
    assert parity.differences(full, other)
    lost = {"services": {"api": {"image": "x", "environment": {}}}}
    assert parity.differences(full, lost)
    added = {"services": {"api": {"image": "x", "environment": {"A": "1", "B": "1"}}}}
    assert parity.differences(full, added)
    renamed = {"services": {"api": {"image": "y", "environment": {"A": "1"}}}}
    assert parity.differences(full, renamed) == ["api: ['image'] differ"]
    assert parity.differences({**full, "volumes": {"a": {}}}, {**same, "volumes": {}})
