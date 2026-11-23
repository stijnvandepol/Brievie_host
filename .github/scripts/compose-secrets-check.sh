#!/usr/bin/env bash
# The assertions of the CI job `compose-stack` about the secrets of a compose-only install.
#
#   compose-secrets-check.sh check      the vault, each role's volume, and where no secret may be
#   compose-secrets-check.sh snapshot   one line per vault file: its checksum (not its content)
#   compose-secrets-check.sh selftest   proves the detector finds a secret where it is planted
#
# Needs COMPOSE (the docker compose command with both files) and RUNNER_TEMP. The secrets
# are read into files of this run only (mode 0600, removed at the end) and masked in the
# log; the output of this script never holds a value, only names and counts.
set -euo pipefail
umask 077

image=${BRIEVIE_CI_IMAGE:-brievie/backend:ci}
project=${COMPOSE_PROJECT:-brievie}
work=$(mktemp -d "${RUNNER_TEMP:?}/secrets-check.XXXXXX")
trap 'rm -rf "${work:?}"' EXIT

vault_files="postgres_password owner_password app_password backup_db_password secret_key installation_id backup_password master_key"

# Runs a shell snippet as root in a throwaway container with volume $1 mounted read-only at /v.
in_volume() {
	docker run --rm --user 0 --network none -v "${project}_$1:/v:ro" "$image" sh -c "$2"
}

# Whether any line of the file $2 is, as a whole substring, a line of $1 (the secrets).
contains_secret() {
	grep -qFf "$1" "$2"
}

selftest() {
	printf 'planted-secret-value-0123456789\n' >"$work/values"
	printf 'a log line without it\n' >"$work/clean"
	printf 'a log line with planted-secret-value-0123456789 in it\n' >"$work/leaky"
	if contains_secret "$work/values" "$work/clean"; then
		echo "selftest: the detector reports a secret in a clean file." >&2
		exit 1
	fi
	if ! contains_secret "$work/values" "$work/leaky"; then
		echo "selftest: the detector misses a planted secret." >&2
		exit 1
	fi
	echo "selftest: the detector finds a planted secret and nothing else."
}

snapshot() {
	for name in $vault_files; do
		in_volume secrets "sha256sum /v/$name" | cut -d' ' -f1 | sed "s|^|$name |"
	done
}

read_values() {
	: >"$work/values"
	for name in $vault_files; do
		value=$(in_volume secrets "cat /v/$name")
		echo "::add-mask::$value"
		printf '%s\n' "$value" >>"$work/values"
	done
	# A line shorter than 16 characters would match innocent text; every secret is longer.
	if awk 'length($0) < 16 { bad = 1 } END { exit !bad }' "$work/values"; then
		echo "A vault value is shorter than 16 characters." >&2
		exit 1
	fi
}

check_vault() {
	listing=$(in_volume secrets 'stat -c "%a %u %n" /v/*' | sort)
	for name in $vault_files; do
		grep -qx "400 10001 /v/$name" <<<"$listing" || {
			echo "The vault file $name is missing or not 0400 owned by uid 10001." >&2
			exit 1
		}
	done
	test "$(wc -l <<<"$listing")" -eq 8
	test "$(in_volume secrets 'stat -c %a /v')" = 700
}

check_roles() {
	test "$(in_volume secrets-postgres 'ls /v')" = postgres_password
	test "$(in_volume secrets-postgres 'stat -c %u /v/postgres_password')" = 999
	api=$(in_volume secrets-api 'ls /v' | tr '\n' ' ')
	test "$api" = "app_password backup_password master_key secret_key "
	keeper=$(in_volume secrets-keeper 'ls /v' | tr '\n' ' ')
	test "$keeper" = "app_password backup_db_password backup_password secret_key "
	if grep -q master_key <<<"$keeper"; then
		echo "The keeper's volume holds the master key." >&2
		exit 1
	fi
	for file in app_password master_key secret_key backup_password; do
		test "$(in_volume secrets-api "stat -c '%a %u' /v/$file")" = "400 10001"
	done
	# What the containers see, not only what the volumes hold.
	if $COMPOSE exec -T keeper ls /run/brievie | grep -q master_key; then
		echo "The keeper container sees the master key." >&2
		exit 1
	fi
	if $COMPOSE exec -T api ls /run/brievie | grep -qE 'owner_password|postgres_password'; then
		echo "The api container sees a superuser or owner password." >&2
		exit 1
	fi
}

check_network() {
	test "$(docker network inspect "${project}_db" --format '{{.Internal}}')" = true
	postgres=$($COMPOSE ps -q postgres)
	test -z "$(docker port "$postgres")"
	networks=$(docker inspect "$postgres" --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}')
	test "$networks" = "${project}_db "
}

collect_outputs() {
	$COMPOSE config >"$work/config.txt"
	# shellcheck disable=SC2046 # one id per container, word splitting is wanted
	docker inspect $(docker ps -aq --filter "label=com.docker.compose.project=$project") >"$work/inspect.txt"
	for service in $($COMPOSE config --services); do
		$COMPOSE logs --no-color "$service" >"$work/logs-$service.txt" 2>&1 || true
	done
	: >"$work/processes.txt"
	for id in $(docker ps -q --filter "label=com.docker.compose.project=$project"); do
		docker top "$id" -eo args >>"$work/processes.txt" 2>/dev/null || true
	done
}

check_nothing_leaks() {
	read_values
	collect_outputs
	for file in "$work/config.txt" "$work/inspect.txt" "$work/processes.txt" "$work"/logs-*.txt; do
		if contains_secret "$work/values" "$file"; then
			echo "A secret of the vault is in $(basename "$file")." >&2
			exit 1
		fi
	done
	echo "No secret of the vault is in the compose config, docker inspect, the logs or a process list."
}

case ${1:-} in
check)
	check_vault
	check_roles
	check_network
	check_nothing_leaks
	echo "Secrets check passed."
	;;
snapshot) snapshot ;;
selftest) selftest ;;
*)
	echo "usage: compose-secrets-check.sh check|snapshot|selftest" >&2
	exit 2
	;;
esac
