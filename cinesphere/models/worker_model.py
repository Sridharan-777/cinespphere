from bson import ObjectId

from cinesphere.models.database import get_db


def worker_query(theater_id=None, active_only=False):
    query = {"role": "worker", "worker_profile": {"$exists": True}}
    if theater_id is not None:
        try:
            query["worker_profile.theater_id"] = ObjectId(theater_id)
        except Exception:
            return None
    if active_only:
        query["status"] = "active"
    return query


def as_worker(user):
    """Expose an embedded worker profile in the shape expected by existing views."""
    if not user:
        return None
    worker = dict(user.get("worker_profile") or {})
    worker.update({
        "_id": user["_id"],
        "user_id": user["_id"],
        "name": user.get("name", ""),
        "email": user.get("email", ""),
        "phone": user.get("phone", ""),
        "status": user.get("status", "inactive"),
        "created_at": user.get("created_at"),
    })
    return worker


def find_worker(user_id, theater_id=None, active_only=False):
    query = worker_query(theater_id, active_only)
    if query is None:
        return None
    try:
        query["_id"] = ObjectId(user_id)
    except Exception:
        return None
    return as_worker(get_db().users.find_one(query))


def list_workers(theater_id=None, active_only=False, limit=None):
    query = worker_query(theater_id, active_only)
    if query is None:
        return []
    cursor = get_db().users.find(query).sort("created_at", -1)
    if limit:
        cursor = cursor.limit(limit)
    return [as_worker(user) for user in cursor]


def count_workers(theater_id=None, active_only=False):
    query = worker_query(theater_id, active_only)
    return get_db().users.count_documents(query) if query is not None else 0


def update_worker(user_id, fields):
    updates = {f"worker_profile.{key}": value for key, value in fields.items()}
    return get_db().users.update_one({"_id": ObjectId(user_id), "role": "worker"}, {"$set": updates})
