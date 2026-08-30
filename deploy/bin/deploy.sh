#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DEPLOY_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
IMAGE_STATE_FILE="$DEPLOY_DIR/.deploy-image.env"
cd "$DEPLOY_DIR"

if [ "$#" -gt 1 ]; then
    printf '%s\n' "Usage: $0 [image-reference]" >&2
    exit 2
fi

if [ "$#" -eq 1 ]; then
    # IMAGE is an environment override, so deployment never edits .env and
    # cannot overwrite secrets or host-specific settings.
    export IMAGE="$1"
fi

if [ ! -f .env ]; then
    printf '%s\n' "Missing $DEPLOY_DIR/.env; create it from .env.example first." >&2
    exit 1
fi
if [ ! -r .env ]; then
    printf '%s\n' "Cannot read $DEPLOY_DIR/.env as deployment user $(id -un)." >&2
    printf '%s\n' "The file must be owned by DEPLOY_USER and kept mode 600; fix its ownership and retry." >&2
    exit 1
fi

# Keep the mount point available even before the optional host control bridge
# is installed. The bridge service later tightens its ownership and mode.
control_dir=./control
if [ ! -d "$control_dir" ]; then
    mkdir -p "$control_dir"
fi

docker compose config --quiet
docker compose pull web collector
docker compose up -d --remove-orphans

# `docker compose up -d` can succeed even when a service exits immediately.
# Fail the deployment in that case and expose the actual container output in
# the GitHub Actions log instead of reporting a false-positive deployment.
if ! running_services=$(docker compose ps --status running --services); then
    printf '%s\n' "Deployment could not inspect running services." >&2
    docker compose ps --all >&2 || true
    docker compose logs --no-color --tail=200 web collector >&2 || true
    exit 1
fi
missing_services=""
for service in web collector; do
    if ! printf '%s\n' "$running_services" | grep -Fxq "$service"; then
        missing_services="$missing_services $service"
    fi
done
if [ -n "$missing_services" ]; then
    printf '%s\n' "Deployment failed: services are not running:$missing_services" >&2
    docker compose ps --all >&2 || true
    docker compose logs --no-color --tail=200 web collector >&2 || true
    exit 1
fi

docker compose ps

if [ "$#" -eq 1 ]; then
    # Keep the selected immutable tag across a host reboot. This file contains
    # no secret and is separate from the operator-managed .env file.
    umask 077
    printf 'IMAGE=%s\n' "$IMAGE" > "$IMAGE_STATE_FILE"
fi
