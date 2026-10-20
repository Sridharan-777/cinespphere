from flask import Blueprint, render_template, request
from bson import ObjectId
from urllib.parse import quote_plus
from cinesphere.models.database import get_db
from cinesphere.services.tmdb_service import tmdb_service
from cinesphere.services.availability_service import list_available_showtimes
from cinesphere.utils.media import youtube_embed_url, verified_trailer_for

movie_bp = Blueprint("movies", __name__)

@movie_bp.route("/movies")
def movie_list():
    db = get_db()
    status_filter = request.args.get("status", "all")
    genre_filter = request.args.get("genre", "all")
    lang_filter = request.args.get("lang", "all")
    region_filter = request.args.get("region", "all") # south or global
    sort_by = request.args.get("sort", "popularity")

    query = {"status": {"$ne": "Archived"}}
    if status_filter in {"Catalog", "Coming Soon", "Now Showing"}:
        query["status"] = status_filter
    if genre_filter != "all":
        query["genres"] = genre_filter
    if lang_filter != "all":
        # Match case-insensitively or exact language name
        query["$or"] = [
            {"language": lang_filter},
            {"language": lang_filter.capitalize()},
            {"language_code": lang_filter.lower()}
        ]
    if region_filter == "south":
        query["is_south_indian"] = True
    elif region_filter == "global":
        query["is_south_indian"] = False

    sort_order = [("popularity", -1)]
    if sort_by == "rating":
        sort_order = [("rating", -1)]
    elif sort_by == "release_date":
        sort_order = [("release_date", -1)]
    elif sort_by == "title":
        sort_order = [("title", 1)]

    movies = list(db.movies.find(query).sort(sort_order))

    # All distinct genres & languages for quick pills
    visible_query = {"status": {"$ne": "Archived"}}
    all_genres = sorted([g for g in db.movies.distinct("genres", visible_query) if g])
    all_languages = sorted([l for l in db.movies.distinct("language", visible_query) if l])
    
    # Pre-counted South Indian vs All
    south_indian_count = db.movies.count_documents({**visible_query, "is_south_indian": True})
    total_count = db.movies.count_documents(visible_query)

    return render_template("movies/movies.html",
                           movies=movies,
                           all_genres=all_genres,
                           all_languages=all_languages,
                           selected_status=status_filter,
                           selected_genre=genre_filter,
                           selected_lang=lang_filter,
                           selected_region=region_filter,
                           selected_sort=sort_by,
                           south_indian_count=south_indian_count,
                           total_count=total_count)

@movie_bp.route("/movies/<movie_id>")
def movie_details(movie_id):
    db = get_db()
    try:
        movie = db.movies.find_one({"_id": ObjectId(movie_id)})
    except Exception:
        movie = None
    if not movie:
        return render_template("errors/404.html", message="Movie not found"), 404

    showtimes = list_available_showtimes(db, {"movie_id": movie["_id"]})

    trailer_url = youtube_embed_url(movie.get("trailer_url")) or verified_trailer_for(movie.get("title"))
    if trailer_url and not movie.get("trailer_url"):
        db.movies.update_one({"_id": movie["_id"]}, {"$set": {"trailer_url": trailer_url}})
    if not trailer_url and movie.get("tmdb_id") and tmdb_service.is_configured:
        details = tmdb_service.get_movie_details(movie["tmdb_id"])
        refreshed = tmdb_service.format_movie_data(details) if details else None
        trailer_url = youtube_embed_url((refreshed or {}).get("trailer_url"))
        if trailer_url:
            db.movies.update_one({"_id": movie["_id"]}, {"$set": {"trailer_url": trailer_url}})
            movie["trailer_url"] = trailer_url

    # Reviews & Ratings
    reviews = list(db.reviews.find({"movie_id": movie["_id"], "status": "Published"}).sort("created_at", -1))
    
    # Rating summary distribution
    total_reviews = len(reviews)
    rating_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for r in reviews:
        star = int(r.get("rating", 5))
        if 1 <= star <= 5:
            rating_counts[star] += 1
            
    rating_bars = {}
    for star in range(5, 0, -1):
        pct = round((rating_counts[star] / total_reviews * 100), 1) if total_reviews > 0 else 0
        rating_bars[star] = {"count": rating_counts[star], "pct": pct}

    return render_template("movies/movie_details.html",
                           movie=movie,
                           showtimes=showtimes,
                           reviews=reviews,
                           trailer_url=trailer_url,
                           trailer_search_url=f"https://www.youtube.com/results?search_query={quote_plus(movie.get('title', '') + ' official trailer')}",
                           rating_bars=rating_bars,
                           total_reviews=total_reviews)

@movie_bp.route("/actors/<int:actor_id>")
def actor_details(actor_id):
    # Fetch from TMDB
    actor_data = tmdb_service.get_actor_details(actor_id)
    if not actor_data:
        # Fallback search from local movie cast
        db = get_db()
        movie_with_cast = db.movies.find_one({"cast.id": actor_id})
        if movie_with_cast:
            cast_member = next((c for c in movie_with_cast.get("cast", []) if c.get("id") == actor_id), None)
            actor_data = {
                "name": cast_member.get("name") if cast_member else "Actor",
                "biography": "Information currently synchronized from TMDB cast database.",
                "birthday": "N/A",
                "place_of_birth": "India / Global",
                "profile_path": cast_member.get("profile_path"),
                "movie_credits": {"cast": []}
            }
        else:
            return render_template("errors/404.html", message="Actor profile not found"), 404

    profile_img = f"https://image.tmdb.org/t/p/h632{actor_data.get('profile_path')}" if actor_data.get("profile_path") else "/static/images/default_actor.svg"
    credits = actor_data.get("movie_credits", {}).get("cast", [])[:12]

    return render_template("movies/actor_details.html",
                           actor=actor_data,
                           profile_img=profile_img,
                           known_movies=credits)

@movie_bp.route("/search")
def search():
    query = request.args.get("q", "").strip()
    db = get_db()
    movies = []
    if query:
        # Regex search across title, language, cast name, genres, director
        movies = list(db.movies.find({
            "status": {"$ne": "Archived"},
            "$or": [
                {"title": {"$regex": query, "$options": "i"}},
                {"original_title": {"$regex": query, "$options": "i"}},
                {"language": {"$regex": query, "$options": "i"}},
                {"genres": {"$regex": query, "$options": "i"}},
                {"cast.name": {"$regex": query, "$options": "i"}},
                {"director": {"$regex": query, "$options": "i"}}
            ]
        }))
    return render_template("movies/search.html", query=query, movies=movies)
