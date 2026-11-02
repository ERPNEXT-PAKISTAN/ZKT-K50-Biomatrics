from zkt_k50_biomatrics.reports import execute_report


def execute(filters=None):
    return execute_report(
        "Attendance Integration Error Report",
        "Biometric Raw Log",
        ["name", "device", "biometric_user_id", "employee", "status", "processing_error"],
        filters,
    )
