from zkt_k50_biomatrics.reports import execute_report


def execute(filters=None):
    return execute_report(
        "Unmapped Biometric Users Report",
        "Biometric Raw Log",
        ["name", "device", "biometric_user_id", "normalized_timestamp", "status"],
        filters,
    )
