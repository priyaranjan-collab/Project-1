"""Build downloadable CSV reports from organiser event data."""
import csv
from io import StringIO


def event_report(connection, organiser_id):
    rows = connection.execute(
        """SELECT e.id, e.name, e.category, e.date, e.time, e.location,
                  e.capacity, e.status, COUNT(r.id) AS registrations
           FROM events e LEFT JOIN registrations r ON r.event_id = e.id
           WHERE e.organiser_id = ?
           GROUP BY e.id ORDER BY e.date, e.time""",
        (organiser_id,),
    ).fetchall()
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Event ID", "Event", "Category", "Date", "Time", "Location",
                     "Capacity", "Registrations", "Utilization %", "Status"])
    for event in rows:
        utilization = round(event["registrations"] * 100 / event["capacity"], 1)
        writer.writerow([event["id"], event["name"], event["category"], event["date"],
                         event["time"], event["location"], event["capacity"],
                         event["registrations"], utilization, event["status"]])
    return output.getvalue()