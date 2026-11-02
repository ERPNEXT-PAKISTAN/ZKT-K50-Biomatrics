"""Durable raw ingestion, device-scoped mapping and standard HRMS processing."""

import json
from datetime import timedelta

import frappe
from frappe.utils import get_datetime, get_system_timezone, now_datetime

from .normalization import clean_event, event_key, normalize_time, punch_type, validate_coordinates

TERMINAL = ("Created", "Duplicate", "Skipped")


def mapping_for(device, user_id, timestamp):
    rows = frappe.get_all(
        "Biometric Employee Mapping",
        filters={
            "device": device,
            "biometric_user_id": user_id,
            "enabled": 1,
            "effective_from": ["<=", timestamp],
        },
        fields=["name", "employee", "effective_to"],
    )
    valid = [r for r in rows if not r.effective_to or get_datetime(r.effective_to) >= timestamp]
    if len(valid) > 1:
        raise ValueError("Conflicting employee mappings; administrator review required")
    return valid[0] if valid else None


def inspect_event(device, event):
    event = clean_event(event)
    timestamp = normalize_time(event["timestamp"], device.device_timezone, get_system_timezone())
    if timestamp < get_datetime(device.sync_start):
        return {"status": "Skipped", "reason": "Before synchronization start cutoff", "time": str(timestamp)}
    if timestamp > now_datetime() + timedelta(minutes=5):
        raise ValueError("Timestamp is in the future; check the terminal clock and timezone")
    mapping = mapping_for(device.name, event["user_id"], timestamp)
    log_type = punch_type(event.get("punch", ""), [r.as_dict() for r in device.punch_mappings])
    result = {"time": str(timestamp), "log_type": log_type}
    if not mapping:
        return dict(result, status="Unmapped", reason="Map this device user to an Employee")
    result.update(employee=mapping.employee, mapping=mapping.name)
    if frappe.db.get_value("Employee", mapping.employee, "status") != "Active":
        return dict(result, status="Requires Review", reason="Employee is inactive")
    if log_type == "Unknown":
        return dict(result, status="Requires Review", reason="Configure this punch code as IN or OUT")
    if device.geolocation_policy == "Fixed Coordinates":
        result["latitude"], result["longitude"] = validate_coordinates(device.latitude, device.longitude)
    elif frappe.db.get_single_value("HR Settings", "allow_geolocation_tracking"):
        return dict(result, status="Requires Review", reason="HR Settings require terminal coordinates")
    existing = frappe.db.get_value(
        "Employee Checkin", {"employee": mapping.employee, "time": timestamp, "log_type": log_type}, "name"
    )
    if existing:
        return dict(result, status="Duplicate", checkin=existing)
    return dict(result, status="Pending")


def persist_event(device, event, run=None, agent=None):
    event = clean_event(event)
    key = event_key(device.name, event)
    # All ingesters lock the device before calling this, protecting the empty-row case.
    existing = frappe.db.get_value("Biometric Raw Log", {"external_key": key}, "name")
    if existing:
        return existing, False
    doc = frappe.get_doc(
        {
            "doctype": "Biometric Raw Log",
            "external_key": key,
            "device": device.name,
            "agent": agent,
            "sync_run": run,
            "biometric_user_id": event["user_id"],
            "device_uid": event.get("uid"),
            "native_id": event.get("native_id"),
            "original_timestamp": event["timestamp"],
            "device_timezone": device.device_timezone,
            "punch_code": event.get("punch", ""),
            "first_received": now_datetime(),
            "raw_payload": json.dumps(event),
            "status": "Received",
        }
    ).insert(ignore_permissions=True)
    return doc.name, True


def process_raw(name):
    # Serialize all app-created checkins for an Employee through the Employee row lock.
    raw = frappe.get_doc("Biometric Raw Log", name, for_update=True)
    if raw.status in TERMINAL:
        return raw.status
    device = frappe.get_doc("Biometric Device", raw.device)
    if not device.enabled or not device.live_sync_enabled:
        return raw.status
    frappe.db.savepoint("process_event")
    try:
        event = json.loads(raw.raw_payload)
        # Keep the source timezone captured at receipt even if the device settings change.
        device.device_timezone = raw.device_timezone or device.device_timezone
        result = inspect_event(device, event)
        raw.normalized_timestamp = result.get("time")
        raw.log_type = result.get("log_type", "Unknown")
        raw.employee = result.get("employee")
        raw.mapping = result.get("mapping")
        raw.status = result["status"]
        raw.processing_error = result.get("reason", "")
        if result["status"] == "Duplicate":
            raw.employee_checkin = result["checkin"]
        if result["status"] == "Pending":
            frappe.db.get_value("Employee", result["employee"], "name", for_update=True)
            # Recheck under employee lock: different devices may report the same punch.
            existing = frappe.db.get_value(
                "Employee Checkin",
                {"employee": result["employee"], "time": result["time"], "log_type": result["log_type"]},
                "name",
            )
            if existing:
                raw.status, raw.employee_checkin = "Duplicate", existing
            else:
                from hrms.hr.doctype.employee_checkin.employee_checkin import add_log_based_on_employee_field

                # Background integration runs as Administrator; the public API is role/scoped-token gated.
                checkin = add_log_based_on_employee_field(
                    employee_field_value=result["employee"],
                    employee_fieldname="name",
                    timestamp=result["time"],
                    device_id=device.device_code,
                    log_type=result["log_type"],
                    skip_auto_attendance=device.skip_auto_attendance,
                    latitude=result.get("latitude"),
                    longitude=result.get("longitude"),
                )
                raw.status, raw.employee_checkin = "Created", checkin.name
    except Exception as exc:
        frappe.db.rollback(save_point="process_event")
        raw = frappe.get_doc("Biometric Raw Log", name)
        raw.status = "Failed"
        # HRMS messages may contain HTML. Retain readable validation details only.
        from frappe.utils import strip_html

        if isinstance(exc, (frappe.ValidationError, ValueError)):
            raw.processing_error = strip_html(str(exc))[:1000]
        else:
            raw.processing_error = f"{type(exc).__name__}: Processing failed; administrator review required"
    raw.last_attempt = now_datetime()
    raw.retry_count = (raw.retry_count or 0) + 1
    raw.save(ignore_permissions=True)
    return raw.status


def process_pending(device_name=None, limit=200):
    enabled_devices = frappe.get_all(
        "Biometric Device", filters={"enabled": 1, "live_sync_enabled": 1}, pluck="name"
    )
    if not enabled_devices:
        return
    filters = {"status": ["in", ["Received", "Pending"]], "device": ["in", enabled_devices]}
    if device_name:
        if device_name not in enabled_devices:
            return
        filters["device"] = device_name
    names = frappe.get_all(
        "Biometric Raw Log", filters=filters, pluck="name", limit_page_length=limit, order_by="creation asc"
    )
    previous_user = frappe.session.user
    try:
        frappe.set_user("Administrator")
        for name in names:
            process_raw(name)
            frappe.db.commit()
    finally:
        frappe.set_user(previous_user)


def ingest_batch(device, events, run=None, agent=None):
    if not isinstance(events, list) or len(events) > 200:
        frappe.throw("Send a list containing at most 200 events")
    acknowledgements = []
    # Lock persists through the request commit; an acknowledgement never precedes durability.
    frappe.db.get_value("Biometric Device", device.name, "name", for_update=True)
    for index, event in enumerate(events):
        frappe.db.savepoint("ingest_event")
        try:
            name, created = persist_event(device, event, run, agent)
            acknowledgements.append(
                {
                    "index": index,
                    "key": event_key(device.name, event),
                    "accepted": True,
                    "raw_log": name,
                    "new": created,
                }
            )
        except (ValueError, TypeError, OverflowError) as exc:
            frappe.db.rollback(save_point="ingest_event")
            acknowledgements.append({"index": index, "accepted": False, "error": str(exc)[:300]})
    frappe.db.commit()
    # Raw history is already durable, so a worker failure must not undo receipt.
    try:
        frappe.enqueue(
            "zkt_k50_biomatrics.engine.process_pending",
            device_name=device.name,
            job_id=f"zkt-process-{device.name}",
            deduplicate=True,
        )
    except Exception:
        # Scheduler also picks up Received/Pending events after queue recovery.
        pass
    return acknowledgements
