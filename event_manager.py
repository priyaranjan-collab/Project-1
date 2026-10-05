"""Event capture, validation, persistence, logging, notification, and dispatch."""
from datetime import date

from socket_client import send_event


class EventValidationError(ValueError):
    """Raised when an event action does not satisfy the domain rules."""


class EventManager:
    def __init__(self, connection):
        self.connection = connection

    @staticmethod
    def validate_event(data):
        fields = {key: str(data.get(key, "")).strip() for key in
                  ("name", "description", "date", "time", "location", "category")}
        if not 3 <= len(fields["name"]) <= 100:
            raise EventValidationError("Event name must be between 3 and 100 characters.")
        if not fields["description"] or len(fields["description"]) > 2000:
            raise EventValidationError("Add a description of up to 2,000 characters.")
        if not fields["location"] or len(fields["location"]) > 120:
            raise EventValidationError("Enter a location of up to 120 characters.")
        if not 2 <= len(fields["category"]) <= 40:
            raise EventValidationError("Category must be between 2 and 40 characters.")
        try:
            event_date = date.fromisoformat(fields["date"])
            if event_date < date.today():
                raise EventValidationError("Event date cannot be in the past.")
            hour, minute = (int(part) for part in fields["time"].split(":"))
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                raise ValueError
        except (ValueError, TypeError):
            raise EventValidationError("Enter a valid future date and time.") from None
        try:
            capacity = int(data.get("capacity", ""))
            if not 1 <= capacity <= 100000:
                raise ValueError
        except (ValueError, TypeError):
            raise EventValidationError("Capacity must be between 1 and 100,000.") from None
        fields["time"] = f"{hour:02d}:{minute:02d}"
        fields["capacity"] = capacity
        return fields

    def _record(self, event_id, user_id, event_type, detail, notify_ids):
        self.connection.execute(
            "INSERT INTO event_logs (event_id, user_id, event_type, detail) VALUES (?, ?, ?, ?)",
            (event_id, user_id, event_type, detail),
        )
        for recipient_id, message in notify_ids:
            self.connection.execute(
                "INSERT INTO notifications (user_id, message) VALUES (?, ?)",
                (recipient_id, message),
            )

    def _dispatch(self, event_id, user_id, event_type, detail):
        send_event({"event_id": event_id, "user_id": user_id,
                    "type": event_type, "detail": detail})

    def create(self, data, organiser_id):
        fields = self.validate_event(data)
        with self.connection:
            cursor = self.connection.execute(
                """INSERT INTO events
                   (organiser_id, name, description, date, time, location, capacity, category)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (organiser_id, fields["name"], fields["description"], fields["date"],
                 fields["time"], fields["location"], fields["capacity"], fields["category"]),
            )
            event_id = cursor.lastrowid
            detail = f"Created event: {fields['name']}"
            self._record(event_id, organiser_id, "event.created", detail,
                         [(organiser_id, detail)])
        self._dispatch(event_id, organiser_id, "event.created", detail)
        return event_id

    def update(self, event_id, data, organiser_id):
        fields = self.validate_event(data)
        event = self.connection.execute(
            "SELECT id FROM events WHERE id = ? AND organiser_id = ?", (event_id, organiser_id)
        ).fetchone()
        if event is None:
            raise EventValidationError("Event not found or you do not own it.")
        with self.connection:
            self.connection.execute(
                """UPDATE events SET name = ?, description = ?, date = ?, time = ?, location = ?,
                   capacity = ?, category = ? WHERE id = ? AND organiser_id = ?""",
                (fields["name"], fields["description"], fields["date"], fields["time"],
                 fields["location"], fields["capacity"], fields["category"], event_id, organiser_id),
            )
            detail = f"Updated event: {fields['name']}"
            self._record(event_id, organiser_id, "event.updated", detail,
                         [(organiser_id, detail)])
        self._dispatch(event_id, organiser_id, "event.updated", detail)

    def delete(self, event_id, organiser_id):
        event = self.connection.execute(
            "SELECT name FROM events WHERE id = ? AND organiser_id = ?", (event_id, organiser_id)
        ).fetchone()
        if event is None:
            raise EventValidationError("Event not found or you do not own it.")
        name = event["name"]
        with self.connection:
            detail = f"Deleted event: {name}"
            self._record(event_id, organiser_id, "event.deleted", detail,
                         [(organiser_id, detail)])
            self.connection.execute("DELETE FROM events WHERE id = ?", (event_id,))
        self._dispatch(event_id, organiser_id, "event.deleted", detail)

    def register(self, event_id, customer_id):
        event = self.connection.execute(
            """SELECT e.*, (SELECT COUNT(*) FROM registrations r WHERE r.event_id = e.id) AS attendees
               FROM events e WHERE e.id = ? AND e.status = 'active'""", (event_id,)
        ).fetchone()
        if event is None:
            raise EventValidationError("This event is no longer available.")
        if event["attendees"] >= event["capacity"]:
            raise EventValidationError("This event has reached capacity.")
        if self.connection.execute(
            "SELECT 1 FROM registrations WHERE event_id = ? AND user_id = ?",
            (event_id, customer_id),
        ).fetchone():
            raise EventValidationError("You are already registered for this event.")
        with self.connection:
            self.connection.execute(
                "INSERT INTO registrations (event_id, user_id) VALUES (?, ?)",
                (event_id, customer_id),
            )
            detail = f"Registration created for {event['name']}"
            self._record(event_id, customer_id, "registration.created", detail,
                         [(customer_id, detail), (event["organiser_id"],
                                                  f"A customer registered for {event['name']}.")])
        self._dispatch(event_id, customer_id, "registration.created", detail)

    def cancel_registration(self, event_id, customer_id):
        row = self.connection.execute(
            """SELECT e.name, e.organiser_id FROM registrations r JOIN events e ON e.id = r.event_id
               WHERE r.event_id = ? AND r.user_id = ?""", (event_id, customer_id)
        ).fetchone()
        if row is None:
            raise EventValidationError("Registration not found.")
        with self.connection:
            self.connection.execute(
                "DELETE FROM registrations WHERE event_id = ? AND user_id = ?",
                (event_id, customer_id),
            )
            detail = f"Registration cancelled for {row['name']}"
            self._record(event_id, customer_id, "registration.cancelled", detail,
                         [(customer_id, detail), (row["organiser_id"],
                                                  f"A customer cancelled for {row['name']}.")])
        self._dispatch(event_id, customer_id, "registration.cancelled", detail)

    def mark_attendance(self, event_id, customer_id, sensor_name="IR"):
        row = self.connection.execute(
            """SELECT e.name, e.organiser_id, u.name AS customer_name
               FROM registrations r
               JOIN events e ON e.id = r.event_id
               JOIN users u ON u.id = r.user_id
               WHERE r.event_id = ? AND r.user_id = ?""",
            (event_id, customer_id),
        ).fetchone()
        if row is None:
            raise EventValidationError("Register for the event before simulating an IR entry.")
        if self.connection.execute(
            "SELECT 1 FROM event_attendance WHERE event_id = ? AND user_id = ?",
            (event_id, customer_id),
        ).fetchone():
            raise EventValidationError("This customer has already logged an IR check-in for this event.")
        with self.connection:
            self.connection.execute(
                "INSERT INTO event_attendance (event_id, user_id, sensor_name) VALUES (?, ?, ?)",
                (event_id, customer_id, sensor_name),
            )
            detail = f"{row['customer_name']} entered via {sensor_name} sensor for {row['name']}"
            self._record(event_id, customer_id, "attendance.logged", detail,
                         [(customer_id, f"IR entry logged for {row['name']}.") ,
                          (row["organiser_id"],
                           f"{row['customer_name']} entered {row['name']} using the IR sensor.")])
        self._dispatch(event_id, customer_id, "attendance.logged", detail)