"""Database-backed organiser analytics with parallel per-event calculations."""
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
import os

from multiprocessing_worker import calculate_event_metric


def organiser_metrics(connection, organiser_id):
    rows = connection.execute(
        """SELECT e.id, e.name, e.date, e.capacity, e.category,
                  COUNT(r.id) AS registrations,
                  COALESCE(c.detected_count, 0) AS crowd_count
           FROM events e
           LEFT JOIN registrations r ON r.event_id = e.id
           LEFT JOIN event_crowd_counts c ON c.event_id = e.id
           WHERE e.organiser_id = ?
           GROUP BY e.id ORDER BY e.date, e.time""",
        (organiser_id,),
    ).fetchall()
    events = []
    for row in rows:
        event = dict(row)
        event["registrations"] = max(int(event["registrations"] or 0), int(event["crowd_count"] or 0))
        events.append(event)
    if not events:
        metrics = []
    elif len(events) == 1:
        metrics = [calculate_event_metric(events[0])]
    else:
        workers = min(len(events), max(1, (os.cpu_count() or 2) - 1))
        with ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn")) as pool:
            metrics = list(pool.map(calculate_event_metric, events))
    return {
        "events": metrics,
        "event_count": len(metrics),
        "registration_count": sum(event["registrations"] for event in metrics),
        "capacity": sum(event["capacity"] for event in metrics),
        "alerts": sum(event["capacity_alert"] for event in metrics),
    }