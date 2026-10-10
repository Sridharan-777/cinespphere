import sys
import getpass
from werkzeug.security import generate_password_hash
from cinesphere.models.database import get_db

def create_admin():
    print("=== CineSphere Admin Account Creator ===")
    email = input("Admin Email: ").strip() or "admin@cinesphere.com"
    name = input("Admin Name: ").strip() or "System Administrator"
    password = getpass.getpass("Admin Password (default Admin@1234): ") or "Admin@1234"

    db = get_db()
    existing = db.users.find_one({"email": email.lower()})
    if existing:
        db.users.update_one(
            {"_id": existing["_id"]},
            {"$set": {"password_hash": generate_password_hash(password), "role": "admin", "status": "active"}}
        )
        print(f"Updated existing user {email} to admin role with new password.")
    else:
        db.users.insert_one({
            "name": name,
            "email": email.lower(),
            "phone": "+91 9999900000",
            "password_hash": generate_password_hash(password),
            "role": "admin",
            "status": "active"
        })
        print(f"Admin {email} created successfully.")

if __name__ == "__main__":
    create_admin()
