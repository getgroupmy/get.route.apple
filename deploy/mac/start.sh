#!/bin/bash
# GET.route Apple Maps on the Mac mini (README.md in this folder): the same
# service as on the NAS, run straight on macOS, for launchd. It listens on this
# Mac only (127.0.0.1); the Mac's Cloudflare Tunnel brings applemap.getcdn.link to it.
#
# Settings: .env next to this file (copy .env.example). The database is in
# data/ next to it unless APPLEMAP_DATA_DIR says otherwise.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
ENV_FILE="$HERE/.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "No $ENV_FILE: copy .env.example to .env and fill it in (README.md)." >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

export APPLEMAP_DATA_DIR="${APPLEMAP_DATA_DIR:-$HERE/data}"
export APPLE_MAPS_KEY_FILE="${APPLE_MAPS_KEY_FILE:-$HERE/secrets/apple_maps.p8}"
mkdir -p "$APPLEMAP_DATA_DIR" "$HERE/logs"
chmod 700 "$APPLEMAP_DATA_DIR"

cd "$REPO"
exec "$REPO/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port "${APPLEMAP_PORT:-8300}" \
  --no-access-log --proxy-headers --forwarded-allow-ips 127.0.0.1
