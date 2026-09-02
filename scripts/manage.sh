#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"

command=${1:-status}

wait_for_gateway() {
    port=$(awk -F= '/^AMAZINGDATA_HTTP_PORT=/{print $2}' .env 2>/dev/null || true)
    port=${port:-8765}
    health_url="http://127.0.0.1:${port}/health"
    attempts=30

    while [ "$attempts" -gt 0 ]; do
        if curl -fsS "$health_url" >/dev/null 2>&1; then
            return 0
        fi
        attempts=$((attempts - 1))
        sleep 1
    done

    printf '%s\n' "Gateway did not become ready at ${health_url}; run ./scripts/manage.sh logs" >&2
    return 1
}

case "$command" in
    build) docker compose build ;;
    start|up)
        docker compose up -d
        wait_for_gateway
        ;;
    stop|down) docker compose down ;;
    restart)
        docker compose up -d --force-recreate
        wait_for_gateway
        ;;
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
