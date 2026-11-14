#!/bin/sh
# Runs Caddy and copies the public root certificate of its internal CA to /ca,
# a volume the api mounts read-only. Caddy's private keys stay in /data, which
# only this container mounts. The root appears once Caddy has created its CA.
set -u

root=/data/caddy/pki/authorities/local/root.crt

caddy run --config /etc/caddy/Caddyfile --adapter caddyfile &
caddy_pid=$!
trap 'kill -TERM "$caddy_pid" 2>/dev/null' TERM INT

while kill -0 "$caddy_pid" 2>/dev/null; do
	if [ -f "$root" ] && ! cmp -s "$root" /ca/root.crt; then
		cp "$root" /ca/root.crt.tmp && chmod 0644 /ca/root.crt.tmp && mv /ca/root.crt.tmp /ca/root.crt
	fi
	sleep 2 &
	wait $!
done

wait "$caddy_pid"
