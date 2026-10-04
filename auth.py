"""Authentication helpers and role-based route protection."""
from functools import wraps

from flask import abort, g, redirect, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from database import get_db


def authenticate(email, password):
    user = get_db().execute(
        "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
    ).fetchone()
    if user is None or not check_password_hash(user["password"], password):
        return None
    return user


def register_user(name, email, password, role="customer"):
    name = name.strip()
    email = email.strip().lower()
    if len(name) < 2 or len(name) > 80:
        raise ValueError("Name must be between 2 and 80 characters.")
    if len(email) > 254 or "@" not in email:
        raise ValueError("Enter a valid email address.")
    if len(password) < 8:
        raise ValueError("Password must contain at least 8 characters.")
    if role not in ("organiser", "customer"):
        raise ValueError("Choose a valid account role.")
    try:
        cursor = get_db().execute(
            "INSERT INTO users (name, email, password, role) VALUES (?, ?, ?, ?)",
            (name, email, generate_password_hash(password), role),
        )
        get_db().commit()
        return cursor.lastrowid
    except Exception as error:
        get_db().rollback()
        if "UNIQUE constraint failed" in str(error):
            raise ValueError("An account with that email already exists.") from error
        raise


def current_user():
    if "current_user" not in g:
        user_id = session.get("user_id")
        g.current_user = get_db().execute(
            "SELECT id, name, email, role FROM users WHERE id = ?", (user_id,)
        ).fetchone() if user_id else None
    return g.current_user


def role_required(role):
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if user is None:
                return redirect(url_for("login"))
            if user["role"] != role:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorate