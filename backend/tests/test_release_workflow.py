"""The release workflow and its version check (.github/workflows/release.yml).

Publishing itself only a real tag can prove. These tests run the version check against
copies of the release files in tmp_path and read the workflow as text: what triggers it,
which job may write what, and that a run by hand publishes nothing.
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "scripts" / "release-check.py"
WORKFLOW = (ROOT / ".github" / "workflows" / "release.yml").read_text()
FILES = (
    "backend/pyproject.toml",
    "deploy/release.json",
    "CHANGELOG.md",
    "deploy/compose/docker-compose.yml",
    "backend/apps/updates/gate.py",
)


@pytest.fixture(autouse=True)
def _database() -> None:
    """Overrides the suite's database fixture: these tests read files only."""


@pytest.fixture(scope="session", autouse=True)
def _test_role_is_subject_to_rls() -> None:
    """Overrides the suite's session check: no database is used here."""


def version() -> str:
    match = re.search(r'^version = "([^"]+)"$', (ROOT / "backend/pyproject.toml").read_text(), re.M)
    assert match
    return match.group(1)


@pytest.fixture
def release(tmp_path: Path) -> Path:
    for name in FILES:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, tmp_path / name)
    return tmp_path


def check(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-I", str(SCRIPT), "--root", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def job(name: str) -> str:
    match = re.search(rf"^  {name}:\n(.*?)(?=^  [\w-]+:\n|\Z)", WORKFLOW, re.M | re.S)
    assert match, name
    return match.group(0)


# --- the version check ------------------------------------------------------------------


def test_the_repository_is_one_version(release: Path) -> None:
    notes = release / "notes.md"
    done = check(release, "--tag", f"v{version()}", "--notes", str(notes))
    assert done.returncode == 0, done.stderr
    assert done.stdout == f"version={version()}\n"
    assert "### " in notes.read_text()
    assert "## [" not in notes.read_text()


def test_a_tag_of_another_version_is_refused(release: Path) -> None:
    done = check(release, "--tag", "v9.9.9")
    assert done.returncode == 1
    assert "v9.9.9" in done.stderr
    assert done.stdout == ""


def test_release_json_must_follow_pyproject(release: Path) -> None:
    path = release / "deploy/release.json"
    path.write_text(path.read_text().replace(f'"version": "{version()}"', '"version": "9.9.9"'))
    done = check(release)
    assert done.returncode == 1
    assert "release.json" in done.stderr


def test_the_changelog_must_have_the_version_on_top(release: Path) -> None:
    path = release / "CHANGELOG.md"
    text = path.read_text().replace(f"## [{version()}]", "## [9.9.9]", 1)
    path.write_text(text)
    assert check(release).returncode == 1


def test_an_undated_changelog_entry_is_refused(release: Path) -> None:
    path = release / "CHANGELOG.md"
    path.write_text(re.sub(rf"(## \[{re.escape(version())}\]) - [\d-]+", r"\1", path.read_text()))
    done = check(release)
    assert done.returncode == 1
    assert "date" in done.stderr


def test_an_unreleased_section_is_skipped(release: Path) -> None:
    path = release / "CHANGELOG.md"
    text = path.read_text()
    first = text.index("## [")
    path.write_text(
        text[:first] + "## [Onuitgebracht]\n\n- regel-van-onuitgebracht\n\n" + text[first:]
    )
    notes = release / "notes.md"
    assert check(release, "--notes", str(notes)).returncode == 0
    assert "regel-van-onuitgebracht" not in notes.read_text()


# --- the workflow -----------------------------------------------------------------------


def test_only_a_version_tag_or_a_run_by_hand_starts_it() -> None:
    trigger = WORKFLOW.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert 'tags: ["v[0-9]+.[0-9]+.[0-9]+"]' in trigger
    assert "workflow_dispatch:" in trigger
    for other in ("pull_request", "branches", "schedule", "workflow_run", "repository_dispatch"):
        assert other not in trigger


def test_no_rights_by_default_and_each_job_names_its_own() -> None:
    assert "\npermissions: {}\n" in WORKFLOW
    for name in ("check", "scan", "publish", "publish-proxy", "release"):
        block = job(name)
        assert "    permissions:\n" in block, name
        assert re.search(r"timeout-minutes: \d+", block), name


def test_only_publish_may_write_packages_and_only_release_may_write_contents() -> None:
    for name in ("check", "scan", "publish", "publish-proxy", "release"):
        block = job(name)
        assert ("packages: write" in block) == name.startswith("publish"), name
        assert ("id-token: write" in block) == name.startswith("publish"), name
        assert ("contents: write" in block) == (name == "release"), name


def test_publishing_happens_only_on_a_tag() -> None:
    for name in ("publish", "publish-proxy", "release"):
        assert "if: ${{ github.event_name == 'push' }}" in job(name), name
    assert "push: false" in job("scan")
    assert "push: true" not in job("scan")
    assert "needs: [check, scan]" in job("publish")
    assert "needs: [check, publish, publish-proxy]" in job("release")
    assert "needs: [check, scan]" in job("publish-proxy")


def test_no_checkout_keeps_credentials() -> None:
    checkouts = WORKFLOW.count("uses: actions/checkout@")
    assert checkouts == 5
    assert WORKFLOW.count("persist-credentials: false") == checkouts


def test_a_version_is_a_fixed_tag_never_latest() -> None:
    assert ":latest" not in WORKFLOW
    publish = job("publish")
    assert 'imagetools create --tag "$IMAGE:$VERSION" "$IMAGE@$DIGEST"' in publish
    assert "linux/amd64,linux/arm64" in publish
    assert "push-by-digest=true" in publish


def test_what_gets_the_version_is_what_was_scanned() -> None:
    publish = job("publish")
    tagging = publish.index("imagetools create")
    for platform in ("linux/amd64", "linux/arm64"):
        scan = publish.index(f"TRIVY_PLATFORM: {platform}")
        assert scan < tagging, platform
    assert publish.count("image-ref: ${{ env.IMAGE }}@${{ steps.build.outputs.digest }}") == 2
    assert publish.index("imagetools create") < publish.index("attest-build-provenance")
    # What :VERSION points at is checked against the digest that gets the attestation.
    assert 'imagetools inspect "$IMAGE:$VERSION"' in publish
    assert 'test "$tagged" = "$DIGEST"' in publish
    assert publish.index('test "$tagged" = "$DIGEST"') < publish.index("attest-build-provenance")


def test_no_expression_reaches_a_shell_directly() -> None:
    # Values from the event go through env:, never into a run: script.
    for block in re.findall(r"run: \|\n((?:\s{10,}.*\n)+)", WORKFLOW):
        assert "${{" not in block


def test_local_builds_have_no_default_attestation_but_releases_keep_theirs() -> None:
    # A local build's provenance holds its build time, which changes the image id of
    # unchanged code under the containerd image store (every start recreated every
    # service). The wrapper turns the default off for its own builds through the
    # environment; compose gets no `provenance:` key, which older Compose refuses.
    wrapper = (ROOT / "brievie").read_text()
    assert re.search(
        r"^BUILDX_NO_DEFAULT_ATTESTATIONS=\$\{BUILDX_NO_DEFAULT_ATTESTATIONS:-1\}\n"
        r"export BUILDX_NO_DEFAULT_ATTESTATIONS$",
        wrapper,
        re.M,
    )
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text()
    assert "provenance:" not in compose
    assert "BUILDX_NO_DEFAULT_ATTESTATIONS" not in WORKFLOW
    assert "actions/attest-build-provenance@" in job("publish")


def _above(text: str) -> str:
    """A version one minor above `text`: a min_upgrade_from no release may have."""
    major, minor, _patch = text.split(".")
    return f"{major}.{int(minor) + 1}.0"


GOOD = {"format": 1, "version": version(), "min_upgrade_from": "0.2.0", "postgres_major": 18}
BAD_RELEASES = [
    {**GOOD, "format": 2},
    {**GOOD, "format": True},
    {**GOOD, "postgres_major": "18"},
    {**GOOD, "postgres_major": 9},
    {**GOOD, "postgres_major": True},
    {**GOOD, "min_upgrade_from": _above(version())},
    {**GOOD, "min_upgrade_from": "0.2"},
    {**GOOD, "extra": 1},
    {k: v for k, v in GOOD.items() if k != "postgres_major"},
]


@pytest.mark.parametrize("data", BAD_RELEASES)
def test_the_check_refuses_what_an_updater_would_refuse(
    release: Path, data: dict[str, object]
) -> None:
    from apps.updates.release import ReleaseError, parse_release

    text = json.dumps({**data, "version": version()} if "version" in data else data)
    with pytest.raises(ReleaseError):
        parse_release(text)
    (release / "deploy/release.json").write_text(text)
    done = check(release)
    assert done.returncode == 1, text
    assert "release.json" in done.stderr


def test_what_the_check_accepts_an_updater_reads(release: Path) -> None:
    from apps.updates.release import parse_release

    text = (release / "deploy/release.json").read_text()
    assert check(release).returncode == 0
    assert str(parse_release(text).version) == version()


# --- the proxy image and the compose file --------------------------------------------


def test_the_proxy_is_built_scanned_tagged_and_attested_like_the_backend() -> None:
    proxy = job("publish-proxy")
    assert "file: deploy/Dockerfile.proxy" in proxy
    assert "linux/amd64,linux/arm64" in proxy
    assert "push-by-digest=true" in proxy
    assert proxy.count("image-ref: ${{ env.PROXY_IMAGE }}@${{ steps.build.outputs.digest }}") == 2
    assert 'imagetools create --tag "$PROXY_IMAGE:$VERSION" "$PROXY_IMAGE@$DIGEST"' in proxy
    assert proxy.index("imagetools create") < proxy.index("attest-build-provenance")
    assert 'test "$tagged" = "$DIGEST"' in proxy
    assert "subject-name: ${{ env.PROXY_IMAGE }}" in proxy
    assert "file: deploy/Dockerfile.proxy" in job("scan")


def test_the_release_attaches_the_compose_file_and_its_pinned_copy() -> None:
    release = job("release")
    assert "needs.publish.outputs.digest" in release
    assert "needs.publish-proxy.outputs.digest" in release
    assert "pin-compose.py" in release
    assert '"$RUNNER_TEMP/docker-compose.yml" "$RUNNER_TEMP/docker-compose.pinned.yml"' in release
    assert "outputs:\n      digest: ${{ steps.build.outputs.digest }}" in job("publish")
    assert "outputs:\n      digest: ${{ steps.build.outputs.digest }}" in job("publish-proxy")


def test_the_compose_file_of_the_repository_is_checked_with_the_release(release: Path) -> None:
    assert check(release, "--tag", f"v{version()}").returncode == 0


def test_a_compose_file_on_another_version_fails_the_check(release: Path) -> None:
    path = release / "deploy/compose/docker-compose.yml"
    path.write_text(path.read_text().replace(f":-{version()}}}", ":-9.9.9}"))
    done = check(release)
    assert done.returncode == 1
    assert "docker-compose.yml" in done.stderr
    assert "9.9.9" in done.stderr


def test_two_images_on_different_versions_fail_the_check(release: Path) -> None:
    path = release / "deploy/compose/docker-compose.yml"
    text = path.read_text()
    first = text.index(f"brievie-proxy:${{BRIEVIE_VERSION:-{version()}}}")
    path.write_text(text[:first] + text[first:].replace(f":-{version()}}}", ":-9.9.9}", 1))
    done = check(release)
    assert done.returncode == 1
    assert "share one version" in done.stderr


def test_a_revision_that_differs_from_the_image_fails_the_check(release: Path) -> None:
    path = release / "backend/apps/updates/gate.py"
    path.write_text(re.sub(r"(?m)^(COMPOSE_REVISION = )\d+", r"\g<1>7", path.read_text()))
    done = check(release)
    assert done.returncode == 1
    assert "BRIEVIE_COMPOSE_REVISION" in done.stderr


def test_pin_compose_names_both_images_by_digest(tmp_path: Path) -> None:
    script = ROOT / ".github" / "scripts" / "pin-compose.py"
    out = tmp_path / "pinned.yml"
    backend, proxy = "sha256:" + "a" * 64, "sha256:" + "b" * 64
    done = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-I",
            str(script),
            str(ROOT / "deploy/compose/docker-compose.yml"),
            str(out),
            "--backend",
            backend,
            "--proxy",
            proxy,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    pinned = out.read_text()
    assert f"brievie-backend@{backend}" in pinned
    assert f"brievie-proxy@{proxy}" in pinned
    assert "brievie-backend:" not in pinned
    assert "brievie-proxy:" not in pinned
    assert "image: postgres:18" in pinned


def test_pin_compose_refuses_a_bad_digest(tmp_path: Path) -> None:
    script = ROOT / ".github" / "scripts" / "pin-compose.py"
    done = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-I",
            str(script),
            str(ROOT / "deploy/compose/docker-compose.yml"),
            str(tmp_path / "out.yml"),
            "--backend",
            "sha256:short",
            "--proxy",
            "sha256:" + "b" * 64,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert done.returncode == 1
    assert not (tmp_path / "out.yml").exists()
