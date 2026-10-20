import os
import re
import sys
from datetime import UTC, datetime, timedelta

from cinesphere.models.database import get_db
from cinesphere.services.tmdb_service import tmdb_service


INDIAN_LANGUAGES = {"ta", "te", "ml", "kn", "hi"}


def _normalise_title(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _best_search_match(query, results):
    wanted = _normalise_title(query)
    exact = [item for item in results if wanted in {
        _normalise_title(item.get("title")), _normalise_title(item.get("original_title"))}]
    if exact:
        return exact[0]
    regional = [item for item in results if item.get("original_language") in INDIAN_LANGUAGES]
    return regional[0] if regional else (results[0] if results else None)


def sync_movies(search_queries=None, db=None):
    """Synchronize recent/upcoming Indian cinema plus explicitly requested titles."""
    if not tmdb_service.is_configured:
        return {"synced": 0, "error": "TMDB_API_KEY is not configured.", "requested_missing": []}

    db = db or get_db()
    today = datetime.now(UTC).date()
    recent_days = max(1, int(os.getenv("RECENT_RELEASE_DAYS", "120")))
    pages = max(1, min(10, int(os.getenv("TMDB_SYNC_PAGES", "3"))))
    recent_start = (today - timedelta(days=recent_days)).isoformat()
    today_text = today.isoformat()
    future_end = (today + timedelta(days=365)).isoformat()

    catalog = {}

    def collect(items, source):
        for raw in items:
            tmdb_id = raw.get("id")
            if not tmdb_id:
                continue
            entry = catalog.setdefault(tmdb_id, {"raw": raw, "sources": set()})
            entry["sources"].add(source)

    for page in range(1, pages + 1):
        collect(tmdb_service.discover_indian_movies(recent_start, today_text, page, True), "india_recent")
        collect(tmdb_service.discover_indian_movies(today_text, future_end, page, False), "india_upcoming")
        collect(tmdb_service.get_now_playing_movies(page, "IN"), "india_now_playing")
        collect(tmdb_service.get_upcoming_movies(page, "IN"), "india_upcoming_provider")

    # Popular discovery helps retain major Indian releases that land just outside
    # a date page, while the release-window check below prevents an unbounded import.
    collect(tmdb_service.get_popular_movies(page=1), "popular")

    requested_missing = []
    for query in list(search_queries or [])[:10]:
        query = str(query).strip()
        if not query:
            continue
        match = _best_search_match(query, tmdb_service.search_movies(query, page=1))
        if match:
            collect([match], "operator_search")
        else:
            requested_missing.append(query)

    synced = 0
    now = datetime.now(UTC).replace(tzinfo=None)
    for tmdb_id, entry in catalog.items():
        raw = entry["raw"]
        release_date = raw.get("release_date") or ""
        is_requested = "operator_search" in entry["sources"]
        is_regional = raw.get("original_language") in INDIAN_LANGUAGES
        is_india_release = bool(entry["sources"].intersection({"india_now_playing", "india_upcoming_provider"}))
        in_window = not release_date or recent_start <= release_date <= future_end
        if not is_requested and (not in_window or not (is_regional or is_india_release)):
            continue

        full_detail = tmdb_service.get_movie_details(tmdb_id) or raw
        formatted = tmdb_service.format_movie_data(full_detail)
        if not formatted or not formatted.get("title"):
            continue
        formatted["provider_lists"] = sorted(entry["sources"])
        formatted["status"] = "Coming Soon" if release_date and release_date > today_text else "Catalog"
        formatted["metadata_synced_at"] = now

        existing = db.movies.find_one({"tmdb_id": tmdb_id}, {"_id": 1, "created_at": 1})
        if existing:
            db.movies.update_one({"_id": existing["_id"]}, {"$set": formatted})
        else:
            formatted["created_at"] = now
            db.movies.insert_one(formatted)
        synced += 1

    return {"synced": synced, "error": None, "requested_missing": requested_missing}


if __name__ == "__main__":
    result = sync_movies(sys.argv[1:])
    if result["error"]:
        raise SystemExit(result["error"])
    print(f"Synchronized {result['synced']} verified TMDB movies.")
    if result["requested_missing"]:
        print("No TMDB match for: " + ", ".join(result["requested_missing"]))
