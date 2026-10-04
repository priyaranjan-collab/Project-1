"""Small session and CSRF-token helpers used by the Flask app."""
import hmac
import secrets

from flask import abort, session


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def validate_csrf(submitted):
    expected = session.get("csrf_token", "")
    if not expected or not submitted or not hmac.compare_digest(expected, submitted):
        abort(400, description="The form expired. Reload the page and try again.")


def sign_in(user):
    session.clear()
    session["user_id"] = user["id"]
    csrf_token()


def sign_out():
    session.clear()