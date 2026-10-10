from datetime import datetime
from flask import request, session
from cinesphere.models.database import get_db

def log_audit(action, entity, entity_id=None, description=""):
    try:
        db = get_db()
        user_id = session.get("user_id")
        role = session.get("role", "system")
        email = session.get("email", "anonymous")
        ip = request.remote_addr if request else "127.0.0.1"
        
        audit_entry = {
            "user_id": str(user_id) if user_id else None,
            "user_email": email,
            "role": role,
            "action": action,
            "entity": entity,
            "entity_id": str(entity_id) if entity_id else None,
            "description": description,
            "ip_address": ip,
            "created_at": datetime.utcnow()
        }
        db.audit_logs.insert_one(audit_entry)
    except Exception as e:
        print(f"Audit log failed: {e}")

def create_notification(user_id, title, message, notif_type="info"):
    try:
        db = get_db()
        db.notifications.insert_one({
            "user_id": str(user_id),
            "title": title,
            "message": message,
            "type": notif_type, # info, success, warning, alert
            "is_read": False,
            "created_at": datetime.utcnow()
        })
    except Exception as e:
        print(f"Failed to create notification: {e}")
