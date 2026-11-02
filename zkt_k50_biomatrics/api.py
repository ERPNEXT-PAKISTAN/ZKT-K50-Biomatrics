from datetime import timedelta

import frappe
from frappe.utils import get_datetime, now_datetime

from .security import require_admin, require_manager, new_secret


@frappe.whitelist(methods=["POST"])
def start_operation(device, operation="Sync", dry_run=1, start=None, end=None):
    from .jobs import queue_run

    require_manager()
    doc = frappe.get_doc("Biometric Device", device)
    doc.check_permission("write")
    if not doc.enabled:
        frappe.throw("Enable the device to test connectivity or preview attendance")
    if operation not in ("Sync", "Test Connection", "Device Info", "Fetch Users"):
        frappe.throw("Unsupported device operation")
    dry_run = bool(int(dry_run))
    if operation == "Sync" and not dry_run and not doc.live_sync_enabled:
        frappe.throw("Run a preview and explicitly enable live synchronization first")
    if start or end:
        if not start or not end or get_datetime(start) > get_datetime(end):
            frappe.throw("Choose a valid start and end for a historical import")
        if get_datetime(end) - get_datetime(start) > timedelta(days=31):
            frappe.throw("Historical imports are limited to 31 days per run")
        if not dry_run:
            require_admin()
    return queue_run(doc, operation, dry_run, start, end, "Historical Import" if start else "Manual")


@frappe.whitelist(methods=["POST"])
def retry_events(device, names=None):
    require_manager()
    frappe.get_doc("Biometric Device", device).check_permission("write")
    filters = {"device": device, "status": ["in", ["Failed", "Unmapped", "Invalid", "Requires Review"]]}
    if names:
        names = frappe.parse_json(names)
        if not isinstance(names, list) or len(names) > 200:
            frappe.throw("Choose at most 200 raw logs")
        filters["name"] = ["in", names]
    rows = frappe.get_all("Biometric Raw Log", filters=filters, pluck="name", limit_page_length=200)
    for name in rows:
        frappe.db.set_value("Biometric Raw Log", name, "status", "Pending")
    frappe.enqueue("zkt_k50_biomatrics.engine.process_pending", device_name=device, enqueue_after_commit=True)
    return {"queued": len(rows)}


@frappe.whitelist(methods=["POST"])
def create_enrollment(agent):
    require_admin()
    doc = frappe.get_doc("Biometric Agent", agent)
    doc.check_permission("write")
    if not doc.enabled or doc.revoked:
        frappe.throw("Enable the agent and remove revoked status before enrollment")
    token, token_hash = new_secret()
    frappe.db.set_value(
        "Biometric Agent",
        agent,
        {"enrollment_hash": token_hash, "enrollment_expires": now_datetime() + timedelta(minutes=15)},
    )
    # Only newly generated enrollment material is returned once, never existing credentials.
    return {"credential": f"{agent}:{token}", "expires_in_minutes": 15}


@frappe.whitelist(methods=["POST"])
def revoke_agent(agent):
    require_admin()
    doc = frappe.get_doc("Biometric Agent", agent)
    doc.check_permission("write")
    doc.revoked, doc.enabled, doc.token_hash, doc.enrollment_hash = 1, 0, "", ""
    doc.save()


@frappe.whitelist(methods=["POST"])
def map_users(device, mappings):
    require_manager()
    frappe.get_doc("Biometric Device", device).check_permission("write")
    rows = frappe.parse_json(mappings)
    if not isinstance(rows, list) or len(rows) > 200:
        frappe.throw("Map at most 200 users per request")
    created = []
    for row in rows:
        # Explicit Employee selection only. Existing mappings are never overwritten.
        doc = frappe.get_doc(
            {
                "doctype": "Biometric Employee Mapping",
                "device": device,
                "biometric_user_id": str(row["user_id"]),
                "employee": row["employee"],
                "effective_from": row["effective_from"],
                "enabled": 1,
            }
        )
        doc.insert()
        created.append(doc.name)
    return created


@frappe.whitelist()
def dashboard():
    if not {
        "System Manager",
        "Biometric Administrator",
        "Biometric Manager",
        "Biometric Viewer",
    }.intersection(frappe.get_roles()):
        frappe.throw("Biometric access required", frappe.PermissionError)
    cutoff = now_datetime() - timedelta(minutes=15)
    today = now_datetime().replace(hour=0, minute=0, second=0, microsecond=0)
    count = frappe.db.count
    return {
        "Total Devices": count("Biometric Device"),
        "Recently Seen Devices": count("Biometric Device", {"enabled": 1, "last_seen": [">=", cutoff]}),
        "Agents Online": count(
            "Biometric Agent", {"enabled": 1, "revoked": 0, "last_heartbeat": [">=", cutoff]}
        ),
        "Events Received Today": count("Biometric Raw Log", {"first_received": [">=", today]}),
        "Checkins Created Today": count(
            "Biometric Raw Log", {"status": "Created", "last_attempt": [">=", today]}
        ),
        "Unmapped Events": count("Biometric Raw Log", {"status": "Unmapped"}),
        "Pending Events": count("Biometric Raw Log", {"status": ["in", ["Received", "Pending"]]}),
        "Failed / Review Events": count(
            "Biometric Raw Log", {"status": ["in", ["Failed", "Invalid", "Requires Review"]]}
        ),
    }


@frappe.whitelist()
def discovered_users(device):
    import json

    require_manager()
    doc = frappe.get_doc("Biometric Device", device)
    doc.check_permission("read")
    mappings = frappe.get_all(
        "Biometric Employee Mapping",
        filters={"device": device, "enabled": 1},
        fields=["biometric_user_id", "employee", "effective_from", "effective_to"],
    )
    current = {}
    for row in mappings:
        if get_datetime(row.effective_from) <= now_datetime() and (
            not row.effective_to or get_datetime(row.effective_to) >= now_datetime()
        ):
            current.setdefault(row.biometric_user_id, []).append(row.employee)
    users = json.loads(doc.discovered_users or "[]")
    return [
        dict(
            user_id=u["user_id"],
            device_user_name=u.get("name", ""),
            uid=u.get("uid", ""),
            current_employee=", ".join(current.get(u["user_id"], [])),
            mapping_status="Mapped" if current.get(u["user_id"]) else "Unmapped",
        )
        for u in users
    ]
