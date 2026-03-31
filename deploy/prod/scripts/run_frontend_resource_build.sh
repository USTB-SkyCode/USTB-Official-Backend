#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"

RESOURCEPACK_HOST_PATH="${FRONTEND_RESOURCEPACK_HOST_PATH:-}"
FRONTEND_CONTEXT="${FRONTEND_BUILD_CONTEXT:-}"
REBUILD_IMAGE=1

usage() {
  cat <<'EOF'
Usage:
  run_frontend_resource_build.sh [options]

Options:
  --resourcepack-host PATH   Host directory containing the production equivalent of world/resource/resourcepack
  --frontend-context VALUE   Override FRONTEND_BUILD_CONTEXT for this invocation
  --no-build                 Reuse the existing builder image without rebuilding it
  -h, --help                Show this help text

Environment fallback:
  FRONTEND_RESOURCEPACK_HOST_PATH
  FRONTEND_BUILD_CONTEXT
EOF
}

log() {
  printf '[run_frontend_resource_build] %s\n' "$*"
}

die() {
  printf '[run_frontend_resource_build] ERROR: %s\n' "$*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --resourcepack-host)
      RESOURCEPACK_HOST_PATH="$2"
      shift 2
      ;;
    --frontend-context)
      FRONTEND_CONTEXT="$2"
      shift 2
      ;;
    --no-build)
      REBUILD_IMAGE=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

[[ -n "$RESOURCEPACK_HOST_PATH" ]] || die "--resourcepack-host is required (or set FRONTEND_RESOURCEPACK_HOST_PATH)"
[[ -d "$RESOURCEPACK_HOST_PATH" ]] || die "resourcepack host path does not exist: $RESOURCEPACK_HOST_PATH"

RESOURCEPACK_HOST_PATH="$(cd -- "$RESOURCEPACK_HOST_PATH" && pwd)"

if ! find "$RESOURCEPACK_HOST_PATH" -maxdepth 1 -type f -name '*.pack.json' | grep -q .; then
  log "warning: no *.pack.json found under $RESOURCEPACK_HOST_PATH"
fi

export FRONTEND_RESOURCEPACK_HOST_PATH="$RESOURCEPACK_HOST_PATH"

if [[ -n "$FRONTEND_CONTEXT" ]]; then
  export FRONTEND_BUILD_CONTEXT="$FRONTEND_CONTEXT"
fi

log "resourcepack_host=$FRONTEND_RESOURCEPACK_HOST_PATH"
log "output=docker volume 'frontend_packs' -> frontend container /data/packs"
if [[ -n "${FRONTEND_BUILD_CONTEXT:-}" ]]; then
  log "frontend_build_context=$FRONTEND_BUILD_CONTEXT"
fi

CMD=(docker compose --profile frontend-resource-build run --rm)
if [[ "$REBUILD_IMAGE" -eq 1 ]]; then
  CMD+=(--build)
fi
CMD+=(frontend-resource-builder)

cd "$COMPOSE_DIR"
"${CMD[@]}"
