import json
from datetime import timedelta

import frappe
from frappe.utils import get_datetime, now_datetime

from .controllers import configuration_hash
from .drivers import get_driver
from .engine import ingest_batch, inspect_event, process_pending
from .normalization import normalize_time


def device_config(device):
    return dict(
        host=device.host,
        port=device.port,
        transport=device.transport,
        timeout=device.timeout,
        omit_ping=device.omit_ping,
        communication_key=device.get_password("communication_key", raise_exception=False) or "0",
    )


def filter_events(device, records, start=None, end=None):
    from frappe.utils import get_system_timezone

    lower = (
        max(get_datetime(device.sync_start), get_datetime(start))
        if start
        else get_datetime(device.sync_start)
    )
    upper = get_datetime(end) if end else now_datetime()
    result = []
    for event in records:
        try:
            stamp = normalize_time(event["timestamp"], device.device_timezone, get_system_timezone())
        except ValueError:
            # Preserve malformed/DST-ambiguous events for review during live ingestion.
            result.append(event)
            continue
        if lower <= stamp <= upper:
            result.append(event)
    return result


def preview_events(device, events):
    counts, sample = {}, []
    for event in events:
        try:
            result = inspect_event(device, event)
        except Exception as exc:
            result = {"status": "Invalid", "reason": type(exc).__name__}
        status = result["status"]
        counts[status] = counts.get(status, 0) + 1
        if len(sample) < 30:
            sample.append(dict(user_id=event.get("user_id"), timestamp=event.get("timestamp"), **result))
    return {
        "counts": counts,
        "sample": sample,
        "notice": "Preview creates no raw logs or checkins. HRMS validations also run during live creation.",
    }


def run_direct(run_name):
    run = frappe.get_doc("Biometric Sync Run", run_name)
    device = frappe.get_doc("Biometric Device", run.device)
    with frappe.cache.lock(f"zkt-direct-{device.name}", timeout=1800, blocking_timeout=1):
        if run.status != "Queued":
            return
        run.status, run.started_at = "Running", now_datetime()
        run.save(ignore_permissions=True)
        frappe.db.set_value(
            "Biometric Device", device.name, {"last_poll": now_datetime(), "last_status": "Running"}
        )
        frappe.db.commit()
        try:
            if not device.enabled or device.connection_mode != "Direct":
                raise ValueError("Enable this device in Direct mode before connecting")
            if run.mode == "Live" and not device.live_sync_enabled:
                raise ValueError("Live synchronization is disabled")
            with get_driver(device.driver, device_config(device)) as driver:
                if run.operation in ("Device Info", "Test Connection"):
                    info = driver.get_device_info()
                    frappe.db.set_value("Biometric Device", device.name, "device_info", json.dumps(info))
                    run.preview = json.dumps(info)
                elif run.operation == "Fetch Users":
                    users = driver.get_users()
                    if len(users) > 10000:
                        raise ValueError("User discovery exceeds 10,000 users; use smaller device groups")
                    frappe.db.set_value(
                        "Biometric Device", device.name, "discovered_users", json.dumps(users)
                    )
                    run.records_retrieved = len(users)
                else:
                    records = filter_events(
                        device, driver.get_attendance_records(), run.range_start, run.range_end
                    )
                    run.records_retrieved = len(records)
                    if run.mode == "Dry Run":
                        run.preview = json.dumps(preview_events(device, records))
                        frappe.db.set_value(
                            "Biometric Device",
                            device.name,
                            {
                                "previewed_at": now_datetime(),
                                "preview_config_hash": configuration_hash(device),
                            },
                        )
                    else:
                        for offset in range(0, len(records), 200):
                            current = frappe.get_doc("Biometric Device", device.name)
                            if not current.enabled or not current.live_sync_enabled:
                                raise ValueError(
                                    "Device synchronization was disabled; retained events remain safe"
                                )
                            acks = ingest_batch(device, records[offset : offset + 200], run.name)
                            run.events_received += sum(a["accepted"] for a in acks)
                            run.failed_count += sum(not a["accepted"] for a in acks)
                            frappe.db.set_value(
                                "Biometric Sync Run",
                                run.name,
                                {
                                    "records_retrieved": run.records_retrieved,
                                    "events_received": run.events_received,
                                    "failed_count": run.failed_count,
                                },
                                update_modified=False,
                            )
                            frappe.db.commit()
                        process_pending(device.name, limit=1000)
                        refresh_counters(run)
            run.status = "Partial" if run.failed_count or run.unmapped_count else "Success"
            frappe.db.set_value(
                "Biometric Device",
                device.name,
                {
                    "last_seen": now_datetime(),
                    "last_sync": now_datetime() if run.operation == "Sync" else device.last_sync,
                    "last_status": run.status,
                    "last_error": "",
                    "consecutive_errors": 0,
                    "last_record_count": run.records_retrieved,
                },
            )
        except Exception as exc:
            frappe.db.rollback()
            run.status = "Failed"
            from .drivers import DeviceError

            run.error_summary = (
                str(exc)[:500]
                if isinstance(exc, (DeviceError, ValueError))
                else f"{type(exc).__name__}: operation failed"
            )
            frappe.db.set_value(
                "Biometric Device",
                device.name,
                {
                    "last_status": "Failed",
                    "last_error": run.error_summary,
                    "consecutive_errors": (device.consecutive_errors or 0) + 1,
                },
            )
        run.ended_at = now_datetime()
        run.duration = (run.ended_at - get_datetime(run.started_at)).total_seconds()
        run.save(ignore_permissions=True)
        frappe.db.commit()


def refresh_counters(run):
    from frappe.query_builder.functions import Count

    table = frappe.qb.DocType("Biometric Raw Log")
    groups = (
        frappe.qb.from_(table)
        .select(table.status, Count(table.name).as_("total"))
        .where(table.sync_run == run.name)
        .groupby(table.status)
        .run(as_dict=True)
    )
    counts = {r.status: r.total for r in groups}
    run.created_count = counts.get("Created", 0)
    run.duplicate_count = counts.get("Duplicate", 0)
    run.unmapped_count = counts.get("Unmapped", 0)
    run.skipped_count = counts.get("Skipped", 0)
    run.failed_count = sum(counts.get(s, 0) for s in ("Failed", "Invalid", "Requires Review"))


def queue_run(device, operation="Sync", dry_run=True, start=None, end=None, trigger="Manual"):
    run = frappe.get_doc(
        {
            "doctype": "Biometric Sync Run",
            "device": device.name,
            "agent": device.agent,
            "operation": operation,
            "mode": "Dry Run" if dry_run else "Live",
            "trigger_type": trigger,
            "range_start": start,
            "range_end": end,
            "initiated_by": frappe.session.user,
            "status": "Queued",
        }
    ).insert(ignore_permissions=True)
    if device.connection_mode == "Direct":
        frappe.enqueue(
            "zkt_k50_biomatrics.jobs.run_direct",
            run_name=run.name,
            queue="long",
            timeout=1800,
            enqueue_after_commit=True,
            job_id=f"zkt-run-{run.name}",
            deduplicate=True,
        )
    return run.name


def schedule():
    now = now_datetime()
    for item in frappe.get_all(
        "Biometric Sync Run",
        filters={"status": ["in", ["Queued", "Running"]]},
        fields=["name", "device", "status", "started_at", "creation"],
        limit_page_length=100,
    ):
        device = frappe.get_doc("Biometric Device", item.device)
        if device.connection_mode != "Direct" or not device.enabled:
            continue
        if item.status == "Queued" and get_datetime(item.creation) < now - timedelta(minutes=2):
            frappe.enqueue(
                "zkt_k50_biomatrics.jobs.run_direct",
                run_name=item.name,
                queue="long",
                timeout=1800,
                job_id=f"zkt-run-{item.name}",
                deduplicate=True,
            )
        elif (
            item.status == "Running"
            and item.started_at
            and get_datetime(item.started_at) < now - timedelta(minutes=35)
        ):
            frappe.db.set_value(
                "Biometric Sync Run",
                item.name,
                {
                    "status": "Failed",
                    "ended_at": now,
                    "error_summary": "Worker interrupted or timed out. Durable events remain available for processing.",
                },
            )
    # No connection attempts for disabled devices or edge-agent devices.
    for row in frappe.get_all(
        "Biometric Device",
        filters={"enabled": 1, "live_sync_enabled": 1, "connection_mode": "Direct"},
        fields=["name", "last_poll", "polling_interval"],
    ):
        if row.last_poll and now - get_datetime(row.last_poll) < timedelta(seconds=row.polling_interval):
            continue
        with frappe.cache.lock(f"zkt-schedule-{row.name}", timeout=30, blocking_timeout=1):
            if not frappe.db.exists(
                "Biometric Sync Run", {"device": row.name, "status": ["in", ["Queued", "Running"]]}
            ):
                queue_run(frappe.get_doc("Biometric Device", row.name), dry_run=False, trigger="Scheduled")
    frappe.enqueue("zkt_k50_biomatrics.engine.process_pending", job_id="zkt-pending", deduplicate=True)
    # Counters follow eventual background processing without claiming receipt means checkin creation.
    for name in frappe.get_all(
        "Biometric Sync Run",
        filters={
            "mode": "Live",
            "operation": "Sync",
            "status": ["in", ["Success", "Partial"]],
            "creation": [">=", now - timedelta(days=2)],
        },
        pluck="name",
        limit_page_length=100,
    ):
        run = frappe.get_doc("Biometric Sync Run", name)
        refresh_counters(run)
        if run.failed_count or run.unmapped_count:
            run.status = "Partial"
        run.save(ignore_permissions=True)
