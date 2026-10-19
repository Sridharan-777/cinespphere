from datetime import UTC, datetime
import os
from datetime import timedelta

from cinesphere.models.database import get_db
from cinesphere.services.tmdb_service import tmdb_service

def sync_movies():
    print("=== Synchronizing Real Movies from TMDB API ===")
    db = get_db()
    popular = tmdb_service.get_popular_movies(page=1)
    now_playing = tmdb_service.get_now_playing_movies(page=1)
    upcoming = tmdb_service.get_upcoming_movies(page=1)
    
    unique_movies = {m["id"]: m for m in (now_playing + popular + upcoming)}.values()
    now_playing_ids = {movie["id"] for movie in now_playing}
    upcoming_ids = {movie["id"] for movie in upcoming}
    recent_cutoff = (datetime.now(UTC).date() - timedelta(
        days=max(1, int(os.getenv("RECENT_RELEASE_DAYS", "120"))))).isoformat()
    synced = 0

    for raw in unique_movies:
        tmdb_id = raw.get("id")
        release_date = raw.get("release_date") or ""
        # Provider discovery does not make a title locally available, and the
        # configurable window prevents an unbounded "recent" catalog.
        if tmdb_id not in upcoming_ids and release_date and release_date < recent_cutoff:
            continue
        full_detail = tmdb_service.get_movie_details(tmdb_id) or raw
        formatted = tmdb_service.format_movie_data(full_detail)
        if not formatted:
            continue
        formatted["provider_lists"] = [name for name, included in {
            "now_playing": tmdb_id in now_playing_ids, "upcoming": tmdb_id in upcoming_ids,
            "popular": any(item.get("id") == tmdb_id for item in popular)}.items() if included]
        # Provider lists describe release/discovery state, never CineSphere local availability.
        formatted["status"] = "Coming Soon" if tmdb_id in upcoming_ids and tmdb_id not in now_playing_ids else "Catalog"
        formatted["metadata_synced_at"] = datetime.now(UTC).replace(tzinfo=None)

        existing = db.movies.find_one({"tmdb_id": tmdb_id})
        if existing:
            db.movies.update_one({"tmdb_id": tmdb_id}, {"$set": formatted})
        else:
            formatted["created_at"] = datetime.now(UTC).replace(tzinfo=None)
            db.movies.insert_one(formatted)
        synced += 1

    print(f"Successfully synchronized {synced} real movies into MongoDB!")

if __name__ == "__main__":
    sync_movies()
