#!/usr/bin/env bash
# Package the service as a single executable file (Python zipapp).
# Output: dist/weather-sensor  -> install to /usr/local/bin/weather-sensor
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT

cp -r "$root/weather_sensor" "$stage/"
find "$stage" -name __pycache__ -prune -exec rm -rf {} +
cat > "$stage/__main__.py" <<'EOF'
import sys
from weather_sensor.service import main
sys.exit(main())
EOF

mkdir -p "$root/dist"
python3 -m zipapp "$stage" -p "/usr/bin/python3" -o "$root/dist/weather-sensor"
chmod 755 "$root/dist/weather-sensor"
echo "built $root/dist/weather-sensor"
