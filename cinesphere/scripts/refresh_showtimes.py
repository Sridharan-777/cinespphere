"""Create bookable future showtimes without changing historical booking data."""

import argparse
from datetime import UTC, datetime, timedelta

from cinesphere.models.database import get_db


SHOW_SLOTS = (
    ("10:30 AM", "01:30 PM", 180),
    ("02:15 PM", "05:15 PM", 220),
    ("06:30 PM", "09:30 PM", 280),
)


def refresh_showtimes(db=None, days=7):
    """Add missing future shows for every active screen and return a summary."""
    db = db or get_db()
    days = max(1, min(int(days), 31))
    now = datetime.now(UTC).replace(tzinfo=None)
    start_date = now.date() + timedelta(days=1)

    movies = list(db.movies.find({"status": {"$in": ["Now Showing", "Catalog"]}}).sort([
        ("popularity", -1), ("release_date", -1), ("title", 1)
    ]))
    if not movies:
        movies = list(db.movies.find({"status": {"$ne": "Archived"}}).sort("title", 1))
    if not movies:
        return {"created": 0, "skipped": 0, "error": "No active catalog movies are available."}

    created = 0
    skipped = 0
    active_theaters = list(db.theaters.find({"status": "active"}).sort("name", 1))
    for theater_index, theater in enumerate(active_theaters):
        screens = list(db.screens.find({
            "theater_id": theater["_id"], "status": "active"
        }).sort("name", 1))
        for screen_index, screen in enumerate(screens):
            movie_offset = theater_index * 3 + screen_index
            for day_offset in range(days):
                show_date = (start_date + timedelta(days=day_offset)).isoformat()
                movie = movies[(movie_offset + day_offset) % len(movies)]
                for start_time, end_time, base_price in SHOW_SLOTS:
                    identity = {
                        "theater_id": theater["_id"],
                        "screen_id": screen["_id"],
                        "show_date": show_date,
                        "start_time": start_time,
                    }
                    if db.showtimes.find_one(identity, {"_id": 1}):
                        skipped += 1
                        continue
                    db.showtimes.insert_one({
                        **identity,
                        "movie_id": movie["_id"],
                        "end_time": end_time,
                        "base_price": base_price,
                        "booked_seats": [],
                        "status": "active",
                        "created_at": now,
                        "source": "availability_refresh",
                    })
                    created += 1
    return {"created": created, "skipped": skipped, "error": None}


def main():
    parser = argparse.ArgumentParser(description="Safely add future CineSphere showtimes.")
    parser.add_argument("--days", type=int, default=7, help="Days to schedule (1-31, default: 7)")
    args = parser.parse_args()
    result = refresh_showtimes(days=args.days)
    if result["error"]:
        raise SystemExit(result["error"])
    print(f"Future showtimes ready: {result['created']} created, {result['skipped']} already existed.")


if __name__ == "__main__":
    main()
