import frappe
from frappe.utils import add_days


def execute_report(name, doctype, fields, filters=None):
    filters = frappe._dict(filters or {})
    if not frappe.has_permission(doctype, "read"):
        frappe.throw("Read permission required", frappe.PermissionError)
    query_filters = {}
    meta = frappe.get_meta(doctype)
    for field in ("device", "agent", "employee", "status", "sync_run"):
        if filters.get(field) and meta.has_field(field):
            query_filters[field] = filters[field]
    date_field = "normalized_timestamp" if doctype == "Biometric Raw Log" else "creation"
    if filters.from_date and filters.to_date:
        query_filters[date_field] = ["between", [filters.from_date, add_days(filters.to_date, 1)]]
    elif filters.from_date:
        query_filters[date_field] = [">=", filters.from_date]
    elif filters.to_date:
        query_filters[date_field] = ["<", add_days(filters.to_date, 1)]
    if name == "Attendance Integration Error Report":
        query_filters["status"] = ["in", ["Failed", "Invalid", "Requires Review"]]
    elif name == "Unmapped Biometric Users Report":
        query_filters["status"] = "Unmapped"
    elif name == "Duplicate Events Report":
        query_filters["status"] = "Duplicate"
    if name == "Daily Device Event Summary":
        rows = frappe.get_list(
            doctype,
            filters=query_filters,
            fields=["device", "normalized_timestamp", "status"],
            limit_page_length=10000,
        )
        counts = {}
        for row in rows:
            key = (row.device, str(row.normalized_timestamp or "")[:10], row.status)
            counts[key] = counts.get(key, 0) + 1
        columns = [
            dict(fieldname="device", label="Device", fieldtype="Link", options="Biometric Device", width=180),
            dict(fieldname="date", label="Date", fieldtype="Date", width=120),
            dict(fieldname="status", label="Status", fieldtype="Data", width=150),
            dict(fieldname="count", label="Count", fieldtype="Int", width=100),
        ]
        return (
            columns,
            [dict(device=k[0], date=k[1] or None, status=k[2], count=v) for k, v in sorted(counts.items())],
            "Summary limited to 10,000 events; narrow dates for larger histories.",
        )
    columns = []
    for field in fields:
        df = meta.get_field(field)
        columns.append(
            dict(
                fieldname=field,
                label=df.label if df else "ID",
                fieldtype=df.fieldtype
                if df and df.fieldtype in ("Link", "Int", "Datetime", "Check")
                else ("Link" if field == "name" else "Data"),
                options=df.options if df else doctype,
                width=180,
            )
        )
    rows = frappe.get_list(
        doctype, filters=query_filters, fields=fields, order_by="creation desc", limit_page_length=5000
    )
    return (
        columns,
        rows,
        "Results limited to 5,000 rows; narrow filters or export raw logs for larger histories.",
    )
