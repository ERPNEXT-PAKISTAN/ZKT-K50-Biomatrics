# Deployment and operator guide

## Requirements and compatibility

Inspected source: Frappe 16.29.0, ERPNext 16.30.0, HRMS 16.15.0. Bench Python 3.14.
The standalone agent requires Linux and Python 3.11 or newer. Shared driver and agent
modules do not import Frappe. pyzk 0.9 implements the first Pull-protocol driver.
Only the K50 prototype is known to have worked in this environment. Other terminals
require a real compatibility test. ISAPI, ADMS and HTTP push are not implemented.
TCP uses `force_udp=False`; pyzk may fall back to UDP. UDP uses `force_udp=True`.
The pyzk option is spelled `ommit_ping`; the app presents it as Omit Ping.

## Bench installation — test site first

The source is `/home/frappe/frappe-bench/apps/zkt_k50_biomatrics`.
The development process registers the app in bench `sites/apps.txt` and installs
its Python package/dependencies, but does not install it on an existing live site.

Use an isolated MariaDB test site with Frappe, ERPNext and HRMS. Supply database
administrator credentials interactively; never put secrets in a command history.
These commands are templates; replace `your-test-site.local` with the actual test site:

```bash
cd /home/frappe/frappe-bench
bench new-site your-test-site.local
bench --site your-test-site.local install-app erpnext
bench --site your-test-site.local install-app hrms
bench --site your-test-site.local install-app zkt_k50_biomatrics
bench --site your-test-site.local migrate
bench build --app zkt_k50_biomatrics
```

Do not use `--set-default` if this bench already serves other sites. Run workers via
this bench's existing process manager. Do not restart a shared production bench
solely for testing. Scheduler activation is explicit:

```bash
bench --site your-test-site.local enable-scheduler
```

No devices are created or enabled on install. Setup creates three roles and lookup
indexes idempotently. No HRMS source changes or Employee custom fields are used.
Assign Biometric Administrator to the setup operator. Managers configure devices,
mappings, manual synchronization and retries. Viewers read logs and reports.
Only administrators issue enrollment codes/revoke agents or execute live historical
imports. Communication keys have field permission level 1; grant this level only to
trusted administrators. Existing secrets are never returned by custom APIs.

## Configure and test the current K50

1. Open **Biometric Attendance → Dashboard → Biometric Device → New**.
2. Enter a unique code, e.g. `WORKPLACE-01`, and title. Choose `pyzk`, model K50.
3. Choose Direct only when this server can route to the terminal through LAN/VPN.
   Otherwise choose Edge Agent and assign an enabled enrolled agent.
4. Enter the tested terminal address and key in protected settings. They are not
   application defaults. Use port 4370, TCP, timeout 15, Omit Ping enabled if that
   matches the successful prototype. In Edge mode also enter the actual address
   and key in the local protected configuration; the server does not distribute keys.
5. Set terminal timezone to `Asia/Karachi` if that is its actual clock timezone.
   Sync Start is in the **ERP site timezone**, and bounds historical ingestion.
6. If coordinates are required, choose Fixed Coordinates and enter the physical
   terminal location. Associate employees with the correct standard Shift Assignment /
   Shift Location. K50 GPS is not assumed; IP location is not used.
7. Set Enabled, leave Live Sync Enabled off, save, then Test Connection and Fetch Users.
   Operations run in background; open the linked run and use Refresh Progress.
8. Reload the device, choose Map Discovered Users and explicitly select Employee
   links. Effective From/To use the site timezone. Never infer mappings by name.
   Existing mappings cannot be overwritten through this dialog. Use standard Frappe
   Data Import on Biometric Employee Mapping for larger CSV batches (no deletes).
9. Test actual punch codes using Preview Attendance. Configure mappings deliberately;
   there is no default assumption that 0 means IN and 1 means OUT. Unknowns require review.
10. Review the run preview, date cutoff, employee mappings and location settings.
    Save Live Sync Enabled, then Start Sync for an operator-controlled first run.
11. Inspect raw logs and linked Employee Checkins, including HRMS shift/geolocation
    failures. Final Attendance is produced by standard HRMS Auto Attendance.
12. Enable the test-site scheduler only after the manual flow succeeds.

Changing important connection/location/time settings requires another successful
preview before saving Live Sync Enabled. Dry runs store a Sync Run and bounded preview
sample; they create no raw logs or checkins. They do not simulate every HRMS validation;
those run during real Employee Checkin insertion.

Add a second/third device by creating a new Device Code and device-specific mappings.
The same user ID on different devices is resolved independently. An edge agent can
serve several assigned devices; add each Device Code under its local `devices` object.
Different agents can serve different workplaces.

## Agent installation (separate workplace Linux computer)

No Frappe bench is required. Copy or clone this repository to `/opt/zkt-k50-agent/src`.
Commands below use a dedicated account and require OS administration privileges:

```bash
sudo useradd --system --home /var/lib/zkt-k50-agent --shell /usr/sbin/nologin zkt-k50-agent
sudo install -d -o zkt-k50-agent -g zkt-k50-agent -m 700 /var/lib/zkt-k50-agent
sudo install -d -o root -g zkt-k50-agent -m 750 /etc/zkt-k50-agent
sudo python3 -m venv /opt/zkt-k50-agent/venv
sudo /opt/zkt-k50-agent/venv/bin/pip install /opt/zkt-k50-agent/src
sudo install -o zkt-k50-agent -g zkt-k50-agent -m 600 \
  /opt/zkt-k50-agent/src/deploy/agent.example.json /etc/zkt-k50-agent/config.json
```

Edit the configuration as an administrator: set HTTPS site URL, actual local host/key,
and each assigned device's exact Device Code. Do not expose port 4370 publicly or
configure router forwarding. Agent traffic is outgoing HTTPS with certificate checking
and no redirects. Device LAN targets are controlled by the local protected config.

In Desk create an Agent, enable it, assign devices, then use Enroll / Rotate Credentials.
The enrollment credential expires after 15 minutes and is single-use. Enter it at the
CLI's hidden prompt (do not pass it on the command line):

```bash
sudo -u zkt-k50-agent /opt/zkt-k50-agent/venv/bin/zkt-k50-agent \
  --config /etc/zkt-k50-agent/config.json enroll
sudo -u zkt-k50-agent /opt/zkt-k50-agent/venv/bin/zkt-k50-agent \
  --config /etc/zkt-k50-agent/config.json test-device --device WORKPLACE-01
sudo -u zkt-k50-agent /opt/zkt-k50-agent/venv/bin/zkt-k50-agent \
  --config /etc/zkt-k50-agent/config.json once
sudo cp /opt/zkt-k50-agent/src/deploy/zkt-k50-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now zkt-k50-agent
sudo journalctl -u zkt-k50-agent -f
```

Agent polls its control plane every 30 seconds and devices at their configured interval
(minimum 60 seconds). Commands are leased for 20 minutes and reissued after an interrupted
agent. Command progress lives in Sync Runs. Periodic edge uploads live in Raw Logs;
they do not create a separate Sync Run for every small batch.

Use `status` instead of `run` to inspect local pending/delivered counts. SQLite uses WAL
and FULL synchronization. Repeated reads are deduplicated locally and on the server.
Only matching per-event acknowledgements mark rows delivered. Invalid/rejected rows stay
queued with bounded exponential backoff (maximum one hour). Delivered fingerprints are
retained to stop old device history being requeued. Monitor disk use and back up the queue.
After a successful first heartbeat the last authorized device configuration is cached for
offline polling across reboot. Revocation is enforced by the server on every request;
offline cached polling can continue until the agent reconnects or is stopped.

## Credentials, rotation and recovery

Only hashes of cloud agent tokens are stored on the server. The active raw token lives
in a mode-600 local file. To rotate, stop the agent, issue a new one-time enrollment code
in Desk, run `enroll`, then restart it. Successful enrollment atomically replaces the
server credential. An interrupted enrollment can be recovered by issuing another code.
Revoke Agent immediately blocks future ingestion; unacknowledged events stay local.
After recovery, enable the agent, clear revoked status, enroll again and resume.
Communication keys use Frappe Password storage; back up the site's encryption key safely.

## Imports, retries, monitoring and errors

Historical Import requires start/end bounds, at most 31 days per run, and defaults to
Dry Run. Preview counts include unmapped, duplicate and review estimates when available.
There is no reliable hardware record count before fetching. Large operations use long
workers, never browser requests. Imports respect device Sync Start.

Use Raw Logs for per-event errors. Fix mappings, employee status, clock, punch mappings
or coordinates and use Retry Failed Events (200 at a time). API also accepts selected
raw-log names. Received/Pending records resume through the scheduler after a crash;
validation failures are retried explicitly to avoid endless bad-record processing.
Disable a device to stop new work; already durable events remain. A network read already
in progress cannot be forcibly cancelled, but further live batches check enable flags.

Dashboard recent-online indicators use a 15-minute window; they are observations, not
continuous reachability guarantees. Eight reports cover health, runs, errors, unmapped,
raw events, duplicates, daily counts and agents. Raw reports cap output at 5,000 rows;
daily summary at 10,000 source events. Narrow date filters or use standard Data Export.
No credentials are included in reports or dashboard responses.

A missing mapping preserves Unmapped. Unknown punch codes or required missing coordinates
require review. Inactive employees and failed HRMS shift/radius validation retain raw events.
Connection errors intentionally expose safe categories rather than raw protocol messages.
Check private routing, VPN, LAN firewall, device key and TCP/UDP. A cloud VPS cannot normally
route to a workplace private IP. No server-side biometric templates are collected.

## Backup, update, rollback and removal

Before installing on an authorized production site, take its normal `bench --site SITE
backup --with-files` backup, preserve encryption keys, and test restore on another site.
Use a maintenance window and the same install/migrate/build sequence proven on MariaDB.
Do not blindly enable the scheduler or restart shared services.

Back up agent SQLite with the SQLite backup API or stop the service before copying its
DB/WAL files. Preserve config and credential with restricted permissions. Upgrade agent
code/venv independently; do not delete its data directory. On server upgrades, back up,
update app source, migrate the chosen site and build assets. Rollback by disabling devices,
stopping agents, restoring compatible app source and restoring a tested site backup when
schema/data rollback is necessary. Do not use Git reset on unrelated bench repositories.

Stop with `systemctl stop zkt-k50-agent`; disable it and revoke credentials before removal.
Disable all devices to retire the integration while preserving attendance. Uninstall is
blocked while biometric history exists because normal Frappe uninstall drops app tables.
Export/archive history and test restoration before any explicit destructive removal.
Employee Checkins belong to HRMS, but removing audit history still needs deliberate review.

## Tests and known limits

```bash
cd /home/frappe/frappe-bench
./env/bin/python -m unittest zkt_k50_biomatrics.tests.test_core -v
./env/bin/ruff check apps/zkt_k50_biomatrics
./env/bin/ruff format --check apps/zkt_k50_biomatrics
bench --site your-test-site.local run-tests --app zkt_k50_biomatrics
```

Routine tests use mocked pyzk and synthetic employees/events. Never use live device or
cloud credentials in tests. See `validation.md` for actual executed checks.

pyzk does not expose a dependable native attendance record ID. Fingerprints therefore
use device + biometric user + second-resolution timestamp + punch code. Two truly identical
physical events at the same second cannot be distinguished and collapse to one raw event.
Different devices and different native IDs (future verified drivers) remain distinct.
HRMS independently prohibits employee/time/log-type duplicates; multiple raw events may
link to one existing checkin. Device timezone conversion is explicit and avoids double
conversion. Ambiguous/nonexistent DST local times require an explicit offset/review.

Polling rereads the device history because K50 does not supply a reliable incremental
cursor. Very large device memories increase LAN work; no records are deleted. High-volume
imports and concurrent MariaDB requests need deployment-specific load testing. Explicit
terminal coordinates are supported; alternate location-source integration is not implemented.
Notification automation and automatic device user enrollment are not included.
