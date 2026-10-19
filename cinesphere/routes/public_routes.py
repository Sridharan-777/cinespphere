from datetime import UTC, datetime

from bson import ObjectId
from flask import Blueprint, render_template, request

from cinesphere.models.database import get_db

public_bp = Blueprint("public", __name__)


def _local_availability(db, movies, city=None):
    today = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d")
    theater_query = {"status": "active"}
    if city:
        theater_query["city"] = city
    theater_ids = [item["_id"] for item in db.theaters.find(theater_query, {"_id": 1})]
    for movie in movies:
        show_query = {"movie_id": movie["_id"], "status": "active", "show_date": {"$gte": today}}
        if city:
            show_query["theater_id"] = {"$in": theater_ids}
        movie["local_show_count"] = db.showtimes.count_documents(show_query)
        movie["has_local_shows"] = movie["local_show_count"] > 0
    return movies


@public_bp.route("/")
def index():
    db = get_db()
    cities = sorted(city for city in db.theaters.distinct("city", {"status": "active"}) if city)
    selected_location = request.args.get("location", "").strip()
    if selected_location and selected_location not in cities:
        selected_location = ""

    trending = _local_availability(db, list(db.movies.find().sort("popularity", -1).limit(8)), selected_location or None)
    coming_soon = _local_availability(db, list(db.movies.find({"status": "Coming Soon"}).sort("release_date", 1).limit(8)), selected_location or None)
    today = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d")
    local_theater_ids = [item["_id"] for item in db.theaters.find({"status": "active", **({"city": selected_location} if selected_location else {})}, {"_id": 1})]
    show_query = {"status": "active", "show_date": {"$gte": today}}
    if selected_location:
        show_query["theater_id"] = {"$in": local_theater_ids}
    movie_ids = list(dict.fromkeys(show["movie_id"] for show in db.showtimes.find(show_query, {"movie_id": 1})))
    candidates = list(db.movies.find({"_id": {"$in": movie_ids}}).sort("popularity", -1)) if movie_ids else []
    now_showing = _local_availability(db, candidates, selected_location or None)[:8]
    featured_movie = next(iter(now_showing), None) or next(iter(trending), None)
    theaters_query = {"status": "active"}
    if selected_location:
        theaters_query["city"] = selected_location
    theaters = list(db.theaters.find(theaters_query).sort("name", 1).limit(6))
    return render_template("index.html", featured_movie=featured_movie, trending=trending,
        now_showing=now_showing, coming_soon=coming_soon, theaters=theaters,
        locations=cities, selected_location=selected_location)


@public_bp.route("/theaters")
def theaters_list():
    db = get_db()
    city = request.args.get("location", "").strip()
    query = {"status": "active"}
    if city:
        query["city"] = city
    theaters = list(db.theaters.find(query))
    for theater in theaters:
        theater["screen_count"] = db.screens.count_documents({"theater_id": theater["_id"], "status": "active"})
    return render_template("theaters.html", theaters=theaters)


@public_bp.route("/theaters/<theater_id>")
def theater_details(theater_id):
    db = get_db()
    try:
        theater = db.theaters.find_one({"_id": ObjectId(theater_id), "status": "active"})
    except Exception:
        theater = None
    if not theater:
        return render_template("errors/404.html", message="Theater not found"), 404
    screens = list(db.screens.find({"theater_id": theater["_id"], "status": "active"}))
    today = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d")
    showtimes = list(db.showtimes.find({"theater_id": theater["_id"], "status": "active",
                                       "show_date": {"$gte": today}}).sort("show_date", 1))
    for show in showtimes:
        show["movie"] = db.movies.find_one({"_id": show["movie_id"]})
        screen = next((item for item in screens if item["_id"] == show["screen_id"]), None)
        show["screen_name"] = screen["name"] if screen else "Screen"
    return render_template("theater_details.html", theater=theater, screens=screens, showtimes=showtimes)
