from datetime import UTC, datetime


def today_key():
    return datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d")


def enrich_available_showtime(db, show):
    """Return an enriched show only when its venue, screen, movie and seats are available."""
    theater = db.theaters.find_one({"_id": show.get("theater_id"), "status": "active"})
    screen = db.screens.find_one({
        "_id": show.get("screen_id"),
        "theater_id": show.get("theater_id"),
        "status": "active",
    })
    movie = db.movies.find_one({"_id": show.get("movie_id"), "status": {"$ne": "Archived"}})
    if not theater or not screen or not movie:
        return None

    capacity = max(0, int(screen.get("capacity", 0) or 0))
    available_seats = max(0, capacity - len(set(show.get("booked_seats", []))))
    if capacity <= 0 or available_seats <= 0:
        return None

    enriched = dict(show)
    enriched.update({
        "theater": theater,
        "theater_name": theater.get("name", "CineSphere"),
        "theater_city": theater.get("city", ""),
        "screen": screen,
        "screen_name": screen.get("name", "Screen"),
        "screen_type": screen.get("screen_type", "Standard"),
        "movie": movie,
        "available_seats": available_seats,
        "available_seats_count": available_seats,
    })
    return enriched


def list_available_showtimes(db, query=None, limit=None):
    show_query = {"status": "active", "show_date": {"$gte": today_key()}, **(query or {})}
    cursor = db.showtimes.find(show_query).sort([("show_date", 1), ("start_time", 1)])
    results = []
    for show in cursor:
        enriched = enrich_available_showtime(db, show)
        if enriched:
            results.append(enriched)
            if limit and len(results) >= limit:
                break
    return results


def list_available_theaters(db, city=None, limit=None):
    theater_query = {"status": "active"}
    if city:
        theater_query["city"] = city
    cursor = db.theaters.find(theater_query).sort("name", 1)
    results = []
    for theater in cursor:
        screens = list(db.screens.find({"theater_id": theater["_id"], "status": "active"}))
        if not screens:
            continue
        shows = list_available_showtimes(db, {"theater_id": theater["_id"]})
        if not shows:
            continue
        item = dict(theater)
        item["screen_count"] = len(screens)
        item["upcoming_show_count"] = len(shows)
        item["next_show_date"] = shows[0].get("show_date")
        results.append(item)
        if limit and len(results) >= limit:
            break
    return results
