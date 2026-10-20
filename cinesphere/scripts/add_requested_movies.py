"""Add the specifically requested current Indian films from verified trailer sources."""

from datetime import UTC, datetime

from cinesphere.models.database import get_db


REQUESTED_MOVIES = (
    {
        "title": "Jailer 2",
        "original_title": "Jailer 2",
        "language": "Tamil",
        "language_code": "ta",
        "genres": ["Action", "Comedy"],
        "director": "Nelson Dilipkumar",
        "overview": "The next chapter in the Jailer story, starring Rajinikanth and directed by Nelson Dilipkumar.",
        "trailer_url": "https://www.youtube.com/embed/aaNq2NL6D4A",
        "source_reference": "https://www.youtube.com/watch?v=aaNq2NL6D4A",
        "status": "Coming Soon",
        "release_date": "",
    },
    {
        "title": "Toxic: A Fairy Tale for Grown-Ups",
        "original_title": "Toxic: A Fairy Tale for Grown-Ups",
        "language": "Kannada",
        "language_code": "kn",
        "genres": ["Action", "Crime", "Thriller"],
        "director": "Geetu Mohandas",
        "overview": "A period gangster drama led by Yash and set against the criminal underworld of Goa.",
        "trailer_url": "https://www.youtube.com/embed/f5M1d7r2UNQ",
        "source_reference": "https://www.youtube.com/watch?v=f5M1d7r2UNQ",
        "status": "Catalog",
        "release_date": "2026-08-26",
    },
    {
        "title": "Vishwanath & Sons",
        "original_title": "Vishwanath & Sons",
        "language": "Tamil",
        "language_code": "ta",
        "genres": ["Drama", "Family", "Romance"],
        "director": "Venky Atluri",
        "overview": "A Tamil family drama starring Suriya and Mamitha Baiju.",
        "trailer_url": "https://www.youtube.com/embed/gpk4bwqg6y4",
        "source_reference": "https://www.youtube.com/watch?v=gpk4bwqg6y4",
        "status": "Catalog",
        "release_date": "",
    },
    {
        "title": "Bha Bha Ba",
        "original_title": "Bha Bha Ba: Bhayam, Bhakthi, Bahumanam",
        "language": "Malayalam",
        "language_code": "ml",
        "genres": ["Action", "Comedy"],
        "director": "Dhananjay Shankar",
        "overview": "A Malayalam action comedy starring Dileep, Mohanlal, Vineeth Sreenivasan and Dhyan Sreenivasan.",
        "trailer_url": "https://www.youtube.com/embed/GHX8UnlRSUM",
        "source_reference": "https://www.youtube.com/watch?v=GHX8UnlRSUM",
        "status": "Catalog",
        "release_date": "",
    },
    {
        "title": "Bethlehem Kudumba Unit",
        "original_title": "Bethlehem Kudumba Unit",
        "language": "Malayalam",
        "language_code": "ml",
        "genres": ["Comedy", "Drama", "Romance"],
        "director": "Girish A D",
        "overview": "A Malayalam romantic comedy-drama starring Nivin Pauly and Mamitha Baiju.",
        "trailer_url": "https://www.youtube.com/embed/fk0JHh1P9H0",
        "source_reference": "https://www.youtube.com/watch?v=fk0JHh1P9H0",
        "status": "Catalog",
        "release_date": "2026-08-21",
    },
)


def add_requested_movies(db=None):
    db = db or get_db()
    now = datetime.now(UTC).replace(tzinfo=None)
    created = 0
    updated = 0
    for index, movie in enumerate(REQUESTED_MOVIES):
        existing = db.movies.find_one({"title": movie["title"]})
        shared = {
            **movie,
            "poster_url": (existing or {}).get("poster_url") or "/static/images/default_poster.svg",
            "backdrop_url": (existing or {}).get("backdrop_url") or "/static/images/default_backdrop.svg",
            "runtime": (existing or {}).get("runtime") or 0,
            "rating": (existing or {}).get("rating") or 0,
            "vote_count": (existing or {}).get("vote_count") or 0,
            "popularity": (existing or {}).get("popularity") or (100 - index),
            "cast": (existing or {}).get("cast") or [],
            "writers": (existing or {}).get("writers") or [],
            "is_south_indian": True,
            "metadata_source": "Verified official trailer",
            "updated_at": now,
        }
        if existing:
            db.movies.update_one({"_id": existing["_id"]}, {"$set": shared})
            updated += 1
        else:
            shared["created_at"] = now
            db.movies.insert_one(shared)
            created += 1
    return {"created": created, "updated": updated}


def main():
    result = add_requested_movies()
    print(f"Requested movies ready: {result['created']} created, {result['updated']} updated.")


if __name__ == "__main__":
    main()
