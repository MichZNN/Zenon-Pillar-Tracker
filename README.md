# Zenon Pillar Tracker

A collector and mobile-first dashboard for Zenon Network Pillars. The application collects current node data, stores snapshots in SQLite, and displays pillar status, 30-day performance, epoch history, events, and live duration.

The existing Telegram channel is still supported as an optional notification output.

## What is included?

- `tools/setup_database.py` — creates the SQLite database and all tables and indexes.
- `tools/create_admin.py` — creates an administrator without exposing a password in a command line.
- `tools/migrate_legacy_json.py` — imports an old JSON cache into SQLite once.
- `tools/backfill_epoch_starts.py` — fills fallback epoch start estimates and applies observed on-chain transitions when available.
- `tools/backfill_epoch_announcements.py` — recovers live Telegram send times from the notification outbox.
- `tools/build_favicons.py` — rebuilds all favicon and web app icon sizes from one PNG source.
- `tools/epoch_schedule.py` — calculates fallback epoch starts for historical tools.
- `pillar_tracker.py`, `collector.py`, `web_app.py` — small root CLI compatibility entrypoints.
- `controllers/` — HTTP and collector orchestration.
- `models/` — SQLite persistence and repositories.
- `services/` — authentication, settings, logging, and notifications.
- `functions/` — pure status and subscription domain functions.
- `utils/` — internal RPC, HTTP, Telegram, Discord, and environment helpers.
- `templates/` — mobile-first dashboard, one login page, and one role-aware portal.
- `deploy/` — Docker deployment script, systemd units, development environment template, and NGINX configuration.
- `SETUP.md` — current setup, run commands, status timing, and database safety.
- `INSTALL.md` — Docker, Debian ARM64/Linux deployment, systemd, GitHub Actions, and rollback.
- `.plans/REPORT_AND_IMPLEMENTATION_PLAN.md` — technical analysis and roadmap.

## Installation

Create the virtual environment and optional secret file:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

On Linux/macOS:

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Runtime settings are stored in SQLite. No JSON configuration file is read by
the application. After creating the database, open `/portal`; when the database
has no accounts yet, it automatically redirects to the first-run setup page at
`/setup`. Create the administrator there and use the administrator section of
`/portal` to configure at least these values:

- `node_rpc_urls`
- `reference_reward_address`

`node_rpc_urls` must contain at least one Zenon HTTP(S) JSON-RPC endpoint. This
application sends JSON-RPC requests with HTTP `POST`; it does not use
WebSockets.

Put the primary endpoint first in `node_rpc_urls` and one or more backup
endpoints after it. A list with one URL is a valid single-node configuration.
The collector checks the node's frontier and synchronization state before
collecting a snapshot and fails over when an endpoint is unavailable, not
synchronized, too old, or returns invalid data. A single node that briefly
reports `state = 1` receives a short synchronization grace period. If it does
not recover, that poll is deferred without sending a collector error. The
primary endpoint is periodically tested again after it recovers.

`reference_reward_address` must contain a valid pillar address with reward
history (normally a `z1...` address). The collector uses this address to
determine the latest epoch and to import reward history.

For a live epoch transition, the collector stores the timestamp of the first
observed momentum carrying the new epoch as `epoch_start_at`. This is the best
available on-chain timestamp and is kept separate from the Telegram send time.
If the transition is not observed during live collection, the collector does
not invent a new start time. Historical import and backfill tools can use an
optional schedule fallback and mark the resulting value `Estimated`; those
settings are not part of the live collector configuration.

Telegram is optional. The bot token is read only from the root `.env` file;
`telegram_channel_id` and the other Telegram settings are stored in SQLite and
editable by administrators.

## Create the database

Run:

```
python tools/setup_database.py
```

This creates:

```
data_store/pillar_tracker.sqlite3
```

The script is idempotent: running it again keeps existing data and applies the current schema.

The first administrator can be created in the browser by opening `/portal` and
following the automatic redirect to `/setup`. For headless installations, use:

```
python tools/create_admin.py
```

Start the dashboard, open `/portal`, and configure the collector settings. The
setup form signs the new administrator in automatically. The command-line
utility remains available when a browser is not convenient.
There is one login page and one role-aware portal: normal users see and manage
their own subscriptions, while administrators additionally see settings,
users, all subscriptions, logs, and the audit trail.

## Import an old JSON cache

If the old tracker created a JSON cache, import it once:

```
python tools/migrate_legacy_json.py
```

The importer deliberately refuses to overwrite a snapshot that already exists for the same pillar and epoch. This prevents duplicate or accidental replacement of historical data.

## Run the collector

Run one poll:

```
python pillar_tracker.py
```

The collector:

- obtains the current epoch through the configured node RPC;
- selects a healthy node RPC endpoint and fails over to a backup when needed;
- handles RPC errors, invalid responses, and request timeouts;
- rejects an epoch when the node response is not coherent;
- retrieves all pillar pages;
- retrieves missing reward history when a new epoch is detected;
- stores epoch, pillar, snapshot, node-health, poll-run, and event data;
- detects status transitions and pillar additions/removals;
- calculates how long each pillar has continuously been live;
- sends notifications through the configured notification channels.

Run continuously:

```
python pillar_tracker.py --loop
```

The default loop interval is stored in SQLite and can be changed in the admin
panel. Restart the collector after changing collector settings. Each loop
iteration performs one complete poll and then waits for the configured
interval. The collector does not use a separate hidden epoch timer: every poll
obtains the current epoch from the node and compares it with the latest stored
epoch.

Pillar performance for the last 30 days is calculated as a weighted
produced / expected ratio from counter changes between snapshots within the
same epoch. Epoch resets and counter decreases are excluded, and one snapshot
before the period is used as a baseline when available. The dashboard also
returns 30 daily points using those same valid intervals. A day without a
compatible interval is shown as no data, not as 0%. A pillar shows no aggregate
percentage until enough compatible snapshots exist to calculate an interval.

## Start the dashboard

Run:

```
python web_app.py --port 8090
```

Open [http://127.0.0.1:8090](http://127.0.0.1:8090) in a browser. The default
port is `8080`; use `--port` when that port is blocked or already reserved on
your system.

The dashboard shows:

- the current epoch and its progress;
- the latest status of every known pillar;
- live duration based on the latest continuous status transition;
- epoch history;
- status and availability events;
- node health and recent poll runs;
- a responsive mobile-first layout.

Available API endpoints:

- `GET /api/overview`
- `GET /api/pillars`
- `GET /api/epochs`
- `GET /api/events`
- `GET /api/health`
- `GET /api/auth/me`
- `GET /api/subscriptions` (authenticated user's records)
- `/api/admin/*` (administrator only; settings, users, subscriptions, logs)

The web server binds to localhost by default. The single portal uses hashed
passwords, expiring sessions, CSRF protection, role checks, and audit records.
The server enforces the permissions independently of what the browser shows.
If exposed publicly, still use HTTPS and a reverse proxy.

## SQLite tables

The main tables are:

- `epochs` — one record per observed epoch.
- `epochs.epoch_start_at` — observed on-chain transition time when available, or an optional historical estimate; `epoch_start_inferred` identifies estimates; `announcement_at` records the first successful Telegram announcement time when live notifications are enabled.
- `pillars` — the latest known identity and metadata for each pillar.
- `pillar_snapshots` — the pillar state observed during an epoch.
- `events` — status changes, availability changes, additions, removals, and poll errors.
- `node_state` — the latest node health and synchronization information.
- `poll_runs` — the result and timing of each collector run.
- `notifications` — an outbox and delivery history for notifications.
- `users` and `sessions` — accounts, roles, password hashes, and expiring web sessions.
- `app_settings` — runtime configuration edited by administrators.
- `pillar_subscriptions` — assigned Telegram subscriptions with active flags; records are never deleted.
- `audit_log` — administrator and account actions.
- `schema_meta` — schema version metadata.

## Telegram

Telegram remains optional. The collector writes notification records to the SQLite outbox and attempts delivery through the configured Telegram wrapper. A failed notification must not invalidate a successful data collection.

See [SETUP.md](SETUP.md) for BotFather setup, channel permissions, chat IDs,
test messages, pinned-message configuration, and troubleshooting. For
production use, run the collector as a supervised service and review the
outbox regularly.

### Per-pillar Telegram channels

The configured `telegram_channel_id` remains the global channel and continues
to receive all notifications. Additional pillar subscriptions are created and
assigned in `/portal`. Each record has an active flag; deactivation is the
supported alternative to deletion.

An epoch-only subscription can be created by leaving the pillar address list
empty and selecting only `epoch_available`. The bot must be an administrator
with permission to post in each additional channel.

Use `"all"` to include all supported events, including epoch notifications.
The bot must be an administrator with permission to post in each additional
channel.

### Import public Telegram history

The optional history importer reads the public preview pages of the old
notification channels. It does not need the bot token and imports only epoch
availability and pillar active/inactive messages. The source channel, message
ID, original text, and source URL are stored in each imported event.

Always preview the import first:

```
python tools/telegram/import_history.py --fill-missing-epochs --dry-run
```

To write the records after reviewing the preview:

```
python tools/telegram/import_history.py --fill-missing-epochs --apply
```

The `--fill-missing-epochs` option adds epoch numbers that are missing between
two known announcements. Their dates are interpolated from the surrounding
announcements and their time is set to `13:30 UTC` by default. These records
are marked as inferred in `events.details`. Omit the option to import only
messages that actually exist in the channels. The importer is idempotent and
does not update the current pillar table, snapshots, rewards, or notification
outbox.

The current public sources are [Pillar Tracker](https://t.me/pillar_tracker)
and [ATS Pillar Tracker](https://t.me/ATSocyPT). Telegram preview availability
and channel history can change independently of this application, so keep the
import command and its output as part of the audit trail.

## Development checks

Run the unit tests:

```
python -m unittest discover -s tests -v
```

Compile the Python modules:

```
python -m compileall -q controllers models services functions utils tools tests pillar_tracker.py collector.py web_app.py
```

The dashboard is static HTML/CSS/JavaScript and is served by `web_app.py`.
