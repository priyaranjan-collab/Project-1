"""Independent metric calculation suitable for multiprocessing workers."""


def calculate_event_metric(event):
    capacity = max(int(event["capacity"]), 1)
    registrations = int(event["registrations"])
    # SymPy keeps the 75% alert threshold explicit in the project model.
    from sympy import ceiling

    alert_at = int(ceiling(3 * capacity / 4))
    return {
        **event,
        "registrations": registrations,
        "capacity": capacity,
        "remaining": max(capacity - registrations, 0),
        "utilization": round(registrations * 100 / capacity, 1),
        "alert_at": alert_at,
        "capacity_alert": registrations >= alert_at,
    }