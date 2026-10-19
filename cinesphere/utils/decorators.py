from functools import wraps
from bson import ObjectId
from flask import session, redirect, url_for, flash, abort, request, g

from cinesphere.models.database import get_db


VALID_ROLES = {"customer", "worker", "staff", "admin"}


def _authenticated_user():
    """Load the current account from the database instead of trusting session role data."""
    user_id = session.get("user_id")
    if not user_id:
        return None
    try:
        user = get_db().users.find_one({"_id": ObjectId(user_id)})
    except Exception:
        user = None
    if not user or user.get("status") != "active" or user.get("role") not in VALID_ROLES:
        session.clear()
        return None
    g.current_user = user
    # Keep display data current while authority always comes from the database record.
    session["role"] = user["role"]
    session["name"] = user.get("name", "Account")
    return user

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _authenticated_user():
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("auth.login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def role_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = _authenticated_user()
            if not user:
                flash("Please log in to continue.", "warning")
                return redirect(url_for("auth.login", next=request.url))
            user_role = user.get("role")
            if user_role not in allowed_roles:
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator
