# Zenon Pillar Tracker — Setup Guide

This guide describes the commands currently used to configure, initialize, run, and stop the Zenon Pillar Tracker on Windows PowerShell.

Run all commands from the repository root, the directory containing
`pillar_tracker.py`.

Replace `python` with `.\.venv\Scripts\python.exe` if the virtual environment is not activated.

## 1. Create and activate the virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell blocks script activation, run the commands with the virtual-environment interpreter directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 2. Create the secret file

Runtime settings are stored in SQLite; no JSON configuration file is read by
the collector or web server. Copy the optional `.env` template when Telegram
is enabled:

```powershell
Copy-Item .\.env.example .\.env
```

Keep the Telegram bot token only in `.env`:

```env
TELEGRAM_BOT_API_KEY=
```

The `.env` file is ignored by Git. Do not commit the token, put it in a web
form, or include it in screenshots or logs.

### Configure node failover

The first URL is the primary. Following URLs are tried in order when the
primary is unavailable or not ready. A single-item list is sufficient when you
do not need failover yet. Add backup endpoints after the primary when failover
is required.

The collector uses a single endpoint for the complete snapshot. It first asks
for the frontier momentum and, when available, `stats.syncInfo`. A node is
rejected when it is unreachable, remains in a synchronization state other than
`2` (synced), is behind its target height, has a frontier older than the
configured limit, returns no pillars, or fails while the snapshot is being
collected. A node that briefly reports `state = 1` (syncing) receives a short
grace period before the poll is deferred. This prevents momentum, pillar, and
epoch data from being combined from different nodes.

`node_require_sync_info` defaults to `false` so older nodes that do not expose
`stats.syncInfo` can still be used. Set it to `true` to require that check.
`node_max_frontier_age_seconds` defaults to five minutes; set it to `0` to
disable the timestamp-age check. `node_failure_cooldown_seconds` defaults to
two minutes. A failed endpoint is skipped during that cooldown, and the
primary is tested again after it becomes eligible. `node_sync_retry_seconds`
defaults to 30 seconds, with a 5-second retry interval. With multiple endpoints,
the collector tries the other candidates immediately and only waits when no
other candidate is available.

`reference_reward_address` must be a valid pillar owner address with reward
history. It is used to determine the latest epoch and to import epoch reward
history. The reward amounts remain available in SQLite, but are not displayed
on the dashboard.

For a live epoch transition, the collector stores the timestamp of the first
observed momentum carrying the new epoch as `epoch_start_at`. This is the best
available on-chain timestamp and is kept separate from the Telegram send time.
If the transition is not observed during live collection, the collector does
not invent a new start time. The historical import and backfill tools can use
an optional schedule fallback and mark the resulting value `Estimated`.

`rate_limit_max_wait_seconds` caps the wait used when a node or Telegram API
returns HTTP 429. `telegram_rate_limit_retries` controls additional attempts.
These settings are editable in the admin panel.

## 3. Initialize the SQLite database

Run this once:

```powershell
python .\tools\setup_database.py
```

Or, without activating the virtual environment:

```powershell
.\.venv\Scripts\python.exe .\tools\setup_database.py
```

The database is created at:

```text
data_store\pillar_tracker.sqlite3
```

The script is idempotent: running it again keeps existing data and applies the current schema.

### Is the existing data safe?

Yes. The setup script is designed to be idempotent:

- it creates missing tables with `CREATE TABLE IF NOT EXISTS`;
- it creates missing indexes with `CREATE INDEX IF NOT EXISTS`;
- it does not drop tables;
- it does not delete rows;
- it does not overwrite existing snapshots or events;
- it keeps the existing SQLite file and data when run again.

The collector and dashboard also initialize the database safely when they open it. The setup command only creates/verifies the schema; it does not contact the Zenon node and does not collect new data.

Do not manually delete `data_store\pillar_tracker.sqlite3` unless you intentionally want to start with an empty database.

Create the first administrator account in the browser by opening
`http://127.0.0.1:8090/portal`. If the database contains no users, this is
automatically redirected to the one-time setup page at `/setup`. The form
creates the administrator and signs it in automatically.

For a headless installation, use the command-line fallback:

```powershell
python .\tools\create_admin.py
```

There is one login and one role-aware portal. Administrators see the settings,
users, all subscriptions, logs, and audit trail; normal users see and manage
only their own subscriptions. The setup endpoint is disabled as soon as the
first account exists.

## 4. Run one collector poll

Use this to perform one node check and then stop:

```powershell
python .\pillar_tracker.py
```

Expected successful output includes the selected endpoint and current values:

```text
Collected height <height>, epoch <epoch>, <pillar-count> pillars via <configured-endpoint>
```

The collector retrieves:

1. the latest frontier momentum;
2. the node synchronization state when the endpoint supports it;
3. all pillar pages from the selected endpoint;
4. the latest reward epoch for the configured reference pillar;
5. the full reward history when required.

It stores the results in SQLite, including pillar snapshots, epoch records, status changes, poll results, node health, and events.

## 5. Run the collector continuously

Start the polling loop:

```powershell
python .\pillar_tracker.py --loop
```

The default interval is stored as the `poll_interval_seconds` setting in
SQLite. It can be changed by an administrator; restart the collector after
changing collector settings.

You can override it for a run:

```powershell
python .\pillar_tracker.py --loop --interval 60
```

Stop the collector with `Ctrl+C`.

The loop only records a new observation after a successful node check with a newer momentum height. If the node height has not advanced, the run is recorded as stale and no new pillar snapshot is created.

## 6. Start the dashboard

Start the web service in a second PowerShell window:

```powershell
python .\web_app.py --host 127.0.0.1 --port 8090
```

Without an activated virtual environment:

```powershell
.\.venv\Scripts\python.exe .\web_app.py --host 127.0.0.1 --port 8090
```

Open:

```text
http://127.0.0.1:8090
```

The dashboard reads SQLite through the local API. It does not poll the Zenon node itself. Keep the collector running in another terminal to keep the dashboard current.

The dashboard refreshes its API data every 30 seconds.

Favicon and web app manifest files are kept in `static/icons/` and are served
by the dashboard under `/static/icons/`. General dashboard images remain in
`static/images/`.

To rebuild all favicon sizes after replacing the source icon, run:

```powershell
python .\tools\build_favicons.py
```

The default source is `static/icons/favicon-512.png`; the builder creates the
16px, 32px, 64px, 180px, 192px, and 512px PNG variants plus `favicon.ico`.

The dashboard API allows 120 requests per 60 seconds per client by default.
When the limit is reached it returns HTTP 429 with a `Retry-After` header. You
can change the limit when starting the service:

```powershell
python .\web_app.py --host 127.0.0.1 --port 8090 --api-rate-limit 120 --api-rate-window 60
```

Use `--api-rate-limit 0` only when the dashboard is strictly local and the
limit must be disabled. The portal uses one built-in login, expiring sessions,
CSRF protection, and role checks. If the dashboard is
exposed publicly, use HTTPS and a reverse proxy as well.

Stop the dashboard with `Ctrl+C`.

## 7. Logging

The collector and dashboard always write to the fixed path
`data_store/pillar_tracker.log`. The path is not a runtime setting and cannot
be changed from the portal. The file is rotated automatically using these
SQLite settings:

- `log_max_bytes` — maximum size of one log file (default 5 MiB);
- `log_backup_count` — number of rotated files (default 5);
- `log_level` — `DEBUG`, `INFO`, `WARNING`, or `ERROR`.

The application creates the `data_store` directory and the log file with the
process umask, normally resulting in a directory mode around `750` and a file
mode around `640`. On Linux, run the service as the account that owns the
application data directory, or grant that service account write permission.
If the fixed data directory is not writable, the process continues with stderr
logging and records the reason. On Linux, make sure the mounted data directory
is owned by the container user (`10001:10001`). The latest file tail and
database audit trail are available in the administrator section of `/portal`.

Normal successful `GET` and `HEAD` requests are intentionally not written to
the application log, including static files. Successful writes such as login
and subscription changes, plus all `4xx` and `5xx` responses, remain visible.

## 8. Users and roles

`tools/create_admin.py` creates the first administrator. Administrators can
create, deactivate, or update users, change runtime settings, and manage all
subscriptions from `/portal`. Normal users use that same portal and can only
view, edit, activate, and deactivate subscriptions assigned to themselves.
There is no delete endpoint for users or subscriptions, and every
account/admin change is written to `audit_log`.

## 9. Recommended startup sequence

Open two PowerShell windows.

In the first window:

```powershell
.\.venv\Scripts\python.exe .\pillar_tracker.py --loop
```

In the second window:

```powershell
.\.venv\Scripts\python.exe .\web_app.py --host 127.0.0.1 --port 8090
```

Then open `http://127.0.0.1:8090`.

The database setup is normally only needed once:

```powershell
.\.venv\Scripts\python.exe .\tools\setup_database.py
```

## 10. How pillar status is updated

Pillar status is calculated from the on-chain `producedMomentums` and `expectedMomentums` values returned by the node.

- A first observation starts as `active`.
- When produced momentums increase, the pillar remains active and its missed counter resets.
- When expected momentums increase but produced momentums do not, the missed counter increases.
- After `missed_momentums_threshold` qualifying checks, the pillar becomes `inactive`.
- With the default threshold of `5` and a 60-second loop, this can take approximately five qualifying checks.
- A new epoch resets the counter so an epoch rollover does not create a false inactive status.
- When produced momentums increase again, the pillar becomes active again.

The dashboard only shows the latest status stored by the collector. If the collector is stopped, the dashboard cannot update the status.

## 11. Import historical Telegram notifications

The repository includes a read-only-by-default importer for the public
Telegram preview pages of the old [Pillar Tracker](https://t.me/pillar_tracker)
channel and the [ATS Pillar Tracker](https://t.me/ATSocyPT) backup channel.
It does not use the bot token. It imports epoch announcements and pillar
active/inactive notifications, including their UTC timestamps and source
message IDs.

Preview the complete import first:

```powershell
python .\tools\telegram\import_history.py --fill-missing-epochs --dry-run
```

Without an activated virtual environment:

```powershell
.\.venv\Scripts\python.exe .\tools\telegram\import_history.py --fill-missing-epochs --dry-run
```

After reviewing the counts, write the records to the existing database:

```powershell
python .\tools\telegram\import_history.py --fill-missing-epochs --apply
```

The importer is idempotent. Running it again keeps existing rows and does not
send old notifications. It does not change the current `pillars` table or
pillar snapshots. Existing on-chain rewards and momentum heights are kept.
Historical schedule-based epoch starts are stored in `epochs.epoch_start_at`
and marked by `epoch_start_inferred = 1`. Known live transitions are then
replaced with their observed on-chain momentum timestamps and marked with
`epoch_start_inferred = 0`. The first successful Telegram message time is
stored separately as `epochs.announcement_at`. If Telegram delivery fails, the
notification remains pending and `announcement_at` stays empty until a retry
succeeds.

`--fill-missing-epochs` fills only gaps between two known epoch announcements.
The date is interpolated from the surrounding announcements and the missing
time defaults to `13:30 UTC` (`3:30 PM CEST` during summer). The rows and
events are marked as inferred. Omit this option if only messages that actually
exist in Telegram should be imported.

The schedule fallback settings belong to this historical tooling, not to the
runtime database settings. The Telegram importer accepts
`--epoch-start-reference-epoch`, `--epoch-start-reference-at`, and
`--epoch-duration-seconds`. The standalone backfill tool accepts the equivalent
`--reference-epoch`, `--reference-start-at`, and `--duration-seconds` options.
They are only needed when you want to create or recalculate estimated starts.

### Backfill epoch start times

After upgrading an existing database, run this once to populate epoch start
times for all stored epochs:

```powershell
python .\tools\backfill_epoch_starts.py
```

The command first fills schedule-based times and marks them as estimates. It
then applies the first on-chain momentum timestamp for epochs whose live
transition was recorded and marks those values as observed. It does not change
announcement timestamps, pillar history, rewards, or snapshots.

If a collector version from before live announcement timestamps were added was
running, recover timestamps for already-sent Telegram epoch messages with:

```powershell
python .\tools\backfill_epoch_announcements.py
```

This reads only successful Telegram notifications from the outbox and does not
alter epoch start times or other historical data.

## 12. Check the API directly

Check the current overview:

```powershell
Invoke-RestMethod http://127.0.0.1:8090/api/overview | ConvertTo-Json -Depth 6
```

Check the pillar list:

```powershell
Invoke-RestMethod http://127.0.0.1:8090/api/pillars | ConvertTo-Json -Depth 6
```

## 13. Back up the database

Stop both the collector and dashboard before copying the database so SQLite WAL data is fully closed.

```powershell
$backupDirectory = ".\data_store\backups"
New-Item -ItemType Directory -Force $backupDirectory | Out-Null
$backupName = "pillar_tracker-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".sqlite3"
Copy-Item .\data_store\pillar_tracker.sqlite3 (Join-Path $backupDirectory $backupName)
```

Keep the `-wal` and `-shm` files together with the database while the application is running. Stopping both processes before a file copy avoids copying an incomplete live database state.

## 14. Configure Telegram notifications

The collector already supports Telegram through the official HTTPS Bot API. It sends event messages with `sendMessage` and can update an optional pinned overview with `editMessageText`. See the [official Telegram Bot API documentation](https://core.telegram.org/bots/api).

### Create the bot

1. Open Telegram and start a chat with `BotFather`.
2. Run `/newbot`.
3. Choose a display name and a unique username ending in `bot`.
4. Copy the token returned by BotFather.
5. Keep the token private. Do not commit it or post it in a chat.

### Add the bot to the channel

The visible channel name is only the title shown to users. It is not the value used by the Bot API.

In Telegram Web or Telegram Desktop:

1. Open the channel.
2. Click the channel title at the top to open **Channel Info**.
3. Open **Edit**, **Manage Channel**, or the three-dot menu, depending on the client version.
4. Open **Administrators**.
5. Select **Add Administrator**.
6. Search for the bot username, which ends in `bot`.
7. Select the bot and confirm.
8. Enable **Post Messages**.
9. Also enable **Edit Messages** if the pinned overview should be updated automatically.

Use the bot username, not the API token. If you do not know the username, retrieve it safely with:

~~~powershell
$botToken = Read-Host "Telegram bot token"
Invoke-RestMethod -Uri "https://api.telegram.org/bot$botToken/getMe" | ConvertTo-Json -Depth 5
~~~

Use the value in `result.username` when searching for the bot.

If Telegram Web shows **No results** when searching under **Administrators**:

1. Open the bot directly at `https://t.me/ZenonPillarTrackerBot`.
2. Press **Start** in the bot chat.
3. Open the bot profile and choose **Add to Group or Channel** if that option is available.
4. Select your channel and grant administrator rights.
5. Alternatively, return to **Administrators** and search for `ZenonPillarTrackerBot` without the `@` symbol.

If the bot still cannot be found, use `getMe` above to verify that the API token actually belongs to `ZenonPillarTrackerBot`. You must also be the channel owner or an administrator who is allowed to add administrators.

For event notifications it needs permission to post messages. For the pinned overview it also needs permission to edit messages in the channel. The Telegram API documents these channel administrator permissions as `can_post_messages` and `can_edit_messages`.

### Configure the channel

A public channel can use its username:

```json
"telegram_channel_id": "@my_pillar_channel"
```

A private channel normally uses its numeric chat ID, which looks like:

```json
"telegram_channel_id": "-1001234567890"
```

The Bot API accepts either a numeric chat ID or a channel username in the `@username` format.

The number in a link such as `https://web.telegram.org/k/#-4343093545` is a Telegram Web peer reference. Do not paste the full URL into the configuration, and do not assume that this number is the Bot API chat ID. For a private channel, obtain the real `-100...` chat ID from `getUpdates` as described below.

Create or update `.env`:

```env
TELEGRAM_BOT_API_KEY=
```

Update the non-secret Telegram settings in the administrator section of
`/portal`.
The pinned message ID and global channel are normal SQLite settings; extra
pillar subscriptions are managed in the Subscriptions section.

The `telegram_pinned_message_id` should stay empty until a real message ID has been obtained. The example value `1` is only a placeholder. If it is incorrect, the collector will log a pinned-message update error on every successful poll, although normal event notifications can still work.

### Test the bot before running the collector

The repository includes two optional Python maintenance tools in
`tools/telegram/`. They read the bot token only from `.env` and the channel ID
from SQLite, so the token does not need to be placed on the command line.

Send a test message:

```powershell
python .\tools\telegram\send_test_message.py
```

Without an activated virtual environment:

```powershell
.\.venv\Scripts\python.exe .\tools\telegram\send_test_message.py
```

The script prints the channel ID and the new `Message ID`. Use that message ID
for `telegram_pinned_message_id` if this message will become the editable
overview. You can provide a custom test message with `--message` or override
the configured channel with `--chat-id`.

The equivalent PowerShell command is:

```powershell
$botToken = Read-Host "Telegram bot token"
$channelId = Read-Host "Telegram channel ID (@username or -100...)"
$body = @{
    chat_id = $channelId
    text = "Zenon Pillar Tracker test message"
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$botToken/sendMessage" -ContentType "application/json" -Body $body
```

A successful response contains `ok: true` and a `result.message_id`. If the response is successful, copy that `message_id` if you want the bot to maintain a pinned overview.

### Find a private channel ID

For a private channel:

1. Add the bot as an administrator.
2. Post a test message in the channel.
3. Request pending updates with the Python maintenance tool:

```powershell
python .\tools\telegram\get_updates.py
```

Without an activated virtual environment:

```powershell
.\.venv\Scripts\python.exe .\tools\telegram\get_updates.py
```

The tool prints every pending `channel_post`, including its channel ID and
message ID. Use `--json` if the complete Telegram response is needed.

The equivalent PowerShell command is:

```powershell
$updates = Invoke-RestMethod -Uri "https://api.telegram.org/bot$botToken/getUpdates"
$updates.result | ConvertTo-Json -Depth 10
```

Look for `channel_post.chat.id`. Use that negative numeric value as `telegram_channel_id`.

`getUpdates` and webhooks are mutually exclusive. If `getUpdates` returns no updates while a webhook is configured, inspect:

```powershell
Invoke-RestMethod -Uri "https://api.telegram.org/bot$botToken/getWebhookInfo"
```

### Configure the pinned overview

The collector creates a summary containing the top pillars, reward-share percentages, weight, produced/expected momentums, and inactive warnings.

To enable it:

1. Send a test message through the bot.
2. Pin that message in the channel.
3. Copy the returned `result.message_id`.
4. Set that integer in `telegram_pinned_message_id`.
5. Restart the collector.

The bot must be allowed to edit the message. Event notifications and pinned-message updates are separate operations.

### Add notifications for specific pillars

The `telegram_channel_id` remains the global channel and continues to receive
all notifications. The optional `discord_channel_webhook` is the global
Discord destination. Add extra subscriptions for specific pillars through the
Subscriptions section in `/portal`. Each subscription can contain a Telegram
channel ID, a Discord webhook, or both; normal users can manage their assigned
records in the same portal.

Use the pillar owner address shown on the dashboard. A subscription can list
multiple owner addresses and multiple subscriptions can use different
destinations. Add `epoch_available` to send epoch notifications to those
destinations; epoch events are network-wide and are not filtered by the owner
addresses. For an epoch-only subscription, leave the pillar address list empty
and select only `epoch_available`. Subscription records can be deactivated but
are never deleted.

If `events` is omitted, the tracker sends inactive, active, and reward-share
changes. Use `"all"` for all supported events, including epoch, name-change,
pillar-creation, and dismantling events.

Add the same bot as an administrator to every additional Telegram channel and
enable **Post Messages**. Discord subscriptions use standard HTTPS Discord
webhook URLs. The collector sends through one bot and one process; it does not
require a separate tracker instance per pillar. Restart the collector after
changing this configuration.

### Run and verify

Restart the collector after changing settings:

```powershell
python .\pillar_tracker.py
```

The first successful run is treated as a bootstrap and intentionally does not send historical notifications. Later epoch changes, pillar additions/removals, name changes, reward-share changes, and active/inactive transitions create Telegram notifications.

Notifications are stored in the SQLite `notifications` outbox before delivery. Failed messages are retried by later collector runs, up to eight attempts. A failed Telegram notification does not roll back the collected node data.

Useful output includes:

```text
Notification 12 failed: Telegram returned HTTP 403
Could not update Telegram pinned message: ...
```

Common errors:

- `401 Unauthorized` — the bot token is invalid or revoked.
- `400 Bad Request: chat not found` — the channel ID or username is wrong.
- `403 Forbidden` — the bot is not an administrator or lacks the required permission.
- pinned-message edit errors — the message ID is wrong, the message was deleted, or the bot cannot edit it.

## 15. Common problems

### HTTP 400 from the node

If the node returns:

```text
The plain HTTP request was sent to HTTPS port
```

use an HTTPS URL:

```json
"node_rpc_urls": ["https://127.0.0.1:35997"]
```

### Node failover did not occur

Confirm that the backup URL is a real JSON-RPC endpoint and that it is listed
after the primary in `node_rpc_urls`. The collector only switches after a
candidate fails its frontier, synchronization, or complete snapshot check.
Look for `Node RPC candidate failed` and `Node RPC failover` in the collector
output. The primary is retried after `node_failure_cooldown_seconds`.

### Dashboard port error

If port 8080 or another port is blocked, choose a different local port:

```powershell
python .\web_app.py --port 8090
```

### Empty dashboard

The web service does not collect data. Start the collector separately:

```powershell
python .\pillar_tracker.py
```

Then keep it running with:

```powershell
python .\pillar_tracker.py --loop
```

### Missing reward address

If the collector reports `reference_reward_address is empty`, set a valid
`z1...` pillar address in the administrator Settings section of `/portal`.

### Stopping everything

Press `Ctrl+C` in the collector and dashboard terminals. No database data is deleted when either process stops.
