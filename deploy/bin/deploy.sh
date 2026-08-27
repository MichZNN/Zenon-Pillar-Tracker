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

docker compose config --quiet
docker compose pull web collector
docker compose up -d --remove-orphans
docker compose ps

if [ "$#" -eq 1 ]; then
    # Keep the selected immutable tag across a host reboot. This file contains
    # no secret and is separate from the operator-managed .env file.
    umask 077
    printf 'IMAGE=%s\n' "$IMAGE" > "$IMAGE_STATE_FILE"
fi
