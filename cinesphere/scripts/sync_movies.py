from datetime import datetime
from cinesphere.models.database import get_db
from cinesphere.services.tmdb_service import tmdb_service

def sync_movies():
    print("=== Synchronizing Real Movies from TMDB API ===")
    db = get_db()
    popular = tmdb_service.get_popular_movies(page=1)
    now_playing = tmdb_service.get_now_playing_movies(page=1)
    upcoming = tmdb_service.get_upcoming_movies(page=1)
    
    unique_movies = {m["id"]: m for m in (now_playing + popular + upcoming)}.values()
    synced = 0

    for raw in unique_movies:
        tmdb_id = raw.get("id")
        full_detail = tmdb_service.get_movie_details(tmdb_id) or raw
        formatted = tmdb_service.format_movie_data(full_detail)
        if not formatted:
            continue

        existing = db.movies.find_one({"tmdb_id": tmdb_id})
        if existing:
            db.movies.update_one({"tmdb_id": tmdb_id}, {"$set": formatted})
        else:
            formatted["created_at"] = datetime.utcnow()
            db.movies.insert_one(formatted)
        synced += 1

    print(f"Successfully synchronized {synced} real movies into MongoDB!")

if __name__ == "__main__":
    sync_movies()
