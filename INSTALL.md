# Installation and deployment

This document describes the recommended production installation of Zenon Pillar
Tracker on a Linux server with Docker. Debian ARM64 is the primary target, but
the application uses no Ubuntu-specific commands or paths. The GitHub workflow
builds one multi-architecture image for `linux/amd64` and `linux/arm64`.

## Architecture

The production installation consists of two containers:

- `web` serves the dashboard on port 8080.
- `collector` collects Zenon data in a separate background container managed by
  Docker.

Both containers use the same host directory for SQLite and the application log.
Runtime configuration is stored in SQLite; the application does not read JSON
configuration at runtime. `.env` is reserved for deployment values and secrets
that do not belong in the database, such as the optional Telegram bot token.

The `collector` service is protected by the Compose `collector` profile. The
production environment enables that profile with `COMPOSE_PROFILES=collector`;
the development environment leaves it empty and runs only the web container.
The GitHub workflow applies the same switch automatically: pushes to
`development` do not start `pillar_tracker.py`, while pushes to `main` start
both services.

The Linux Compose collector uses the host network namespace. A Zenon node or
reverse proxy listening on the same server can therefore be configured as
`http://127.0.0.1:35997`. Use `https://` when that port terminates TLS, for
example `https://zenon.turmin.com:35997`; the two schemes are not
interchangeable.

The application image uses Python `3.14.5`. The application runs as UID/GID
`10001` (`tracker`) without root privileges. Only the mounted data directory is
writable. The application log always uses the fixed path
`data_store/pillar_tracker.log`; only rotation size, backup count, and log level
are editable in the admin portal. Compose additionally limits Docker
stdout/stderr logs to 10 MB with a maximum of three files per container.

Systemd does not run inside a container. If the Linux host uses systemd, one
host unit manages the Compose stack. On a Linux host without systemd, the same
stack can be managed directly with `docker compose`.

## Requirements

Install a supported Docker Engine with Compose v2 on the server, following the
documentation for the selected Linux distribution. No Ubuntu installation or
Ubuntu package is required. Verify the installation:

```sh
docker --version
docker compose version
```

The deployment uses a rootful Docker daemon. The deployment user must be
allowed to call Docker, for example through the distribution's Docker group or
an equivalent access model. Membership of the `docker` group is effectively
root-equivalent in practice; use a separate deployment user and restrict SSH
access accordingly.

## Create the server directory

Use `/srv/zenon-pillar-tracker` as the standard directory. The included systemd
unit uses this path. If you choose another directory, change `WorkingDirectory`
in `deploy/systemd/zenon-pillar-tracker.service` before installing the unit.

```sh
sudo mkdir -p /srv/zenon-pillar-tracker/deploy/bin
sudo mkdir -p /srv/zenon-pillar-tracker/deploy/systemd
sudo mkdir -p /srv/zenon-pillar-tracker/data_store
sudo chown -R "$(id -u):$(id -g)" /srv/zenon-pillar-tracker
```

Copy the following files from this repository:

```sh
cp compose.yaml /srv/zenon-pillar-tracker/compose.yaml
cp deploy/bin/deploy.sh /srv/zenon-pillar-tracker/deploy/bin/deploy.sh
cp deploy/systemd/zenon-pillar-tracker.service /srv/zenon-pillar-tracker/deploy/systemd/zenon-pillar-tracker.service
cp .env.example /srv/zenon-pillar-tracker/.env
```

## Environment file and write permissions

Open `/srv/zenon-pillar-tracker/.env` and fill in the deployment values. Never
put real secrets in GitHub or in this repository.

```dotenv
IMAGE=ghcr.io/michznn/zenon-pillar-tracker:main
WEB_BIND_ADDRESS=127.0.0.1
WEB_PORT=8080
DATA_DIR=/srv/zenon-pillar-tracker/data_store
COMPOSE_PROFILES=collector
TELEGRAM_BOT_API_KEY=
```

`TELEGRAM_BOT_API_KEY` is optional. An administrator configures the Telegram
and Discord settings that belong in the database after signing in to the
portal. Restrict access to the environment file:

```sh
sudo chmod 600 /srv/zenon-pillar-tracker/.env
sudo chown "$(id -un):$(id -gn)" /srv/zenon-pillar-tracker/.env
```

Run these commands while logged in as the same account configured as
`DEPLOY_USER`. The GitHub workflow runs the deployment script as that account;
Docker access alone does not grant permission to read `.env`.

The directory configured in `DATA_DIR` must be writable by the container user
`10001:10001`. This is required for SQLite, SQLite journal files, and rotating
application logs:

```sh
sudo chown -R 10001:10001 /srv/zenon-pillar-tracker/data_store
sudo chmod 750 /srv/zenon-pillar-tracker/data_store
```

If `DATA_DIR` points to another existing directory, apply the same permissions
to that exact directory. Do not delete files to resolve a permissions issue.

## First start

Validate the Compose configuration and start only the web container first:

```sh
cd /srv/zenon-pillar-tracker
docker compose config
docker compose pull web
docker compose up -d web
```

Initialize the database explicitly. The script is idempotent and preserves
existing data:

```sh
docker compose run --rm web python tools/setup_database.py --database /app/data_store/pillar_tracker.sqlite3
```

Start the collector afterwards:

```sh
docker compose --profile collector up -d collector
docker compose ps
docker compose logs --tail=100 web collector
```

Open the dashboard through the reverse proxy or locally at
`http://127.0.0.1:8080`. When the database contains no accounts, `/portal`
automatically redirects to `/setup`. Create the first administrator account
there, then configure the node and notification settings in SQLite.

Bind the web container to localhost by default and use an HTTPS reverse proxy
for internet access. Set `WEB_BIND_ADDRESS` to a public address only when
firewall and TLS protection are correctly handled elsewhere.

## Systemd on the Linux host

Use systemd only on hosts that actually run it. The unit starts and stops the
Compose stack; systemd is not installed in the image.

```sh
sudo cp /srv/zenon-pillar-tracker/deploy/systemd/zenon-pillar-tracker.service /etc/systemd/system/zenon-pillar-tracker.service
sudo systemctl daemon-reload
sudo systemctl enable --now zenon-pillar-tracker.service
sudo systemctl status zenon-pillar-tracker.service
```

Useful management commands:

```sh
sudo systemctl restart zenon-pillar-tracker.service
sudo systemctl stop zenon-pillar-tracker.service
sudo systemctl start zenon-pillar-tracker.service
docker compose -f /srv/zenon-pillar-tracker/compose.yaml ps
```

The Compose `restart: unless-stopped` policy restarts a crashed container. The
systemd unit starts the stack after a host reboot and manages the complete
stack. `docker compose down` removes the containers and network, but not the
bind-mounted `data_store`.

On a distribution where the Docker systemd unit has a name other than
`docker.service`, change only `Requires=` and `After=` in the included unit. The
Docker and application commands remain the same.

## Test with Docker locally

The same image works on Windows for development when Docker Desktop is running.
The production target remains Linux; Windows is only an additional Python test
environment in the workflow.

```sh
docker build --tag zenon-pillar-tracker:local .
IMAGE=zenon-pillar-tracker:local docker compose up -d web
docker compose run --rm web python tools/setup_database.py --database /app/data_store/pillar_tracker.sqlite3
IMAGE=zenon-pillar-tracker:local docker compose up -d collector
```

On Windows, `DATA_DIR` in `.env` can point to a directory shared with Docker
Desktop. Check file-sharing and volume permissions explicitly in that setup.

## First development deployment on Debian ARM64

The first remote deployment uses a separate Compose stack so it cannot share
the production database, logs, containers, or host port. The development stack
uses `/srv/zenon-pillar-tracker-dev`, port `8081`, and the environment template
at `deploy/examples/development.env.example`.

Copy the repository's deployment files to the server, then prepare the
development directory:

```sh
sudo mkdir -p /srv/zenon-pillar-tracker-dev/deploy/bin
sudo mkdir -p /srv/zenon-pillar-tracker-dev/deploy/systemd
sudo mkdir -p /srv/zenon-pillar-tracker-dev/deploy/nginx
sudo mkdir -p /srv/zenon-pillar-tracker-dev/data_store
sudo chown -R "$(id -u):$(id -g)" /srv/zenon-pillar-tracker-dev
cp compose.yaml /srv/zenon-pillar-tracker-dev/compose.yaml
cp deploy/bin/deploy.sh /srv/zenon-pillar-tracker-dev/deploy/bin/deploy.sh
cp deploy/examples/development.env.example /srv/zenon-pillar-tracker-dev/.env
cp deploy/systemd/zenon-pillar-tracker-dev.service /srv/zenon-pillar-tracker-dev/deploy/systemd/zenon-pillar-tracker-dev.service
cp deploy/nginx/pillartracker.turmin.com.bootstrap.conf /srv/zenon-pillar-tracker-dev/deploy/nginx/pillartracker.turmin.com.bootstrap.conf
cp deploy/nginx/pillartracker.turmin.com.conf /srv/zenon-pillar-tracker-dev/deploy/nginx/pillartracker.turmin.com.conf
```

Set the real Telegram token only when development notifications are needed.
The directory used by `DATA_DIR` must be writable by the container user:

```sh
sudo chmod 600 /srv/zenon-pillar-tracker-dev/.env
sudo chown "$(id -un):$(id -gn)" /srv/zenon-pillar-tracker-dev/.env
sudo chown -R 10001:10001 /srv/zenon-pillar-tracker-dev/data_store
sudo chmod 750 /srv/zenon-pillar-tracker-dev/data_store
```

Start and initialize the development stack:

```sh
cd /srv/zenon-pillar-tracker-dev
docker compose config
docker compose up -d web
docker compose run --rm web python tools/setup_database.py --database /app/data_store/pillar_tracker.sqlite3
docker compose ps
```

The development unit is optional because Compose already uses
`restart: unless-stopped`. If the host uses systemd, install the development
unit and enable it:

```sh
sudo cp deploy/systemd/zenon-pillar-tracker-dev.service /etc/systemd/system/zenon-pillar-tracker-dev.service
sudo systemctl daemon-reload
sudo systemctl enable --now zenon-pillar-tracker-dev.service
```

### NGINX and DNS

Create a DNS `A` record for `pillartracker.turmin.com` that points to the VPS.
Add an `AAAA` record only when IPv6 is configured correctly. Allow inbound TCP
ports 80 and 443 in the host or provider firewall.

Before enabling this server block, inspect the existing NGINX configuration
with `sudo nginx -T` and confirm that no other server block already claims
`pillartracker.turmin.com`. Leave unrelated `turmin.com` configurations
unchanged.

The supplied NGINX configuration routes the domain to the development web
container on `127.0.0.1:8081`. It does not expose Docker or the application
port publicly. First install the temporary HTTP configuration so Let's Encrypt
can validate the domain:

```sh
sudo mkdir -p /var/www/certbot
sudo cp deploy/nginx/pillartracker.turmin.com.bootstrap.conf /etc/nginx/sites-available/pillartracker.turmin.com
sudo ln -sf /etc/nginx/sites-available/pillartracker.turmin.com /etc/nginx/sites-enabled/pillartracker.turmin.com
sudo nginx -t
sudo systemctl reload nginx
sudo certbot certonly --webroot -w /var/www/certbot -d pillartracker.turmin.com
```

After the certificate has been issued, replace the temporary configuration with
the HTTPS configuration and reload NGINX:

```sh
sudo cp deploy/nginx/pillartracker.turmin.com.conf /etc/nginx/sites-available/pillartracker.turmin.com
sudo nginx -t
sudo systemctl reload nginx
```

Do not install both supplied configurations at the same time. The final file
still points to the development stack on port `8081`; change that one upstream
port to `8080` when this hostname is promoted to production. The application
currently uses root-relative URLs, so `/dev` on the same hostname is not enabled
by this configuration: it would break frontend assets and API calls. Use the
hostname root for this first development environment. If development and
production must run simultaneously, use a separate hostname such as
`dev.pillartracker.turmin.com` or add explicit application base-path support
before using `/dev`.

## Automatic GitHub deployment

`.github/workflows/ci-cd.yml` performs the following steps:

1. Pull requests and pushes to `development` and `main` run tests natively on
   Windows and inside a Debian ARM64 container.
2. A push to `development` builds a `development` image and deploys the
   development environment. A push to `main` (including a merge from
   `development` to `main`) builds and deploys the production environment.
3. Each image is published to GHCR with its channel tag (`development` or
   `main`) and an immutable full commit-SHA tag.
4. The deployment job uploads `compose.yaml`, the deployment script, and the
   selected environment's systemd unit to the selected server path.
5. The script pulls the SHA-tagged image and runs `docker compose up -d`.
   Changed containers are replaced/restarted while SQLite and logs remain in
   `DATA_DIR`.

The script is copied to the server again on every deployment. The application
source does not need to be checked out on the server because it is inside the
container image. The matching systemd unit only needs to be installed during
the first setup. The workflow does not change secrets or overwrite `.env`. The
deployment script stores the active image tag in `.deploy-image.env`, so the
same version remains active after a host reboot.

The Debian ARM64 test job uses `ubuntu-latest` only as a GitHub host for Docker
and QEMU; the Python tests run in `python:3.14.5-slim-bookworm` with platform
`linux/arm64`. Ubuntu is not a production or application-runtime assumption.

### Required GitHub secrets

Create `development` and `production` GitHub Environments. Add the following
secrets to each environment, using the development server/path for
`development` and the production server/path for `production`:

| Secret | Value |
| --- | --- |
| `DEPLOY_HOST` | DNS name or IP address of the server |
| `DEPLOY_PORT` | SSH port; empty means `22` |
| `DEPLOY_USER` | Dedicated SSH/deployment user |
| `DEPLOY_PATH` | Deployment directory, such as `/srv/zenon-pillar-tracker` or `/srv/zenon-pillar-tracker-dev` |
| `DEPLOY_SSH_KEY` | Private ed25519 SSH key for the deployment user |
| `DEPLOY_KNOWN_HOSTS` | Pre-verified host key line(s) |

If the GHCR package is private, also add:

| Secret | Value |
| --- | --- |
| `GHCR_DEPLOY_USERNAME` | GitHub username for the package login |
| `GHCR_DEPLOY_TOKEN` | Token with at least `read:packages` |

The workflow does not automatically accept an unknown host key:
`DEPLOY_KNOWN_HOSTS` must be populated in advance by an administrator with a
verified SSH host key. This prevents a deployment from silently targeting the
wrong host.

The deployment user must be able to run `docker compose`. Root privileges are
needed during the initial setup for `/srv`, the systemd unit, and data-directory
permissions; after that, the workflow only needs Docker access.

## Manual update and rollback

A manual update uses the same script as GitHub Actions:

```sh
cd /srv/zenon-pillar-tracker
sh deploy/bin/deploy.sh ghcr.io/michznn/zenon-pillar-tracker:<commit-sha>
```

For a rollback, use the full SHA of the previous known-good deployment. Every
image has its own SHA tag, so no source code needs to be restored on the server.
The selected rollback tag is stored in `.deploy-image.env` and therefore remains
active after a reboot. Check the result:

```sh
docker compose ps
docker compose logs --tail=100 web collector
```

If the build or test job fails, the deployment job does not start. If a
deployment job fails before `docker compose up`, the running containers remain
untouched.

## Logs and backups

View container logs with:

```sh
cd /srv/zenon-pillar-tracker
docker compose logs --tail=200 web collector
```

The application log is always stored at
`/srv/zenon-pillar-tracker/data_store/pillar_tracker.log`. The path is fixed;
an administrator can change only the maximum size, backup count, and log level
in the portal. Docker stdout/stderr logs additionally have a fixed limit of
10 MB × 3 per container in `compose.yaml`.

The Operations section also displays the last collector attempt, the last
successful poll, and the exact error from the latest failed node RPC check.
Databases from older releases may still contain a `log_path` row, but it is
ignored; the application always writes to the fixed `data_store` path above.

Prefer backing up SQLite and logs while the containers are briefly stopped:

```sh
cd /srv/zenon-pillar-tracker
docker compose stop web collector
tar -czf "zenon-pillar-tracker-backup-$(date +%Y%m%d-%H%M%S).tar.gz" data_store
docker compose start web collector
```

Store backups outside the deployment directory and periodically verify that a
backup can actually be restored.

## Collector operation and runtime settings

The collector is supervised by Docker Compose and, on Linux hosts that use it,
the supplied systemd unit. Compose restarts a crashed collector automatically,
and the systemd unit starts the complete stack after a host reboot. The portal
therefore does not expose Docker or systemd Start, Stop, or Restart commands.

Runtime settings edited by an administrator are stored in SQLite. The collector
checks the settings revision every 60 seconds and reloads valid changes without
restarting. This includes node endpoints, polling interval, retry settings,
pillar thresholds, and notification destinations. Multiple saves close
together are coalesced naturally: the collector applies the latest committed
configuration rather than starting a restart for every save.

The portal reads the shared application log and SQLite audit trail directly.
Container stdout/stderr logs remain available to a host operator with
`docker compose logs` when deeper deployment diagnostics are needed.

Older installations may still have the former control-bridge systemd unit
installed. It is no longer used by the application; remove it once during
deployment cleanup if it exists:

```sh
sudo systemctl disable --now zenon-pillar-tracker-control.service
```

This is a one-time cleanup for older hosts. Normal settings changes and
deployments do not require a VPS CLI action.
