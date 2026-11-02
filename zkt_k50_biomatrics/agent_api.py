"""HTTPS endpoints gated by high-entropy, revocable, device-scoped agent credentials.

Guest routing is necessary for agents without Frappe accounts; every endpoint
performs explicit authentication before accessing data. X-ZKT-Credential is not
an Authorization header, so Frappe API-key authentication does not intercept it.
"""

import json
import secrets
from datetime import timedelta

import frappe
from frappe.rate_limiter import rate_limit
from frappe.utils import get_datetime, now_datetime

from .controllers import configuration_hash
from .engine import ingest_batch
from .jobs import preview_events, filter_events
from .security import authenticate_agent, assigned_device, new_secret


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=120, seconds=60)
def enroll():
    agent = authenticate_agent(enrollment=True)
    # Consume the one-use code under lock to prevent parallel redemptions.
    frappe.db.get_value("Biometric Agent", agent.name, "name", for_update=True)
    agent = authenticate_agent(enrollment=True)
    token, hashed = new_secret()
    frappe.db.set_value(
        "Biometric Agent",
        agent.name,
        {
            "token_hash": hashed,
            "enrollment_hash": "",
            "enrollment_expires": None,
            "credential_rotated_at": now_datetime(),
        },
    )
    return {"credential": f"{agent.name}:{token}"}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=120, seconds=60)
def heartbeat(version="", hostname=""):
    agent = authenticate_agent()
    frappe.db.set_value(
        "Biometric Agent",
        agent.name,
        {
            "last_heartbeat": now_datetime(),
            "last_contact": now_datetime(),
            "agent_version": str(version)[:140],
            "hostname": str(hostname)[:140],
        },
    )
    devices = frappe.get_all(
        "Biometric Device",
        filters={"agent": agent.name, "connection_mode": "Edge Agent", "enabled": 1},
        fields=[
            "name",
            "device_code",
            "host",
            "port",
            "transport",
            "timeout",
            "omit_ping",
            "device_timezone",
            "sync_start",
            "polling_interval",
            "live_sync_enabled",
            "driver",
        ],
    )
    from frappe.utils import get_system_timezone

    for device in devices:
        device["site_timezone"] = get_system_timezone()
    # Device communication keys stay in protected local config; never returned to a browser or agent.
    commands = []
    for device in devices:
        names = frappe.get_all(
            "Biometric Sync Run",
            filters={"device": device.name, "status": ["in", ["Queued", "Running"]]},
            pluck="name",
            order_by="creation asc",
            limit_page_length=1,
        )
        if names:
            run = frappe.get_doc("Biometric Sync Run", names[0], for_update=True)
            if (
                run.status == "Running"
                and run.lease_expires
                and get_datetime(run.lease_expires) > now_datetime()
            ):
                continue
            if run.mode == "Dry Run":
                snapshot = frappe.get_doc("Biometric Device", device.name)
                run.preview = json.dumps({"configuration_hash": configuration_hash(snapshot)})
                run.records_retrieved = 0
            run.status, run.started_at = "Running", now_datetime()
            run.lease_token = secrets.token_urlsafe(32)
            run.lease_expires = now_datetime() + timedelta(minutes=20)
            run.save(ignore_permissions=True)
            commands.append(
                {
                    "run": run.name,
                    "device": device.name,
                    "operation": run.operation,
                    "mode": run.mode,
                    "start": run.range_start,
                    "end": run.range_end,
                    "lease": run.lease_token,
                }
            )
    return {"devices": devices, "commands": commands}


def command_for(agent, device, run_name, lease):
    run = frappe.get_doc("Biometric Sync Run", run_name, for_update=True)
    if (
        run.device != device.name
        or run.agent != agent.name
        or run.status != "Running"
        or not run.lease_token
        or not secrets.compare_digest(run.lease_token, str(lease))
        or get_datetime(run.lease_expires) < now_datetime()
    ):
        frappe.throw("Invalid or expired command lease", frappe.PermissionError)
    run.db_set("lease_expires", now_datetime() + timedelta(minutes=20), update_modified=False)
    return run


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=120, seconds=60)
def ingest(device, events, run=None, lease=None):
    agent = authenticate_agent()
    doc = assigned_device(agent, device)
    if not doc.live_sync_enabled:
        frappe.throw("Live synchronization is disabled for this device", frappe.PermissionError)
    records = frappe.parse_json(events)
    if run:
        command = command_for(agent, doc, run, lease)
        if command.mode != "Live" or command.operation != "Sync":
            frappe.throw("This command cannot ingest live events", frappe.PermissionError)
    acks = ingest_batch(doc, records, run, agent.name)
    if run:
        table = frappe.qb.DocType("Biometric Sync Run")
        (
            frappe.qb.update(table)
            .set(table.events_received, table.events_received + sum(a["accepted"] for a in acks))
            .set(table.records_retrieved, table.records_retrieved + len(records))
            .where(table.name == run)
            .run()
        )
    frappe.db.set_value(
        "Biometric Device",
        doc.name,
        {
            "last_seen": now_datetime(),
            "last_sync": now_datetime(),
            "last_status": "Success",
            "last_error": "",
        },
    )
    frappe.db.set_value("Biometric Agent", agent.name, "last_contact", now_datetime())
    return {"acknowledgements": acks}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=120, seconds=60)
def preview(device, run, lease, events):
    agent = authenticate_agent()
    doc = assigned_device(agent, device)
    command = command_for(agent, doc, run, lease)
    if command.mode != "Dry Run" or command.operation != "Sync":
        frappe.throw("A dry-run command is required", frappe.PermissionError)
    records = frappe.parse_json(events)
    if not isinstance(records, list) or len(records) > 200:
        frappe.throw("Send at most 200 preview events")
    records = filter_events(doc, records, command.range_start, command.range_end)
    result = preview_events(doc, records)
    previous = frappe.parse_json(command.preview or "{}")
    import hashlib

    batch_hash = hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
    if batch_hash in previous.get("batch_hashes", []):
        return {"received": len(records)}
    previous.setdefault("batch_hashes", []).append(batch_hash)
    for status, count in result["counts"].items():
        previous.setdefault("counts", {})[status] = previous.setdefault("counts", {}).get(status, 0) + count
    previous["sample"] = (previous.get("sample", []) + result["sample"])[:30]
    command.preview = json.dumps(previous)
    command.records_retrieved = (command.records_retrieved or 0) + len(records)
    command.save(ignore_permissions=True)
    return {"received": len(records)}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=120, seconds=60)
def complete(device, run, lease, result=None, error=None):
    agent = authenticate_agent()
    doc = assigned_device(agent, device)
    command = command_for(agent, doc, run, lease)
    result = frappe.parse_json(result or "{}")
    changed = (
        command.operation == "Sync"
        and command.mode == "Dry Run"
        and frappe.parse_json(command.preview or "{}").get("configuration_hash") != configuration_hash(doc)
    )
    if error or changed:
        # Never accept arbitrary edge exception text into server logs.
        command.status = "Failed"
        command.error_summary = (
            "Configuration changed during preview; queue another preview"
            if changed
            else "Edge operation failed; inspect local agent logs"
        )
    else:
        if command.operation == "Fetch Users":
            users = result.get("users", [])
            if not isinstance(users, list) or len(users) > 10000:
                frappe.throw("User discovery is limited to 10,000 users")
            safe = [
                {
                    "user_id": str(u.get("user_id", ""))[:140],
                    "uid": str(u.get("uid", ""))[:140],
                    "name": str(u.get("name", ""))[:140],
                }
                for u in users
            ]
            frappe.db.set_value("Biometric Device", doc.name, "discovered_users", json.dumps(safe))
        elif command.operation in ("Device Info", "Test Connection"):
            info = {k: str(result.get(k, ""))[:200] for k in ("serial_number", "firmware")}
            frappe.db.set_value("Biometric Device", doc.name, "device_info", json.dumps(info))
        elif command.mode == "Dry Run":
            frappe.db.set_value(
                "Biometric Device",
                doc.name,
                {"previewed_at": now_datetime(), "preview_config_hash": configuration_hash(doc)},
            )
        else:
            from .jobs import refresh_counters

            refresh_counters(command)
        pending_events = max(0, min(int(result.get("pending_events", 0)), 1000000))
        if pending_events:
            command.error_summary = (
                f"{pending_events} events remain in the agent outbox; inspect agent health and raw logs"
            )
        command.status = (
            "Partial" if pending_events or command.failed_count or command.unmapped_count else "Success"
        )
        frappe.db.set_value("Biometric Device", doc.name, {"last_seen": now_datetime(), "last_error": ""})
    command.ended_at = now_datetime()
    command.duration = (command.ended_at - get_datetime(command.started_at)).total_seconds()
    command.lease_token = ""
    command.save(ignore_permissions=True)
    return {"completed": True}
