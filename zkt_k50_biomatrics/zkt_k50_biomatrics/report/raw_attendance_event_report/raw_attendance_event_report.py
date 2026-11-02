from zkt_k50_biomatrics.reports import execute_report


def execute(filters=None):
    return execute_report(
        "Raw Attendance Event Report",
        "Biometric Raw Log",
        [
            "name",
            "device",
            "agent",
            "biometric_user_id",
            "employee",
            "normalized_timestamp",
            "log_type",
            "status",
            "employee_checkin",
            "sync_run",
        ],
        filters,
    )
