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
        db.reviews.create_index([("movie_id", 1), ("customer_id", 1)])
        db.tasks.create_index("worker_id")
        db.tasks.create_index("theater_id")
        db.notifications.create_index("user_id")
        db.audit_logs.create_index("created_at")
        # Backfill legacy worker documents created before payroll fields were introduced.
        db.workers.update_many(
            {"monthly_salary": {"$exists": False}},
            {"$set": {"monthly_salary": 22000, "hourly_rate": 137.5, "shift_timings": "09:00 AM - 05:30 PM"}}
        )
    except Exception as e:
        app.logger.error("MongoDB initialization failed: %s", e)
        raise RuntimeError("CineSphere could not connect to MongoDB. Start MongoDB and verify MONGO_URI.") from e
    return db

def check_connection(app=None):
    """Return database health information for diagnostics and deployment checks."""
    db = get_db(app)
    _client.admin.command("ping")
    return {"connected": True, "database": db.name}
