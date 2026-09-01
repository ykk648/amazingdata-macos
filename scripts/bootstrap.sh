#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TGW_WHEEL=${1:-}
AMAZINGDATA_WHEEL=${2:-}

usage() {
    printf '%s\n' \
        "Usage:" \
        "  ./scripts/bootstrap.sh /path/to/tgw-*.whl /path/to/AmazingData-*.whl"
}

if [ -z "$TGW_WHEEL" ] || [ -z "$AMAZINGDATA_WHEEL" ]; then
    usage
    exit 2
fi
if [ ! -f "$TGW_WHEEL" ] || [ ! -f "$AMAZINGDATA_WHEEL" ]; then
    printf '%s\n' "Both wheel paths must point to existing files." >&2
    exit 2
fi

case "$(basename "$TGW_WHEEL")" in
    tgw-*.whl) ;;
    *) printf '%s\n' "The first wheel must match tgw-*.whl" >&2; exit 2 ;;
esac
case "$(basename "$AMAZINGDATA_WHEEL")" in
    AmazingData-*.whl) ;;
    *) printf '%s\n' "The second wheel must match AmazingData-*.whl" >&2; exit 2 ;;
esac

mkdir -p "$PROJECT_DIR/vendor"
find "$PROJECT_DIR/vendor" -maxdepth 1 -name '*.whl' -delete
cp "$TGW_WHEEL" "$PROJECT_DIR/vendor/"
cp "$AMAZINGDATA_WHEEL" "$PROJECT_DIR/vendor/"

if [ ! -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    printf '%s\n' "Created .env. Add your TGW credentials before querying data."
fi

if ! docker info >/dev/null 2>&1; then
    if command -v colima >/dev/null 2>&1; then
        if [ "$(uname -m)" = "arm64" ]; then
            colima start --dns 223.5.5.5 --dns 119.29.29.29 --vz-rosetta
        else
            colima start --dns 223.5.5.5 --dns 119.29.29.29
        fi
    else
        printf '%s\n' "Docker is not running. Start Docker Desktop and run this again." >&2
        exit 1
    fi
fi

if ! docker compose version >/dev/null 2>&1; then
    printf '%s\n' "Docker Compose v2 is required. On Homebrew Docker CLI, install docker-compose and configure its plugin path." >&2
    exit 1
fi
if ! docker buildx version >/dev/null 2>&1; then
    printf '%s\n' "Docker Buildx is required to build the linux/amd64 gateway image. On Homebrew, install docker-buildx." >&2
    exit 1
fi

cd "$PROJECT_DIR"
docker compose build
docker compose up -d

printf '%s\n' \
    "Gateway container is running." \
    "Health: http://127.0.0.1:8765/health (or your AMAZINGDATA_HTTP_PORT)" \
    "Next: edit .env, then run ./scripts/manage.sh restart"
