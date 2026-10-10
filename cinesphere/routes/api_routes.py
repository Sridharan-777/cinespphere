from flask import Blueprint, jsonify, request
from bson import ObjectId
from cinesphere.models.database import get_db
from cinesphere.models.database import check_connection

api_bp = Blueprint("api", __name__)

@api_bp.route("/api/health/database")
def database_health():
    try:
        return jsonify({"status": "ok", **check_connection()})
    except Exception:
        return jsonify({"status": "unavailable", "connected": False}), 503

@api_bp.route("/api/movies")
def get_movies():
    db = get_db()
    movies = list(db.movies.find({}, {"_id": 1, "title": 1, "rating": 1, "poster_url": 1, "genres": 1, "status": 1}))
    for m in movies:
        m["_id"] = str(m["_id"])
    return jsonify({"success": True, "movies": movies})

@api_bp.route("/api/showtimes/<showtime_id>/seats")
def get_showtime_seats(showtime_id):
    db = get_db()
    try:
        showtime = db.showtimes.find_one({"_id": ObjectId(showtime_id)})
    except Exception:
        return jsonify({"success": False, "error": "Invalid showtime ID"}), 400

    if not showtime:
        return jsonify({"success": False, "error": "Showtime not found"}), 404

    booked_seats = showtime.get("booked_seats", [])
    screen = db.screens.find_one({"_id": showtime["screen_id"]})

    return jsonify({
        "success": True,
        "showtime_id": showtime_id,
        "base_price": showtime.get("base_price", 200),
        "screen_type": screen.get("screen_type", "Standard") if screen else "Standard",
        "booked_seats": booked_seats
    })

@api_bp.route("/api/theaters")
def get_theaters():
    db = get_db()
    theaters = list(db.theaters.find({"status": "active"}, {"_id": 1, "name": 1, "city": 1, "address": 1}))
    for t in theaters:
        t["_id"] = str(t["_id"])
    return jsonify({"success": True, "theaters": theaters})
