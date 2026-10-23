#!/usr/bin/env bash
# Passively capture one public passport page with REA through a loopback-only headless Chromium.
#
# Usage: capture_live.sh <passport-url> <output-dir> [settle-ms]
#
# Starts Chromium on about:blank (through $HTTPS_PROXY when set), attaches REA in connect mode,
# navigates to the URL inside a recorded browser scenario so the passport's own data requests
# are captured, then runs REA's static bundle analysis on the loaded page. Only point this at
# pages that are publicly reachable without signing in; it never clicks, types or submits.
set -euo pipefail

url=${1:?passport URL}
out=${2:?output directory}
settle_ms=${3:-8000}
port=${REA_CDP_PORT:-9222}
browser=${REA_BROWSER_EXECUTABLE:?set REA_BROWSER_EXECUTABLE}

mkdir -p "$out"
profile=$(mktemp -d)
proxy_args=()
[ -n "${HTTPS_PROXY:-}" ] && proxy_args=(--proxy-server="$HTTPS_PROXY")

"$browser" --headless=new --no-sandbox --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port="$port" --user-data-dir="$profile" "${proxy_args[@]}" about:blank \
  >"$out/chromium.log" 2>&1 &
browser_pid=$!
trap 'kill "$browser_pid" 2>/dev/null || true; wait "$browser_pid" 2>/dev/null || true; rm -rf "$profile" 2>/dev/null || true' EXIT

endpoint="http://127.0.0.1:$port"
for _ in $(seq 1 30); do
  curl -sf "$endpoint/json/version" >/dev/null && break
  sleep 1
done
first_page() {
  curl -sf "$endpoint/json/list" | python3 -I -c '
import json, sys
print([t for t in json.load(sys.stdin) if t["type"] == "page"][0][sys.argv[1]])' "$1"
}
target=$(first_page id)

python3 -I - "$endpoint" "$target" "$url" "$settle_ms" >"$out/scenario.json" <<'EOF'
import json, sys
endpoint, target, url, settle = sys.argv[1:5]
print(json.dumps({
    "browser": {"mode": "connect", "cdp_endpoint": endpoint, "target_id": target},
    "start_url": {"url": url},
    "actions": [{"step_id": "settle", "action": "wait_for_timeout", "duration_ms": int(settle)}],
    "capture": {"at_end": ["accessibility", "url", "screenshot"],
                "events": ["network", "console", "page-errors"]},
}, indent=1))
EOF

rea capture-browser-scenario "$out/scenario.json" --format json >"$out/scenario-evidence.json"

origin=$(first_page url | python3 -I -c '
import sys
from urllib.parse import urlsplit
parts = urlsplit(sys.stdin.read().strip())
print(f"{parts.scheme}://{parts.netloc}")')
rea analyze-web-bundle "$endpoint" "$target" --allowed-origins "$origin" --fetch-source-maps \
  --format json >"$out/bundle.json" || true
rea discover-webmcp-tools "$endpoint" "$target" --allowed-origins "$origin" --format json \
  >"$out/webmcp.json" || true
echo "captured $url ($origin) into $out"
