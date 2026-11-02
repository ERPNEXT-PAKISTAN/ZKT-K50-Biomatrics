import frappe

ROLES = ("Biometric Administrator", "Biometric Manager", "Biometric Viewer")


def setup():
    for role in ROLES:
        if not frappe.db.exists("Role", role):
            frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(
                ignore_permissions=True
            )
    # Compound index supports mapping lookups; parent row locks protect overlapping ranges.
    frappe.db.add_index(
        "Biometric Employee Mapping", ["device", "biometric_user_id", "enabled"], "biometric_mapping_lookup"
    )
    frappe.db.add_index("Biometric Raw Log", ["status", "device"], "biometric_backlog")


def before_uninstall():
    if frappe.db.count("Biometric Raw Log") or frappe.db.count("Biometric Sync Run"):
        frappe.throw(
            "Uninstall blocked: export and archive biometric history first. "
            "Disable devices and revoke agents to stop safely without losing audit history."
        )
