#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"

command=${1:-status}
case "$command" in
    build) docker compose build ;;
    start|up) docker compose up -d ;;
    stop|down) docker compose down ;;
    restart) docker compose up -d --force-recreate ;;
    logs) docker compose logs -f --tail=200 gateway ;;
    status) docker compose ps ;;
    health)
        port=$(awk -F= '/^AMAZINGDATA_HTTP_PORT=/{print $2}' .env 2>/dev/null || true)
        port=${port:-8765}
        curl -fsS "http://127.0.0.1:${port}/health"
        printf '\n'
        ;;
    *)
        printf '%s\n' "Usage: ./scripts/manage.sh build|start|stop|restart|logs|status|health" >&2
        exit 2
        ;;
esac
