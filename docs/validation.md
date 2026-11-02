# Validation record — 3 November 2026

## Environment and scope

Source inspected: Frappe 16.29.0, ERPNext 16.30.0, HRMS 16.15.0, Python 3.14.4.
Development source: `/home/frappe/frappe-bench/apps/zkt_k50_biomatrics`.
Test site: `zkt-k50-test.local`, isolated SQLite database, scheduler disabled.
Existing sites were not installed, migrated, configured, or synchronized.
Bench-level changes: editable package/dependencies installed, app registered in
`sites/apps.txt`, standard app asset build performed. No existing app source was edited.
Existing prototype scripts, service configuration and credentials were not changed.

## Executed checks

- App installation on the isolated Frappe/ERPNext/HRMS test site: passed.
- `bench --site zkt-k50-test.local migrate`: passed.
- Device schema reload after form layout improvements: passed.
- `bench build --app zkt_k50_biomatrics`: passed.
- Frappe test runner: 24 integration tests, 22 passed and 2 explicitly skipped.
- Framework-independent core/agent tests: 28 passed.
- Total automated suite: **50 passed, 2 skipped**.
- Ruff lint and formatting checks: passed.
- Python compilation and JavaScript syntax checks: passed.
- Real WSGI/HTTP smoke: unauthenticated rejection, scoped heartbeat, durable event
  receipt, replay idempotency and credential revocation passed. Used synthetic
  fixtures on the isolated site, no device or cloud credentials.
- Standalone package installed into a fresh virtual environment without Frappe:
  CLI loaded and displayed help successfully.

The suite exercises mocked TCP/UDP, connection failures and redaction, timezone/DST
conversion, per-device event identity, configurable/unknown punches, overlapping
mappings, inactive/unmapped employees, real standard HRMS checkin creation and
duplicates, preserved HRMS rejection, non-destructive previews, direct background
runs, edge command leasing/preview/live receipts, permissions, credential rotation,
revocation, partial acknowledgements, offline outbox restart and network recovery,
raw-history deletion protection, report queries and repeatable install hooks.

## Checks explicitly pending

1. **MariaDB concurrency:** skipped because SQLite does not implement MariaDB row locks.
   A two-worker ingestion test is included and will run on MariaDB.
2. **Real HRMS Shift Location radius workflow:** skipped on SQLite. The installed HRMS
   shift code adds MariaDB `timedelta` values to datetimes; SQLite returns `datetime.time`,
   causing `TypeError` before radius validation. The test creates real Shift Type,
   Shift Assignment and Shift Location documents and will run on MariaDB. A separate
   mocked radius-rejection test confirms the app retains failed raw events correctly.
3. Full installation/migration and all tests on an isolated **MariaDB** v16 site.
4. Physical K50 acceptance through the new driver and operator-configured settings.
5. Deployment-specific large-backlog, multi-agent and concurrent-worker load tests.
6. Browser visual QA and operator acceptance on the chosen deployment site.

The first ERPNext installation on experimental SQLite hit a database-lock error during
its post-install custom-field setup, although its DocTypes had installed. HRMS and this
app then installed, and migration succeeded. Tests create their required synthetic
Company/Gender/Warehouse Type fixtures. This is not evidence of a complete supported
ERPNext production deployment on SQLite. MariaDB is the production acceptance target.

No production-readiness claim is made until the pending environment/hardware checks
are completed. The app is version 0.1.0 with the limitations described in deployment.md.

## Subsequent deployment and permission fix

At the user's explicit request, the app was installed on `ss.frappe.my` after a successful
site database/config backup. The dashboard Page and empty monitoring API were verified.
The running Python services were reloaded to recognize the new package. No devices,
employee mappings or checkins were created on that site, and no live synchronization
was enabled.

A reported navigation failure was traced to an active System Manager account lacking
DocType permissions despite already being allowed by the app's dashboard/management APIs.
Version-controlled permissions now consistently admit System Manager for configuration
and read-only audit access. A regression test confirms configuration access without
write access to raw logs/runs. These permissions were reloaded only for this app's five
DocTypes on `ss.frappe.my`; the active account's device read access was verified.
Dashboard navigation now uses links and includes an Add Device action and setup guidance.
A hard browser refresh is required to refresh the existing session's boot permissions.
