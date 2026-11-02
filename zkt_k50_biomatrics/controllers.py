import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime

from .normalization import validate_coordinates


def configuration_hash(doc):
    import hashlib
    import json

    values = {
        k: doc.get(k)
        for k in (
            "driver",
            "host",
            "port",
            "transport",
            "omit_ping",
            "device_timezone",
            "sync_start",
            "geolocation_policy",
            "latitude",
            "longitude",
            "skip_auto_attendance",
            "connection_mode",
            "agent",
        )
    }
    from frappe.utils import get_system_timezone

    values["site_timezone"] = get_system_timezone()
    values["punch_mappings"] = [(r.punch_code, r.log_type) for r in doc.punch_mappings]
    return hashlib.sha256(json.dumps(values, default=str, sort_keys=True).encode()).hexdigest()


class BiometricDevice(Document):
    def validate(self):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", self.device_code or ""):
            frappe.throw("Device Code may contain only letters, numbers, underscores and hyphens")
        try:
            ZoneInfo(self.device_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            frappe.throw("Choose a valid IANA timezone, for example Asia/Karachi")
        if not (1 <= self.port <= 65535 and 1 <= self.timeout <= 120 and self.polling_interval >= 60):
            frappe.throw(
                "Port must be 1–65535, timeout 1–120 seconds and polling interval at least 60 seconds"
            )
        if self.connection_mode == "Edge Agent" and not self.agent:
            frappe.throw("Assign an edge agent for Edge Agent mode")
        if self.geolocation_policy == "Fixed Coordinates":
            validate_coordinates(self.latitude, self.longitude)
        codes = [r.punch_code for r in self.punch_mappings]
        if len(codes) != len(set(codes)):
            frappe.throw("Each punch code can be mapped only once")
        if any(r.log_type not in ("IN", "OUT") for r in self.punch_mappings):
            frappe.throw("Punch mappings must use IN or OUT")
        if self.live_sync_enabled and (
            not self.previewed_at or self.preview_config_hash != configuration_hash(self)
        ):
            frappe.throw(
                "Save configuration, run a dry-run preview, review it, then enable live synchronization"
            )
        key = self.get_password("communication_key", raise_exception=False)
        if key and (not key.isdigit() or not 0 <= int(key) <= 4294967295):
            frappe.throw("Communication key must be an unsigned 32-bit integer")


class BiometricAgent(Document):
    def validate(self):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", self.agent_code or ""):
            frappe.throw("Agent Code may contain only letters, numbers, underscores and hyphens")
        if self.revoked:
            self.token_hash = ""
            self.enrollment_hash = ""
            self.enabled = 0


class BiometricEmployeeMapping(Document):
    def validate(self):
        if self.effective_to and get_datetime(self.effective_to) < get_datetime(self.effective_from):
            frappe.throw("Effective To must follow Effective From")
        # Lock the parent device to serialize mapping changes, including empty ranges.
        frappe.db.get_value("Biometric Device", self.device, "name", for_update=True)
        if self.enabled:
            rows = frappe.get_all(
                "Biometric Employee Mapping",
                filters={
                    "device": self.device,
                    "biometric_user_id": self.biometric_user_id,
                    "enabled": 1,
                    "name": ["!=", self.name or ""],
                },
                fields=["effective_from", "effective_to"],
            )
            start = get_datetime(self.effective_from)
            end = get_datetime(self.effective_to) if self.effective_to else get_datetime("9999-12-31")
            for row in rows:
                other_end = get_datetime(row.effective_to) if row.effective_to else get_datetime("9999-12-31")
                if start <= other_end and get_datetime(row.effective_from) <= end:
                    frappe.throw("An enabled mapping overlaps this device/user effective date range")


class BiometricRawLog(Document):
    def on_trash(self):
        frappe.throw(
            "Raw attendance history cannot be deleted. Back up and archive through an audited process."
        )


class BiometricSyncRun(Document):
    pass


class BiometricPunchMapping(Document):
    pass
