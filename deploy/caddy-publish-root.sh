#!/bin/sh
# Runs Caddy and copies the public root certificate of its internal CA to /ca,
# a volume the api mounts read-only. Caddy's private keys stay in /data, which
# only this container mounts. The root appears once Caddy has created its CA.
#
# It also copies the CA (certificates and keys) to /authority. That copy exists only
# for the back-up: only the keeper (read-only) and the one-off admin container mount
# it, never the api or the worker. The keeper runs as uid 10001, so the copy is its.
set -u

ca=/data/caddy/pki/authorities/local
root=$ca/root.crt

mkdir -p /authority && chown 10001:10001 /authority && chmod 0700 /authority

caddy run --config /etc/caddy/Caddyfile --adapter caddyfile &
caddy_pid=$!
trap 'kill -TERM "$caddy_pid" 2>/dev/null' TERM INT

while kill -0 "$caddy_pid" 2>/dev/null; do
	if [ -f "$root" ] && ! cmp -s "$root" /ca/root.crt; then
		cp "$root" /ca/root.crt.tmp && chmod 0644 /ca/root.crt.tmp && mv /ca/root.crt.tmp /ca/root.crt
	fi
	for f in root.crt root.key intermediate.crt intermediate.key; do
		if [ -f "$ca/$f" ] && ! cmp -s "$ca/$f" "/authority/$f"; then
			# umask 077: a key copy is never readable by others, not even before chmod.
			rm -f "/authority/$f.tmp" \
				&& (umask 077 && cp "$ca/$f" "/authority/$f.tmp") \
				&& chown 10001:10001 "/authority/$f.tmp" \
				&& chmod 0600 "/authority/$f.tmp" \
				&& mv "/authority/$f.tmp" "/authority/$f"
		fi
	done
	sleep 2 &
	wait $!
done

wait "$caddy_pid"
