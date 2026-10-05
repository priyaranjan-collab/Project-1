"""End-to-end checks for the first-review dashboard workflows."""
from datetime import date, timedelta
from contextlib import closing
import os
import re
import sqlite3
import tempfile
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from werkzeug.datastructures import FileStorage

from app import CrowdDetectionError, create_app, estimate_people_in_image


class DashboardTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = os.path.join(self.temp_dir.name, "test.sqlite3")
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DATABASE_PATH": self.database,
        })
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def token(self):
        response = self.client.get("/dashboard", follow_redirects=True)
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def login(self, email, password):
        return self.client.post("/login", data={
            "csrf_token": self.token(), "email": email, "password": password,
        }, follow_redirects=True)

    def event_form(self, name="Test Conference", capacity=20):
        return {
            "name": name,
            "description": "A practical test event for the dashboard.",
            "date": (date.today() + timedelta(days=10)).isoformat(),
            "time": "10:30",
            "location": "Review Hall",
            "category": "Technology",
            "capacity": str(capacity),
        }

    def event_id(self):
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute(
                "SELECT id FROM events WHERE name = 'Open Source Summit'"
            ).fetchone()[0]

    def test_01_organiser_login_redirects_to_organiser_dashboard(self):
        response = self.login("organiser@example.com", "organiser123")
        self.assertIn(b"Upcoming events", response.data)
        self.assertIn(b"Create event", response.data)

    def test_02_customer_login_redirects_to_customer_dashboard(self):
        response = self.login("customer@example.com", "customer123")
        self.assertIn(b"Coming up", response.data)
        self.assertIn(b"Discover events", response.data)

    def test_03_invalid_login_is_rejected(self):
        response = self.login("organiser@example.com", "wrong-password")
        self.assertIn(b"Email or password is incorrect", response.data)
        self.assertNotIn(b"Create event", response.data)

    def test_04_organiser_can_create_event_and_pipeline_records_it(self):
        self.login("organiser@example.com", "organiser123")
        data = self.event_form()
        data["csrf_token"] = self.token()
        response = self.client.post("/organiser/events/new", data=data)
        self.assertEqual(response.status_code, 302)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM events WHERE name = 'Test Conference'"
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM event_logs WHERE event_type = 'event.created'"
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM notifications WHERE message LIKE '%Test Conference%'"
            ).fetchone()[0], 1)

    def test_05_customer_can_register_for_event(self):
        self.login("customer@example.com", "customer123")
        response = self.client.post(
            f"/customer/events/{self.event_id()}/register",
            data={"csrf_token": self.token()},
        )
        self.assertEqual(response.status_code, 302)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM registrations"
            ).fetchone()[0], 1)

    def test_06_customer_can_cancel_registration(self):
        self.login("customer@example.com", "customer123")
        event_id = self.event_id()
        self.client.post(f"/customer/events/{event_id}/register", data={"csrf_token": self.token()})
        response = self.client.post(
            f"/customer/events/{event_id}/cancel", data={"csrf_token": self.token()}
        )
        self.assertEqual(response.status_code, 302)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM registrations"
            ).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM event_logs WHERE event_type = 'registration.cancelled'"
            ).fetchone()[0], 1)

    def test_07_organiser_can_update_owned_event(self):
        self.login("organiser@example.com", "organiser123")
        event_id = self.event_id()
        data = self.event_form(name="Updated Summit")
        data["csrf_token"] = self.token()
        response = self.client.post(f"/organiser/events/{event_id}/edit", data=data)
        self.assertEqual(response.status_code, 302)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT name FROM events WHERE id = ?", (event_id,)
            ).fetchone()[0], "Updated Summit")

    def test_08_organiser_can_delete_event_and_keep_audit_log(self):
        self.login("organiser@example.com", "organiser123")
        event_id = self.event_id()
        response = self.client.post(
            f"/organiser/events/{event_id}/delete", data={"csrf_token": self.token()}
        )
        self.assertEqual(response.status_code, 302)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertIsNone(connection.execute(
                "SELECT id FROM events WHERE id = ?", (event_id,)
            ).fetchone())
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM event_logs WHERE event_type = 'event.deleted'"
            ).fetchone()[0], 1)

    def test_09_customer_is_forbidden_from_organiser_routes(self):
        self.login("customer@example.com", "customer123")
        self.assertEqual(self.client.get("/organiser").status_code, 403)
        self.assertEqual(self.client.get("/organiser/analytics").status_code, 403)

    def test_10_analytics_report_and_polling_endpoint(self):
        self.login("organiser@example.com", "organiser123")
        analytics = self.client.get("/organiser/analytics")
        self.assertEqual(analytics.status_code, 200)
        self.assertIn(b"worker processes", analytics.data)
        report = self.client.get("/organiser/reports.csv")
        self.assertEqual(report.status_code, 200)
        self.assertIn(b"Utilization %", report.data)
        stats = self.client.get("/api/dashboard-stats")
        self.assertEqual(stats.status_code, 200)
        self.assertIn(b'"events":2', stats.data)

    def test_crowd_detector_counts_person_boxes_at_high_resolution(self):
        detector = Mock(return_value=[
            SimpleNamespace(
                names={0: "person", 1: "car"},
                boxes=[
                    SimpleNamespace(cls=[0]),
                    SimpleNamespace(cls=[0]),
                    SimpleNamespace(cls=[1]),
                ],
            )
        ])
        upload = FileStorage(stream=BytesIO(b"image"), filename="crowd.jpg")

        with tempfile.TemporaryDirectory() as working_directory, \
                patch("app.os.getcwd", return_value=working_directory), \
                patch("app.get_crowd_model", return_value=detector):
            count = estimate_people_in_image(upload)

        self.assertEqual(count, 2)
        detector.assert_called_once()
        self.assertEqual(detector.call_args.kwargs, {
            "verbose": False, "conf": 0.15, "imgsz": 1280, "classes": [0],
        })

    def test_crowd_detector_returns_zero_without_person_boxes(self):
        detector = Mock(return_value=[
            SimpleNamespace(names={0: "person"}, boxes=[])
        ])
        upload = FileStorage(stream=BytesIO(b"image"), filename="empty.jpg")

        with tempfile.TemporaryDirectory() as working_directory, \
                patch("app.os.getcwd", return_value=working_directory), \
                patch("app.get_crowd_model", return_value=detector):
            count = estimate_people_in_image(upload)

        self.assertEqual(count, 0)

    def test_crowd_detection_errors_are_not_replaced_by_estimates(self):
        upload = FileStorage(stream=BytesIO(b"image"), filename="crowd.jpg")

        with tempfile.TemporaryDirectory() as working_directory, \
                patch("app.os.getcwd", return_value=working_directory), \
                patch("app.get_crowd_model", side_effect=CrowdDetectionError("Model unavailable")):
            with self.assertRaisesRegex(CrowdDetectionError, "Model unavailable"):
                estimate_people_in_image(upload)

    def test_crowd_detection_failure_is_returned_without_saving_a_count(self):
        self.login("organiser@example.com", "organiser123")
        with patch("app.estimate_people_in_image", side_effect=CrowdDetectionError("Model unavailable")):
            response = self.client.post(
                f"/organiser/events/{self.event_id()}/crowd-count",
                data={
                    "csrf_token": self.token(),
                    "image": (BytesIO(b"image"), "crowd.jpg"),
                },
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn(b"Model unavailable", response.data)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM event_crowd_counts"
            ).fetchone()[0], 0)

    def test_passwords_are_hashed_in_sqlite(self):
        with closing(sqlite3.connect(self.database)) as connection:
            stored = connection.execute(
                "SELECT password FROM users WHERE email = 'customer@example.com'"
            ).fetchone()[0]
        self.assertNotEqual(stored, "customer123")
        self.assertTrue(stored.startswith("scrypt:"))

    def test_11_customer_ir_checkin_is_recorded_and_visible_on_organiser_dashboard(self):
        self.login("customer@example.com", "customer123")
        event_id = self.event_id()
        self.client.post(
            f"/customer/events/{event_id}/register",
            data={"csrf_token": self.token()},
        )

        response = self.client.post(
            f"/customer/events/{event_id}/sensor-entry",
            data={"csrf_token": self.token()},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"IR entry logged", response.data)

        with closing(sqlite3.connect(self.database)) as connection:
            customer_id = connection.execute(
                "SELECT id FROM users WHERE email = ?", ("customer@example.com",)
            ).fetchone()[0]
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM event_attendance WHERE event_id = ? AND user_id = ?",
                (event_id, customer_id),
            ).fetchone()[0], 1)

        self.client.post("/logout", data={"csrf_token": self.token()}, follow_redirects=True)
        self.login("organiser@example.com", "organiser123")
        dashboard = self.client.get("/organiser")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"1", dashboard.data)
        self.assertIn(b"IR check-ins", dashboard.data)


if __name__ == "__main__":
    unittest.main()