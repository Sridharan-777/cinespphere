from datetime import UTC, datetime
from bson import ObjectId
from werkzeug.security import generate_password_hash, check_password_hash
from cinesphere.models.database import get_db

class UserModel:
    @staticmethod
    def create_user(name, email, password=None, role="customer", phone="", tier="Free", google_id=None):
        db = get_db()
        email = email.strip().lower()
        if db.users.find_one({"email": email}):
            return None, "User with this email already exists."
        
        user_doc = {
            "name": name.strip(),
            "email": email,
            "phone": phone.strip(),
            "password_hash": generate_password_hash(password) if password else None,
            "role": role, # admin, staff, worker, customer
            "tier": tier if role == "customer" else None, # Free, Plus, Pro
            "google_id": google_id,
            "status": "active", # active, inactive
            "profile_image": None,
            "created_at": datetime.now(UTC).replace(tzinfo=None),
            "updated_at": datetime.now(UTC).replace(tzinfo=None),
            "last_login": None
        }
        res = db.users.insert_one(user_doc)
        user_doc["_id"] = res.inserted_id
        return user_doc, None

    @staticmethod
    def authenticate(email, password):
        db = get_db()
        email = email.strip().lower()
        user = db.users.find_one({"email": email})
        if not user or not user.get("password_hash") or not check_password_hash(user["password_hash"], password):
            return None, "Invalid email or password."
        if user.get("status") != "active":
            return None, "Your account is deactivated. Please contact support."
        
        # Update last login
        db.users.update_one({"_id": user["_id"]}, {"$set": {"last_login": datetime.now(UTC).replace(tzinfo=None)}})
        return user, None
