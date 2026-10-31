#!/usr/bin/env bash
# Usage: DATABASE_URL=postgres://... ./migrate.sh up|down
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"
set -a
source "$PARENT_DIR/.env"
set +a

cd "$SCRIPT_DIR"

: "${DATABASE_URL:?set DATABASE_URL}"
# echo "Arguments received: $#"
# echo "First argument: '${1:-EMPTY}'"
case "${1:-}" in
  up)
    for f in *.up.sql; do
      [[ -e "$f" ]] || continue
      echo "-> $f"
      psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -1 -f "$f"
    done
    ;;

  down)
    for f in *.down.sql; do
      [[ -e "$f" ]] || continue
      echo "<- $f"
      psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -1 -f "$f"
    done
    ;;

  *)
    echo "usage: $0 up|down"
    exit 1
    ;;
esac
