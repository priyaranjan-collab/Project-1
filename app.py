"""Flask entry point for the event-driven production monitoring dashboard."""
from datetime import date
from functools import wraps
from io import BytesIO
import os

from flask import (Flask, abort, flash, jsonify, redirect, render_template, request,
                   send_file, url_for)

from analytics import organiser_metrics
from auth import authenticate, current_user, register_user, role_required
from database import close_db, get_db, init_db
from event_manager import EventManager, EventValidationError
from report_generator import event_report
from session_manager import csrf_token, sign_in, sign_out, validate_csrf


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
            """SELECT COUNT(DISTINCT e.id) AS event_count, COUNT(r.id) AS registrations,
                      COALESCE(SUM(e.capacity), 0) AS capacity
               FROM events e LEFT JOIN registrations r ON r.event_id = e.id
               WHERE e.organiser_id = ?""", (user["id"],)
        ).fetchone()
        events = db.execute(
            """SELECT e.*, COUNT(r.id) AS registrations FROM events e
               LEFT JOIN registrations r ON r.event_id = e.id WHERE e.organiser_id = ?
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
            """SELECT e.*, r.created_at AS registered_at FROM registrations r
               JOIN events e ON e.id = r.event_id WHERE r.user_id = ?
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
            """SELECT u.name, u.email, r.created_at FROM registrations r
               JOIN users u ON u.id = r.user_id WHERE r.event_id = ? ORDER BY r.created_at""",
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

    @app.get("/organiser/analytics")
    @role_required("organiser")
    def analytics_page():
        metrics = organiser_metrics(get_db(), current_user()["id"])
        return render_template("analytics.html", page_title="Performance", metrics=metrics)

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
                """SELECT COUNT(DISTINCT e.id) AS events, COUNT(r.id) AS registrations,
                          COALESCE(SUM(e.capacity), 0) AS capacity
                   FROM events e LEFT JOIN registrations r ON r.event_id = e.id
                   WHERE e.organiser_id = ?""", (user["id"],)
            ).fetchone()
            return jsonify({"events": values["events"], "registrations": values["registrations"],
                            "capacity": values["capacity"], "unread": unread})
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