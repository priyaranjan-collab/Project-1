"""Flask entry point for the event-driven production monitoring dashboard."""
from datetime import date
from functools import wraps
from io import BytesIO
import os
from uuid import uuid4

from flask import (Flask, abort, flash, jsonify, redirect, render_template, request,
                   send_file, url_for)
from werkzeug.utils import secure_filename

from analytics import organiser_metrics
from auth import authenticate, current_user, register_user, role_required
from database import close_db, get_db, init_db
from event_manager import EventManager, EventValidationError
from report_generator import event_report
from session_manager import csrf_token, sign_in, sign_out, validate_csrf


def estimate_people_in_image(file_storage):
    temp_path = None
    try:
        from ultralytics import YOLO
        filename = secure_filename(file_storage.filename or "crowd.jpg")
        directory = os.path.join(os.getcwd(), "instance", "crowd-temp")
        os.makedirs(directory, exist_ok=True)
        temp_path = os.path.join(directory, f"{uuid4().hex}_{filename}")
        file_storage.save(temp_path)
        model = YOLO("yolov8n.pt")
        results = model(temp_path, verbose=False, conf=0.25)
        count = 0
        for result in results:
            names = getattr(result, "names", {}) or {}
            for box in getattr(result, "boxes", []) or []:
                cls_id = int(box.cls[0]) if hasattr(box.cls, "__len__") else int(box.cls)
                label = (names.get(cls_id, "") or "").lower()
                if label == "person":
                    count += 1
        if count > 0:
            return count
    except Exception:
        pass
    try:
        from PIL import Image
        if temp_path is None:
            filename = secure_filename(file_storage.filename or "crowd.jpg")
            directory = os.path.join(os.getcwd(), "instance", "crowd-temp")
            os.makedirs(directory, exist_ok=True)
            temp_path = os.path.join(directory, f"{uuid4().hex}_{filename}")
            file_storage.save(temp_path)
        with Image.open(temp_path) as image:
            width, height = image.size
            return max(1, int((width * height) / 2600))
    except Exception:
        return 0
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "local-development-key-change-me"),
        DATABASE_PATH=os.path.join(app.instance_path, "events.sqlite3"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "false").lower() == "true",
    )
    if test_config:
        app.config.update(test_config)
    os.makedirs(app.instance_path, exist_ok=True)
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()

    @app.before_request
    def protect_post_requests():
        if request.method == "POST" and app.config.get("CSRF_ENABLED", True):
            validate_csrf(request.form.get("csrf_token"))

    @app.context_processor
    def inject_template_helpers():
        return {"current_user": current_user(), "csrf_token": csrf_token, "today": date.today().isoformat()}

    def dashboard_for(user):
        return "organiser_dashboard" if user["role"] == "organiser" else "customer_dashboard"

    @app.route("/")
    def index():
        user = current_user()
        return redirect(url_for("dashboard")) if user else redirect(url_for("login"))

    @app.route("/dashboard")
    def dashboard():
        user = current_user()
        if user is None:
            return redirect(url_for("login"))
        return redirect(url_for(dashboard_for(user)))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user():
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            user = authenticate(request.form.get("email", ""), request.form.get("password", ""))
            if user is None:
                flash("Email or password is incorrect.", "error")
            else:
                sign_in(user)
                flash(f"Welcome back, {user['name'].split()[0]}.", "success")
                return redirect(url_for("dashboard"))
        return render_template("login.html", page_title="Sign in")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if current_user():
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            try:
                user_id = register_user(
                    request.form.get("name", ""), request.form.get("email", ""),
                    request.form.get("password", ""), request.form.get("role", "customer"),
                )
                user = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
                sign_in(user)
                flash("Your account is ready.", "success")
                return redirect(url_for("dashboard"))
            except ValueError as error:
                flash(str(error), "error")
        return render_template("register.html", page_title="Create account")

    @app.post("/logout")
    def logout():
        sign_out()
        flash("You have signed out.", "success")
        return redirect(url_for("login"))

    @app.get("/organiser")
    @role_required("organiser")
    def organiser_dashboard():
        user = current_user()
        db = get_db()
        summary = db.execute(
            """SELECT COUNT(DISTINCT e.id) AS event_count,
                      COUNT(DISTINCT r.id) AS registrations,
                      COUNT(DISTINCT a.id) AS checkins,
                      COALESCE(SUM(e.capacity), 0) AS capacity
               FROM events e
               LEFT JOIN registrations r ON r.event_id = e.id
               LEFT JOIN event_attendance a ON a.event_id = e.id
               WHERE e.organiser_id = ?""", (user["id"],)
        ).fetchone()
        events = db.execute(
            """SELECT e.*, COUNT(DISTINCT r.id) AS registrations, COUNT(DISTINCT a.id) AS checkins
               FROM events e
               LEFT JOIN registrations r ON r.event_id = e.id
               LEFT JOIN event_attendance a ON a.event_id = e.id
               WHERE e.organiser_id = ?
               GROUP BY e.id ORDER BY e.date, e.time LIMIT 6""", (user["id"],)
        ).fetchall()
        logs = db.execute(
            "SELECT * FROM event_logs WHERE user_id = ? ORDER BY id DESC LIMIT 5", (user["id"],)
        ).fetchall()
        unread = db.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0",
            (user["id"],),
        ).fetchone()[0]
        return render_template("organiser_dashboard.html", page_title="Overview",
                               summary=summary, events=events, logs=logs, unread=unread)

    @app.get("/customer")
    @role_required("customer")
    def customer_dashboard():
        user = current_user()
        db = get_db()
        registrations = db.execute(
            """SELECT e.*, r.created_at AS registered_at,
                      a.checked_in_at,
                      CASE WHEN a.id IS NOT NULL THEN 1 ELSE 0 END AS is_checked_in
               FROM registrations r
               JOIN events e ON e.id = r.event_id
               LEFT JOIN event_attendance a ON a.event_id = e.id AND a.user_id = r.user_id
               WHERE r.user_id = ?
               ORDER BY e.date, e.time LIMIT 5""", (user["id"],)
        ).fetchall()
        event_count = db.execute(
            "SELECT COUNT(*) FROM registrations WHERE user_id = ?", (user["id"],)
        ).fetchone()[0]
        unread = db.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0",
            (user["id"],),
        ).fetchone()[0]
        upcoming = db.execute(
            "SELECT COUNT(*) FROM events WHERE status = 'active' AND date >= ?", (date.today().isoformat(),)
        ).fetchone()[0]
        return render_template("customer_dashboard.html", page_title="My activity",
                               registrations=registrations, event_count=event_count,
                               unread=unread, upcoming=upcoming)

    @app.get("/customer/events")
    @role_required("customer")
    def browse_events():
        search = request.args.get("q", "").strip()[:80]
        category = request.args.get("category", "").strip()[:40]
        query = """SELECT e.*, u.name AS organiser_name, COUNT(r.id) AS registrations,
                   MAX(CASE WHEN mine.user_id IS NULL THEN 0 ELSE 1 END) AS is_registered
                   FROM events e JOIN users u ON u.id = e.organiser_id
                   LEFT JOIN registrations r ON r.event_id = e.id
                   LEFT JOIN registrations mine ON mine.event_id = e.id AND mine.user_id = ?
                   WHERE e.status = 'active' AND e.date >= ?"""
        values = [current_user()["id"], date.today().isoformat()]
        if search:
            query += " AND (e.name LIKE ? OR e.description LIKE ? OR e.location LIKE ?)"
            values.extend([f"%{search}%"] * 3)
        if category:
            query += " AND e.category = ?"
            values.append(category)
        query += " GROUP BY e.id ORDER BY e.date, e.time"
        events = get_db().execute(query, values).fetchall()
        categories = get_db().execute(
            "SELECT DISTINCT category FROM events WHERE status = 'active' ORDER BY category"
        ).fetchall()
        return render_template("browse_events.html", page_title="Discover events",
                               events=events, categories=categories, search=search, category=category)

    @app.route("/organiser/events/new", methods=["GET", "POST"])
    @role_required("organiser")
    def create_event():
        if request.method == "POST":
            try:
                EventManager(get_db()).create(request.form, current_user()["id"])
                flash("Event created and added to the activity log.", "success")
                return redirect(url_for("organiser_dashboard"))
            except EventValidationError as error:
                flash(str(error), "error")
        return render_template("event_form.html", page_title="New event", event=None)

    @app.route("/organiser/events/<int:event_id>/edit", methods=["GET", "POST"])
    @role_required("organiser")
    def edit_event(event_id):
        event = get_db().execute(
            "SELECT * FROM events WHERE id = ? AND organiser_id = ?",
            (event_id, current_user()["id"]),
        ).fetchone()
        if event is None:
            abort(404)
        if request.method == "POST":
            try:
                EventManager(get_db()).update(event_id, request.form, current_user()["id"])
                flash("Event changes saved.", "success")
                return redirect(url_for("organiser_dashboard"))
            except EventValidationError as error:
                flash(str(error), "error")
        return render_template("event_form.html", page_title="Edit event", event=event)

    @app.post("/organiser/events/<int:event_id>/delete")
    @role_required("organiser")
    def delete_event(event_id):
        try:
            EventManager(get_db()).delete(event_id, current_user()["id"])
            flash("Event deleted. Its activity log has been retained.", "success")
        except EventValidationError as error:
            flash(str(error), "error")
        return redirect(url_for("organiser_dashboard"))

    @app.get("/organiser/events/<int:event_id>/registrations")
    @role_required("organiser")
    def event_registrations(event_id):
        event = get_db().execute(
            "SELECT * FROM events WHERE id = ? AND organiser_id = ?",
            (event_id, current_user()["id"]),
        ).fetchone()
        if event is None:
            abort(404)
        attendees = get_db().execute(
            """SELECT u.name, u.email, r.created_at,
                      a.checked_in_at, a.sensor_name
               FROM registrations r
               JOIN users u ON u.id = r.user_id
               LEFT JOIN event_attendance a ON a.event_id = r.event_id AND a.user_id = r.user_id
               WHERE r.event_id = ? ORDER BY r.created_at""",
            (event_id,),
        ).fetchall()
        return render_template("registrations.html", page_title="Attendees", event=event,
                               attendees=attendees)

    @app.post("/customer/events/<int:event_id>/register")
    @role_required("customer")
    def register_for_event(event_id):
        try:
            EventManager(get_db()).register(event_id, current_user()["id"])
            flash("You are registered. The organiser has been notified.", "success")
        except EventValidationError as error:
            flash(str(error), "error")
        return redirect(request.referrer or url_for("browse_events"))

    @app.post("/customer/events/<int:event_id>/cancel")
    @role_required("customer")
    def cancel_registration(event_id):
        try:
            EventManager(get_db()).cancel_registration(event_id, current_user()["id"])
            flash("Registration cancelled.", "success")
        except EventValidationError as error:
            flash(str(error), "error")
        return redirect(request.referrer or url_for("customer_dashboard"))

    @app.post("/customer/events/<int:event_id>/sensor-entry")
    @role_required("customer")
    def sensor_entry(event_id):
        try:
            EventManager(get_db()).mark_attendance(event_id, current_user()["id"], "IR")
            flash("IR entry logged. The organiser can see it on the dashboard.", "success")
        except EventValidationError as error:
            flash(str(error), "error")
        return redirect(request.referrer or url_for("customer_dashboard"))

    @app.get("/organiser/analytics")
    @role_required("organiser")
    def analytics_page():
        metrics = organiser_metrics(get_db(), current_user()["id"])
        return render_template("analytics.html", page_title="Performance", metrics=metrics)

    @app.post("/organiser/events/<int:event_id>/crowd-count")
    @role_required("organiser")
    def crowd_count_for_event(event_id):
        event = get_db().execute(
            "SELECT * FROM events WHERE id = ? AND organiser_id = ?",
            (event_id, current_user()["id"]),
        ).fetchone()
        if event is None:
            abort(404)
        image = request.files.get("image")
        if image is None or not image.filename:
            return jsonify({"error": "Upload a crowd image before counting people."}), 400
        count = estimate_people_in_image(image)
        with get_db():
            get_db().execute(
                """INSERT INTO event_crowd_counts (event_id, detected_count, image_name)
                   VALUES (?, ?, ?)
                   ON CONFLICT(event_id) DO UPDATE SET
                   detected_count = excluded.detected_count,
                   image_name = excluded.image_name,
                   created_at = CURRENT_TIMESTAMP""",
                (event_id, count, image.filename),
            )
        return jsonify({"count": count, "event_id": event_id})

    @app.post("/organiser/events/<int:event_id>/crowd-reset")
    @role_required("organiser")
    def reset_crowd_count(event_id):
        event = get_db().execute(
            "SELECT * FROM events WHERE id = ? AND organiser_id = ?",
            (event_id, current_user()["id"]),
        ).fetchone()
        if event is None:
            abort(404)
        with get_db():
            get_db().execute("DELETE FROM event_crowd_counts WHERE event_id = ?", (event_id,))
        return jsonify({"ok": True})

    @app.get("/organiser/reports.csv")
    @role_required("organiser")
    def download_report():
        content = event_report(get_db(), current_user()["id"])
        return send_file(BytesIO(content.encode("utf-8-sig")), mimetype="text/csv",
                         as_attachment=True, download_name="event-performance.csv")

    @app.get("/notifications")
    def notifications():
        user = current_user()
        if user is None:
            return redirect(url_for("login"))
        rows = get_db().execute(
            "SELECT * FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 50",
            (user["id"],),
        ).fetchall()
        with get_db():
            get_db().execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (user["id"],))
        return render_template("notifications.html", page_title="Notifications", notifications=rows)

    @app.get("/api/dashboard-stats")
    def dashboard_stats():
        user = current_user()
        if user is None:
            return jsonify({"error": "authentication required"}), 401
        db = get_db()
        unread = db.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0",
            (user["id"],),
        ).fetchone()[0]
        if user["role"] == "organiser":
            values = db.execute(
                """SELECT COUNT(DISTINCT e.id) AS events,
                          COUNT(DISTINCT r.id) AS registrations,
                          COUNT(DISTINCT a.id) AS checkins,
                          COALESCE(SUM(e.capacity), 0) AS capacity
                   FROM events e
                   LEFT JOIN registrations r ON r.event_id = e.id
                   LEFT JOIN event_attendance a ON a.event_id = e.id
                   WHERE e.organiser_id = ?""", (user["id"],)
            ).fetchone()
            return jsonify({"events": values["events"], "registrations": values["registrations"],
                            "capacity": values["capacity"], "checkins": values["checkins"],
                            "unread": unread})
        registrations = db.execute(
            "SELECT COUNT(*) FROM registrations WHERE user_id = ?", (user["id"],)
        ).fetchone()[0]
        upcoming = db.execute(
            "SELECT COUNT(*) FROM events WHERE status = 'active' AND date >= ?",
            (date.today().isoformat(),),
        ).fetchone()[0]
        return jsonify({"registrations": registrations, "upcoming": upcoming, "unread": unread})

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("error.html", page_title="Access denied", code=403,
                               message="This area is not available for your account."), 403

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html", page_title="Not found", code=404,
                               message="That page or event could not be found."), 404

    @app.errorhandler(400)
    def bad_request(error):
        message = getattr(error, "description", "The request could not be processed.")
        return render_template("error.html", page_title="Request rejected", code=400,
                               message=message), 400

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true")