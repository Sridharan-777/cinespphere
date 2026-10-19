from pymongo import MongoClient
import os

_client = None
_db = None

def get_db(app=None):
    global _client, _db
    if _db is not None:
        return _db
    
    uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
    db_name = os.getenv("DATABASE_NAME", "cinesphere")
    
    if app:
        uri = app.config.get("MONGO_URI", uri)
        db_name = app.config.get("DATABASE_NAME", db_name)
        
    _client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    _db = _client[db_name]
    return _db

def init_db(app):
    db = get_db(app)
    # Ensure indexes
    try:
        _client.admin.command("ping")
        app.logger.info("MongoDB connected: %s", app.config.get("DATABASE_NAME", "cinesphere"))
        db.users.create_index("email", unique=True)
        db.users.create_index("role")
        db.movies.create_index("tmdb_id", unique=True, sparse=True)
        db.movies.create_index("title")
        db.showtimes.create_index([("movie_id", 1), ("theater_id", 1), ("screen_id", 1), ("show_date", 1)])
        db.bookings.create_index("booking_code", unique=True)
        db.bookings.create_index("customer_id")
        db.bookings.create_index("showtime_id")
        db.bookings.create_index("confirmation_key", unique=True, sparse=True)
        db.payments.create_index("transaction_id", unique=True, sparse=True)
        db.payment_intents.create_index("provider_order_id", unique=True)
        db.payment_events.create_index("event_id", unique=True)
        db.seat_holds.create_index([("showtime_id", 1), ("seat_number", 1)], unique=True)
        db.seat_holds.create_index("expires_at", expireAfterSeconds=0)
        db.reward_awards.create_index([("worker_id", 1), ("cycle", 1)], unique=True)
        db.reward_credits.create_index([("award_id", 1), ("sequence", 1)], unique=True)
        db.payroll.create_index([("worker_id", 1), ("period", 1)], unique=True)
        db.reviews.create_index([("movie_id", 1), ("customer_id", 1)])
        db.tasks.create_index("assigned_worker_id")
        db.tasks.create_index("theater_id")
        db.attendance.create_index([("worker_id", 1), ("date", 1)], unique=True)
        db.users.create_index([("role", 1), ("worker_profile.theater_id", 1), ("status", 1)])
        db.notifications.create_index("user_id")
        db.audit_logs.create_index("created_at")
        db.login_attempts.create_index("created_at", expireAfterSeconds=900)
        db.customer_theater_access.create_index([("customer_id", 1), ("theater_id", 1)], unique=True)
        # One-time compatibility migration: embed legacy worker records in users.
        for worker in db.workers.find({}):
            user_id = worker.get("user_id")
            if not user_id:
                continue
            profile = {key: value for key, value in worker.items()
                       if key not in {"_id", "user_id", "name", "email", "phone", "status", "created_at"}}
            profile.setdefault("monthly_salary", 22000)
            profile.setdefault("hourly_rate", 137.5)
            profile.setdefault("shift_timings", "09:00 AM - 05:30 PM")
            result = db.users.update_one({"_id": user_id, "role": "worker"}, {"$set": {"worker_profile": profile}})
            db.tasks.update_many({"assigned_worker_id": worker["_id"]}, {"$set": {"assigned_worker_id": user_id}})
            db.attendance.update_many({"worker_id": worker["_id"]}, {"$set": {"worker_id": user_id}})
            if result.matched_count:
                db.workers.delete_one({"_id": worker["_id"]})
    except Exception as e:
        app.logger.error("MongoDB initialization failed: %s", e)
        raise RuntimeError("CineSphere could not connect to MongoDB. Start MongoDB and verify MONGO_URI.") from e
    return db

def check_connection(app=None):
    """Return database health information for diagnostics and deployment checks."""
    db = get_db(app)
    _client.admin.command("ping")
    return {"connected": True, "database": db.name}
