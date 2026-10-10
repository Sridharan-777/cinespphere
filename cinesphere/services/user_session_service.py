from datetime import UTC, datetime
from bson import ObjectId
from flask import request, session
from cinesphere.models.database import get_db


def start_user_session(user, login_method="password"):
    """Create one auditable record for this login and link it to Flask's session."""
    now = datetime.now(UTC).replace(tzinfo=None)
    user_agent = request.headers.get("User-Agent", "Unknown")[:500]
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    ip_address = forwarded_for.split(",")[0].strip() if forwarded_for else (request.remote_addr or "Unknown")
    result = get_db().user_sessions.insert_one({
        "user_id": user["_id"],
        "name": user.get("name", "Unknown"),
        "email": user.get("email", ""),
        "role": user.get("role", "customer"),
        "login_method": login_method,
        "login_at": now,
        "last_seen_at": now,
        "logout_at": None,
        "status": "active",
        "ip_address": ip_address,
        "user_agent": user_agent,
    })
    session["session_record_id"] = str(result.inserted_id)
    return result.inserted_id


def close_user_session():
    record_id = session.get("session_record_id")
    if not record_id:
        return
    try:
        object_id = ObjectId(record_id)
    except Exception:
        return
    now = datetime.now(UTC).replace(tzinfo=None)
    get_db().user_sessions.update_one(
        {"_id": object_id, "status": "active"},
        {"$set": {"logout_at": now, "last_seen_at": now, "status": "logged_out"}},
    )
