#!/bin/sh
# Install locked dependencies, build the interface and run the local simulated demo.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
for tool in uv node npm; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    printf '%s\n' "Missing $tool. Install the prerequisites listed in README.md." >&2
    exit 2
  fi
done
node -e 'if (Number(process.versions.node.split(".")[0]) < 22) { console.error("Node 22 or newer is required."); process.exit(2); }'
uv sync --locked --python 3.12
npm --prefix frontend ci --no-audit --no-fund
npm --prefix frontend run build
printf '\n%s\n' 'Open http://127.0.0.1:8000. Processing and payments are simulated. Press Ctrl+C to stop.'
exec env APP_ENV=local MODEL_MODE=mock ALLOW_LIVE_IN_SESSIONS=0 uv run python main.py --serve
