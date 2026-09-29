#!/usr/bin/env bash
# End-to-end run: socat pty pair <-> mock sensor <-> weather-sensor.
# Usage: scripts/demo.sh [seconds] [extra weather-sensor args...]
# Defaults to --stderr so it works without journald; pass nothing extra and
# drop --stderr below to log to the journal instead.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
bin="${BIN:-$here/../build/weather-sensor}"
secs="${1:-20}"; shift || true
dir="$(mktemp -d)"
trap 'kill $(jobs -p) 2>/dev/null || true; wait 2>/dev/null; rm -rf "$dir"' EXIT

socat pty,raw,echo=0,link="$dir/ttyMOCK" pty,raw,echo=0,link="$dir/ttyMOCK_PEER" &
for _ in $(seq 50); do [[ -e "$dir/ttyMOCK" && -e "$dir/ttyMOCK_PEER" ]] && break; sleep 0.1; done

python3 "$here/mock.py" --serial "$dir/ttyMOCK" --sensor a --corrupt-prob "${CORRUPT:-0.2}" -v \
    2> >(sed 's/^/[mock] /' >&2) &

"$bin" --stderr "$@" "$dir/ttyMOCK_PEER" &
svc=$!
sleep "$secs"
kill -USR1 "$svc"; sleep 0.2
kill -TERM "$svc"; wait "$svc"
