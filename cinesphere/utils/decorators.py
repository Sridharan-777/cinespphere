from functools import wraps
from flask import session, redirect, url_for, flash, abort, request

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("auth.login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def role_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get("user_id"):
                flash("Please log in to continue.", "warning")
                return redirect(url_for("auth.login", next=request.url))
            user_role = session.get("role")
            if user_role not in allowed_roles:
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator
