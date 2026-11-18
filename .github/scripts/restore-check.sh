#!/usr/bin/env bash
# The assertions of the CI job `restore`, run after every restore: ./restore-check.sh SEED OLD_ROOT
#   SEED      the JSON line of restore-seed.py
#   OLD_ROOT  the certificate authority the server had BEFORE it was destroyed
# One request per page proves a lot at once: the sealed page opens with the restored
# master key and workspace key, the workspace's rows are complete, the paired device's
# token still signs in, and the server's certificate chains to the OLD authority (a
# phone that pinned it keeps working). No --insecure anywhere in this file.
set -euo pipefail
umask 077

seed=${1:?usage: restore-check.sh SEED OLD_ROOT}
root=${2:?usage: restore-check.sh SEED OLD_ROOT}
base=https://localhost/api/v1
work=$(mktemp -d "${RUNNER_TEMP:?}/restore-check.XXXXXX")
trap 'rm -rf "${work:?}"' EXIT

# The token goes to curl from a file (--header @file), never as a process argument.
printf 'Authorization: Bearer %s\n' "$(jq -r .token "$seed")" >"$work/header"

# --retry-all-errors: the server has just been started again.
fetch() {
	curl --fail --silent --show-error --retry 10 --retry-delay 3 --retry-all-errors \
		--cacert "$root" "$@"
}

checked=0
while IFS=$'\t' read -r workspace document expected; do
	actual=$(fetch --header "@$work/header" \
		"$base/workspaces/$workspace/documents/$document/pages/1" | sha256sum | cut -d' ' -f1)
	if [ "$actual" != "$expected" ]; then
		echo "Page of workspace $workspace has another hash after the restore." >&2
		exit 1
	fi
	checked=$((checked + 1))
done < <(jq -r '.workspaces[] | [.workspace, .document, .page_sha256] | @tsv' "$seed")
if [ "$checked" -ne 2 ]; then
	echo "Expected two seeded workspaces, found $checked." >&2
	exit 1
fi

# The authority is the same one: byte for byte, fetched over a connection that only
# the old authority can have signed.
fetch "$base/server/ca-certificate" | jq -j .pem >"$work/new-root.pem"
if ! cmp -s "$root" "$work/new-root.pem"; then
	echo "The certificate authority differs from the one before the restore." >&2
	exit 1
fi
echo "Restore check passed: $checked pages, one authority."
