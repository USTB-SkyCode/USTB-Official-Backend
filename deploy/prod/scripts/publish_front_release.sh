#!/usr/bin/env bash

set -euo pipefail

STATIC_ROOT="/srv/ustb/prod/front-static"
SITE_URL=""
KEEP_RELEASES=3
ARCHIVE_PATH=""
SOURCE_DIR=""
RELEASE_NAME=""
SMOKE_ASSET_PATH=""
RUN_SMOKE_CHECKS=1

usage() {
  cat <<'INNEREOF'
Usage:
  publish_front_release.sh (--archive PATH | --source-dir PATH) [options]

Options:
  --archive PATH         Path to a .tar.gz payload already present on the server
  --source-dir PATH      Path to an extracted payload directory already present on the server
  --static-root PATH     Stable parent directory containing live + releases
  --site-url URL         Production site base URL used for smoke checks
  --keep N               Number of releases to keep after a successful publish
  --release-name NAME    Explicit release directory name
  --smoke-asset PATH     Relative asset path to smoke-check
  --no-smoke             Skip HTTP smoke checks
  -h, --help             Show this help text
INNEREOF
}

log() {
  printf '[publish_front_release] %s\n' "$*"
}

die() {
  printf '[publish_front_release] ERROR: %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

join_url() {
  local base="$1"
  local path="$2"
  base="${base%/}"
  if [[ "$path" == /* ]]; then
    printf '%s%s\n' "$base" "$path"
  else
    printf '%s/%s\n' "$base" "$path"
  fi
}

http_status() {
  local url="$1"
  curl --silent --show-error --location --output /dev/null --write-out '%{http_code}' "$url"
}

discover_smoke_asset() {
  local release_dir="$1"
  find "$release_dir/assets" -maxdepth 1 -type f \( -name '*.js' -o -name '*.css' -o -name '*.wasm' \) | sort | head -n 1
}

validate_release_layout() {
  local release_dir="$1"
  [[ -f "$release_dir/index.html" ]] || die "Release is missing index.html"
  [[ -d "$release_dir/assets" ]] || die "Release is missing assets/"
  [[ -d "$release_dir/basic" ]] || die "Release is missing basic/"
  [[ -d "$release_dir/model" ]] || die "Release is missing model/"
}

cleanup() {
  if [[ -n "${TEMP_DIR:-}" && -d "${TEMP_DIR}" ]]; then
    rm -rf "${TEMP_DIR}"
  fi
}

trap cleanup EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --archive)
      ARCHIVE_PATH="$2"
      shift 2
      ;;
    --source-dir)
      SOURCE_DIR="$2"
      shift 2
      ;;
    --static-root)
      STATIC_ROOT="$2"
      shift 2
      ;;
    --site-url)
      SITE_URL="$2"
      shift 2
      ;;
    --keep)
      KEEP_RELEASES="$2"
      shift 2
      ;;
    --release-name)
      RELEASE_NAME="$2"
      shift 2
      ;;
    --smoke-asset)
      SMOKE_ASSET_PATH="$2"
      shift 2
      ;;
    --no-smoke)
      RUN_SMOKE_CHECKS=0
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

if [[ -n "$ARCHIVE_PATH" && -n "$SOURCE_DIR" ]]; then
  die "Use either --archive or --source-dir, not both"
fi

if [[ -z "$ARCHIVE_PATH" && -z "$SOURCE_DIR" ]]; then
  die "One of --archive or --source-dir is required"
fi

if [[ "$RUN_SMOKE_CHECKS" -eq 1 && -z "$SITE_URL" ]]; then
  die "--site-url is required unless --no-smoke is used"
fi

require_cmd rsync
require_cmd find
require_cmd ln
require_cmd mv

if [[ "$RUN_SMOKE_CHECKS" -eq 1 ]]; then
  require_cmd curl
fi

if [[ -n "$ARCHIVE_PATH" ]]; then
  require_cmd tar
  [[ -f "$ARCHIVE_PATH" ]] || die "Archive not found: $ARCHIVE_PATH"
  TEMP_DIR="$(mktemp -d)"
  tar -xzf "$ARCHIVE_PATH" -C "$TEMP_DIR"
  SOURCE_DIR="$TEMP_DIR"
fi

[[ -d "$SOURCE_DIR" ]] || die "Source directory not found: $SOURCE_DIR"

if [[ -z "$RELEASE_NAME" ]]; then
  RELEASE_NAME="release-$(date +%Y%m%d-%H%M%S)"
fi

RELEASES_DIR="$STATIC_ROOT/releases"
LIVE_LINK="$STATIC_ROOT/live"
LIVE_LINK_TEMP="$STATIC_ROOT/live.__new"
NEW_RELEASE_DIR="$RELEASES_DIR/$RELEASE_NAME"

mkdir -p "$STATIC_ROOT" "$RELEASES_DIR"
rm -rf "$NEW_RELEASE_DIR"
mkdir -p "$NEW_RELEASE_DIR"

log "Syncing payload into $NEW_RELEASE_DIR"
rsync -a --delete "$SOURCE_DIR/" "$NEW_RELEASE_DIR/"

validate_release_layout "$NEW_RELEASE_DIR"

PREVIOUS_LIVE_TARGET=""
if [[ -L "$LIVE_LINK" ]]; then
  PREVIOUS_LIVE_TARGET="$(readlink "$LIVE_LINK")"
fi

if [[ -z "$SMOKE_ASSET_PATH" ]]; then
  SMOKE_ASSET_FILE="$(discover_smoke_asset "$NEW_RELEASE_DIR")"
  [[ -n "$SMOKE_ASSET_FILE" ]] || die "Unable to discover a smoke-check asset under assets/"
  SMOKE_ASSET_PATH="${SMOKE_ASSET_FILE#"$NEW_RELEASE_DIR"}"
fi

log "Switching live symlink to $RELEASE_NAME"
ln -sfn "releases/$RELEASE_NAME" "$LIVE_LINK_TEMP"
mv -Tf "$LIVE_LINK_TEMP" "$LIVE_LINK"

if [[ "$RUN_SMOKE_CHECKS" -eq 1 ]]; then
  INDEX_STATUS="$(http_status "$(join_url "$SITE_URL" "/")")"
  if [[ "$INDEX_STATUS" != "200" ]]; then
    if [[ -n "$PREVIOUS_LIVE_TARGET" ]]; then
      ln -sfn "$PREVIOUS_LIVE_TARGET" "$LIVE_LINK_TEMP"
      mv -Tf "$LIVE_LINK_TEMP" "$LIVE_LINK"
    fi
    die "Smoke check failed for / with status $INDEX_STATUS"
  fi

  ASSET_STATUS="$(http_status "$(join_url "$SITE_URL" "$SMOKE_ASSET_PATH")")"
  if [[ "$ASSET_STATUS" != "200" ]]; then
    if [[ -n "$PREVIOUS_LIVE_TARGET" ]]; then
      ln -sfn "$PREVIOUS_LIVE_TARGET" "$LIVE_LINK_TEMP"
      mv -Tf "$LIVE_LINK_TEMP" "$LIVE_LINK"
    fi
    die "Smoke check failed for $SMOKE_ASSET_PATH with status $ASSET_STATUS"
  fi
fi

if [[ "$KEEP_RELEASES" =~ ^[0-9]+$ ]] && (( KEEP_RELEASES > 0 )); then
  log "Keeping latest $KEEP_RELEASES releases"
  find "$RELEASES_DIR" -mindepth 1 -maxdepth 1 -type d -name 'release-*' | sort -r | tail -n "+$((KEEP_RELEASES + 1))" | xargs -r rm -rf --
fi

log "Publish completed"
log "static_root=$STATIC_ROOT"
log "live=$(readlink "$LIVE_LINK")"
log "release=$NEW_RELEASE_DIR"
