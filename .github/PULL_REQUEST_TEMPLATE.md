## What

<!-- What changes and why, in a few sentences. Link the issue if there is one. -->

## How it was checked

<!-- Commands you ran (make lint typecheck test, pnpm test, ...) and what you tried by hand. -->

## Not checked

<!-- What you could not or did not verify: a platform, a destination, a migration on real data. "Nothing" is a valid answer only when it is true. -->

## Checklist

- [ ] Tests for new or changed logic
- [ ] `make openapi` and `make web-api` after an API change; `make web-i18n` after changing visible text
- [ ] A line in `docs/DECISIONS.md` for a new dependency or a decision
- [ ] No document content, tokens, passwords or BSNs in logs, fixtures or screenshots
