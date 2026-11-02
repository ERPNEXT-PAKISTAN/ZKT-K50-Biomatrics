from zkt_k50_biomatrics.reports import execute_report


def execute(filters=None):
    return execute_report(
        "Device Health Report",
        "Biometric Device",
        [
            "name",
            "device_name",
            "enabled",
            "connection_mode",
            "last_seen",
            "last_sync",
            "last_status",
            "consecutive_errors",
        ],
        filters,
    )
