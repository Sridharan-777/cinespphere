from datetime import UTC, datetime
from bson import ObjectId
from cinesphere.models.database import get_db
from cinesphere.models.worker_model import count_workers

class TheaterModel:
    @staticmethod
    def create_theater(name, address, city, phone="", email="", facilities=None):
        db = get_db()
        doc = {
            "name": name.strip(),
            "address": address.strip(),
            "city": city.strip(),
            "phone": phone.strip(),
            "email": email.strip(),
            "facilities": facilities or ["Parking", "Dolby Atmos", "Food Court", "3D", "Wheelchair Access"],
            "staff_capacity": 5,
            "worker_capacity": 20,
            "status": "active",
            "created_at": datetime.now(UTC).replace(tzinfo=None),
            "updated_at": datetime.now(UTC).replace(tzinfo=None)
        }
        res = db.theaters.insert_one(doc)
        doc["_id"] = res.inserted_id
        return doc

    @staticmethod
    def get_staff_count(theater_id):
        db = get_db()
        return db.staff.count_documents({"theater_id": ObjectId(theater_id), "status": "active"})

    @staticmethod
    def get_worker_count(theater_id):
        db = get_db()
        return count_workers(theater_id, active_only=True)
