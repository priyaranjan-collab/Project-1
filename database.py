# SQLite schema, connection helpers, and first-run sample data.
from datetime import date, timedelta
import os
import sqlite3

from flask import current_app, g
from werkzeug.security import generate_password_hash


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('organiser', 'customer')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organiser_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    location TEXT NOT NULL,
    capacity INTEGER NOT NULL CHECK (capacity > 0),
    category TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS registrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (event_id, user_id)
);
CREATE TABLE IF NOT EXISTS event_attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sensor_name TEXT NOT NULL DEFAULT 'IR',
    checked_in_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (event_id, user_id)
);
CREATE TABLE IF NOT EXISTS event_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0 CHECK (is_read IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_events_organiser ON events(organiser_id);
CREATE INDEX IF NOT EXISTS idx_registrations_event ON registrations(event_id);
CREATE INDEX IF NOT EXISTS idx_event_attendance_event ON event_attendance(event_id);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, is_read);
CREATE INDEX IF NOT EXISTS idx_event_logs_created ON event_logs(created_at);
"""


def database_path():
    return current_app.config["DATABASE_PATH"]


def get_db():
    if "db" not in g:
        connection = sqlite3.connect(database_path())
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        g.db = connection
    return g.db


def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db(path=None):
    target = path or database_path()
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    connection = sqlite3.connect(target)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)

    if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        users = [
            ("Asha Organiser", "organiser@example.com", generate_password_hash("organiser123"), "organiser"),
            ("Noah Organiser", "noah@example.com", generate_password_hash("organiser123"), "organiser"),
            ("Maya Customer", "customer@example.com", generate_password_hash("customer123"), "customer"),
            ("Leo Customer", "leo@example.com", generate_password_hash("customer123"), "customer"),
        ]
        connection.executemany(
            "INSERT INTO users (name, email, password, role) VALUES (?, ?, ?, ?)", users
        )
        organiser_id = connection.execute(
            "SELECT id FROM users WHERE email = ?", ("organiser@example.com",)
        ).fetchone()[0]
        events = [
            (organiser_id, "Open Source Summit", "A day of talks and workshops for builders.",
             (date.today() + timedelta(days=14)).isoformat(), "09:30", "Innovation Hall", 120, "Technology"),
            (organiser_id, "Community Design Lab", "A practical, collaborative design session.",
             (date.today() + timedelta(days=28)).isoformat(), "13:00", "Studio 4", 40, "Design"),
        ]
        connection.executemany(
            """INSERT INTO events
               (organiser_id, name, description, date, time, location, capacity, category)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", events
        )
    connection.commit()
    connection.close()