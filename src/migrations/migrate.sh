#!/usr/bin/env bash
# Usage: DATABASE_URL=postgres://... ./migrate.sh up|down
set -euo pipefail
cd "$(dirname "$0")"
: "${DATABASE_URL:?set DATABASE_URL}"

case "${1:-}" in
  up)   for f in $(ls *.up.sql   | sort);    do echo "-> $f"; psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -1 -f "$f"; done ;;
  down) for f in $(ls *.down.sql | sort -r); do echo "<- $f"; psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -1 -f "$f"; done ;;
  *) echo "usage: $0 up|down"; exit 1 ;;
esac
