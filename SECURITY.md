# Security

Brievie keeps people's letters: tax, health, debts. A security problem here can hurt
someone, so reports are welcome and taken seriously.

## Reporting a vulnerability

Please do not open a public issue for a security problem.

- Mail **info@brievie.com**, or
- use GitHub's private report form: *Security* → *Report a vulnerability* on this
  repository (if the form is available).

Tell us what you found, how to reproduce it and which version or commit you looked at.
Never send real letters, passwords, tokens or a real `BRIEVIE_MASTER_KEY`; made-up data is
enough.

Brievie is a small project. You get an answer as soon as possible, and you are told when
a fix is released. There is no bug bounty.

## Supported versions

Fixes go into the latest release (until the first release: the `main` branch). Older
versions get no separate fixes: update to the latest one.

## Scope

In scope: the server in `Brievie_host`, the iOS app in `Brievie_ios`, the install script
on brievie.com.

Out of scope: problems that need an attacker who already is administrator of the server
or has the master key, and the AI service or other third parties a user chose to
connect.

## How this repository is checked

Every pull request and every push to `main` runs these checks in GitHub Actions
(`.github/workflows/`). They find known classes of problems; they do not prove the
absence of others, and no external audit has been done.

- **Static analysis:** CodeQL (`security-extended` queries) on the Python, TypeScript and
  workflow code, also weekly; ruff with the flake8-bandit (`S`) rules; `mypy --strict`.
- **Dependencies:** pip-audit on every package in `backend/uv.lock`, `pnpm audit` on the
  web interface's production packages (high and critical), Dependabot for updates.
- **Secrets:** gitleaks on the commits of every push and pull request.
- **The image:** hadolint on `deploy/Dockerfile`; Trivy on the built image, failing on
  high and critical vulnerabilities that have a fix.
- **The running server:** OWASP ZAP against the stack as a user installs it: a passive
  scan of the web interface and an active scan of every API operation in the OpenAPI
  contract, without logging in. Pages behind a login are not scanned yet.
- **The workflows:** zizmor; every third-party action is pinned to a commit.
- **Behaviour:** the test suite runs as a database role that row-level security applies
  to, and a CI job restores a back-up onto an empty machine on every change.

## What Brievie promises

`docs/PRIVACY.md` in `Brievie_host` describes what is stored, how it is protected and
what leaves the server. If the software does something else than that document says,
that is a security problem: please report it.
