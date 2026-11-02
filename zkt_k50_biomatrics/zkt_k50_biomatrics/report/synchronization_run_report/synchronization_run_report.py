from zkt_k50_biomatrics.reports import execute_report


def execute(filters=None):
    return execute_report(
        "Synchronization Run Report",
        "Biometric Sync Run",
        [
            "name",
            "device",
            "agent",
            "mode",
            "status",
            "started_at",
            "records_retrieved",
            "created_count",
            "failed_count",
        ],
        filters,
    )
