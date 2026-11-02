# Implementation handoff

## Delivered behavior

`ZKT K50 Biomatrics` (`zkt_k50_biomatrics`, version 0.1.0) configures devices in Frappe
Desk and ingests attendance into existing HRMS Employee Checkin documents. It does not
replace Attendance or Auto Attendance. New devices/agents are disabled, and live
processing additionally requires a saved preview for the current configuration.

Five primary DocTypes:

- **Biometric Device:** identity, enabled/live flags, pyzk connection options, agent,
  timezone, polling, import cutoff, workplace, explicit terminal coordinates, punch
  mapping, discovery and synchronization health.
- **Biometric Agent:** enabled/revoked state, one-time enrollment hash/expiry, active
  credential hash, rotation date, heartbeat, hostname and version.
- **Biometric Employee Mapping:** device + biometric ID + Employee with effective
  start/end dates, enabled state, discovered UID/name and notes. Overlaps are rejected
  under a device-row lock.
- **Biometric Raw Log:** unique fingerprint, device/agent/run, original timestamp and
  source timezone, normalized time, punch, mapping/employee, checkin link, processing
  state, bounded attendance-only payload and retry/error details. Deletion is blocked.
- **Biometric Sync Run:** operation, dry/live mode, requested dates, trigger, initiator,
  counters, preview sample, status/timings and edge-command lease.

One child DocType: **Biometric Punch Mapping**, explicitly mapping codes to IN or OUT.
Roles: Biometric Administrator, Biometric Manager, Biometric Viewer.

Desk includes the Biometric Attendance workspace, KPI dashboard, status indicators,
connection/info/user actions, discovered-user bulk mapping, preview/live/historical
controls, progress refresh, retries and links to audit records. Eight reports cover
devices, agents, runs, raw events, errors, unmapped events, duplicates and daily counts.

## Connectivity and reliability

The registered pyzk driver supports configured host/port, communication key, TCP/UDP,
timeout and Omit Ping. It reads information, users and punches, strips enrollment
material and never clears device history. Only verified pyzk-compatible models should
be used; universal ZKTeco support is not claimed.

Direct mode runs on long workers when LAN/VPN routing exists. Edge mode never polls a
private terminal from the cloud. A separate Linux agent polls assigned local terminals
and sends outgoing HTTPS requests. It needs no ERPNext installation. Its durable SQLite
outbox survives offline periods/reboots and only retires key-matching per-event server
acknowledgements. Tokens are scoped to assigned devices, revocable and replaced through
administrator-issued one-use enrollment. Network targets and communication keys remain
in protected local configuration. No router forwarding or public device ports are needed.

The server commits raw events before acknowledging them, enforces unique fingerprints,
serializes app processing with row locks and preserves HRMS document validation. Per-event
errors remain reviewable/retryable. Unknown punches are never guessed. Timestamps use
terminal-to-site timezone conversion, including explicit handling of DST ambiguity.
Received/Pending processing and interrupted direct jobs resume through scheduled work.
See the deployment guide for fingerprint limitations and bounded report output.

## Location, repository and deployment

Application directory: `/home/frappe/frappe-bench/apps/zkt_k50_biomatrics`.
The application has its own Git repository on branch `main`. Its GitHub remote is
`https://github.com/ERPNEXT-PAKISTAN/ZKT-K50-Biomatrics.git`.
No unrelated repositories or prototypes were edited.

Executed checks and pending MariaDB/hardware validation are in [validation.md](validation.md).
The full test-site installation/migration sequence, current K50 acceptance steps, secure
agent setup/systemd commands, second/third-device setup, backup, update, recovery, rollback
and guarded removal are in [deployment.md](deployment.md).

For an already prepared, authorized test site with ERPNext and HRMS:

```bash
cd /home/frappe/frappe-bench
bench --site YOUR_TEST_SITE install-app zkt_k50_biomatrics
bench --site YOUR_TEST_SITE migrate
bench build --app zkt_k50_biomatrics
```

Do not substitute a live site without explicit authorization. No live synchronization
was enabled during development. Synthetic test devices are disabled after validation.

## Remaining acceptance work

MariaDB installation/concurrency/radius tests, physical K50 acceptance, large-volume load
tests and browser/operator review remain. Other protocol drivers and alternative location
sources are extension points, not implemented support. Periodic edge batches use raw audit
logs rather than a separate Sync Run for every batch. No external notifications or automatic
device enrollment are installed. Keep the old prototype stopped when deliberately enabling
this app's live workflow, to avoid two integrations independently sending the same punches.
