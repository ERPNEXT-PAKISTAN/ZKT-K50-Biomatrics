from zkt_k50_biomatrics.reports import execute_report


def execute(filters=None):
    return execute_report(
        "Duplicate Events Report",
        "Biometric Raw Log",
        ["name", "device", "employee", "normalized_timestamp", "employee_checkin"],
        filters,
    )
