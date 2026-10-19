"""Small, dependency-free security helpers shared by the Flask application."""

import hmac
import secrets
from urllib.parse import urljoin, urlparse

from flask import abort, request, session


SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def csrf_token():
    """Return the session-bound CSRF token, creating it on first use."""
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def validate_csrf():
    """Reject unsafe browser requests that do not carry the session CSRF token."""
    if request.method in SAFE_METHODS:
        return
    expected = session.get("_csrf_token")
    supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not expected or not supplied or not hmac.compare_digest(str(expected), str(supplied)):
        abort(400, description="The form expired or could not be verified. Refresh and try again.")


def is_safe_redirect_target(target):
    """Only allow redirects back to the same origin."""
    if not target:
        return False
    host = urlparse(request.host_url)
    destination = urlparse(urljoin(request.host_url, target))
    return destination.scheme in {"http", "https"} and host.netloc == destination.netloc

