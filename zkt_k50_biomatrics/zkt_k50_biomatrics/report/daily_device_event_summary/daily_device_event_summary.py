from zkt_k50_biomatrics.reports import execute_report


def execute(filters=None):
    return execute_report("Daily Device Event Summary", "Biometric Raw Log", [], filters)
