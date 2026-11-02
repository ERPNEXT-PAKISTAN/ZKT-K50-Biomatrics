from zkt_k50_biomatrics.reports import execute_report


def execute(filters=None):
    return execute_report(
        "Agent Status Report",
        "Biometric Agent",
        [
            "name",
            "agent_name",
            "enabled",
            "revoked",
            "last_heartbeat",
            "last_contact",
            "agent_version",
            "hostname",
        ],
        filters,
    )
