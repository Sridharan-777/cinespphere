from flask import Blueprint, render_template, request
from bson import ObjectId
from cinesphere.models.database import get_db

public_bp = Blueprint("public", __name__)

@public_bp.route("/")
def index():
    db = get_db()
    # Featured Hero movie (Prefer RRR or Kalki or Inception)
    featured_movie = db.movies.find_one({"title": "RRR"})
    if not featured_movie:
        featured_movie = db.movies.find_one({"status": "Now Showing"})
    if not featured_movie:
        featured_movie = db.movies.find_one()

    # Now Showing
    now_showing = list(db.movies.find({"status": "Now Showing"}).limit(8))
    
    # South Indian Cinema Showcase
    south_indian_movies = list(db.movies.find({"is_south_indian": True, "status": "Now Showing"}).limit(8))
    
    # Top Rated Across All Genres
    top_rated = list(db.movies.find().sort("rating", -1).limit(6))
    
    # Active theaters
    theaters = list(db.theaters.find({"status": "active"}).limit(4))

    return render_template("index.html",
                           featured_movie=featured_movie,
                           now_showing=now_showing,
                           south_indian_movies=south_indian_movies,
                           top_rated=top_rated,
                           theaters=theaters)

@public_bp.route("/theaters")
def theaters_list():
    db = get_db()
    theaters = list(db.theaters.find({"status": "active"}))
    for t in theaters:
        t["screen_count"] = db.screens.count_documents({"theater_id": t["_id"], "status": "active"})
    return render_template("theaters.html", theaters=theaters)

@public_bp.route("/theaters/<theater_id>")
def theater_details(theater_id):
    db = get_db()
    theater = db.theaters.find_one({"_id": ObjectId(theater_id)})
    if not theater:
        return render_template("errors/404.html", message="Theater not found"), 404

    screens = list(db.screens.find({"theater_id": theater["_id"], "status": "active"}))
    showtimes = list(db.showtimes.find({"theater_id": theater["_id"], "status": "active"}).sort("show_date", 1))

    # Enrich showtimes with movie info
    for show in showtimes:
        movie = db.movies.find_one({"_id": show["movie_id"]})
        show["movie"] = movie
        screen = next((s for s in screens if s["_id"] == show["screen_id"]), None)
        show["screen_name"] = screen["name"] if screen else "Screen 1"

    return render_template("theater_details.html", theater=theater, screens=screens, showtimes=showtimes)
