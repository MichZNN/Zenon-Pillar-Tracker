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

docker compose config --quiet
configured_services=$(docker compose config --services)
if ! printf '%s\n' "$configured_services" | grep -Fxq "web"; then
    printf '%s\n' "Deployment failed: the web service is not enabled." >&2
    exit 1
fi

collector_enabled=false
if printf '%s\n' "$configured_services" | grep -Fxq "collector"; then
    collector_enabled=true
fi

# A development deployment has the collector profile disabled. Remove an old
# development collector explicitly so a previous deployment cannot keep it
# running after the profile switch.
if [ "$collector_enabled" = false ]; then
    docker compose --profile collector stop collector >/dev/null 2>&1 || true
    docker compose --profile collector rm --force collector >/dev/null 2>&1 || true
fi

docker compose pull
docker compose up -d --remove-orphans

# `docker compose up -d` can succeed even when a service exits immediately.
# Fail the deployment in that case and expose the actual container output in
# the GitHub Actions log instead of reporting a false-positive deployment.
if ! running_services=$(docker compose ps --status running --services); then
    printf '%s\n' "Deployment could not inspect running services." >&2
    docker compose ps --all >&2 || true
    docker compose logs --no-color --tail=200 >&2 || true
    exit 1
fi
missing_services=""
expected_services="web"
if [ "$collector_enabled" = true ]; then
    expected_services="web
collector"
fi
for service in $expected_services; do
    if ! printf '%s\n' "$running_services" | grep -Fxq "$service"; then
        missing_services="$missing_services $service"
    fi
done
if [ -n "$missing_services" ]; then
    printf '%s\n' "Deployment failed: services are not running:$missing_services" >&2
    docker compose ps --all >&2 || true
    docker compose logs --no-color --tail=200 >&2 || true
    exit 1
fi

docker compose ps

if [ "$#" -eq 1 ]; then
    # Keep the selected immutable tag across a host reboot. This file contains
    # no secret and is separate from the operator-managed .env file. Persist
    # the selected profile as well so an older installed systemd unit still
    # follows the branch switch after a reboot.
    umask 077
    if [ "$collector_enabled" = true ]; then
        persisted_profiles=collector
    else
        persisted_profiles=
    fi
    printf 'IMAGE=%s\nCOMPOSE_PROFILES=%s\n' "$IMAGE" "$persisted_profiles" > "$IMAGE_STATE_FILE"
fi
