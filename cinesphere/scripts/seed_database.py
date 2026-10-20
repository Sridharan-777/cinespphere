import os
import sys
from datetime import UTC, datetime, timedelta
from werkzeug.security import generate_password_hash

# Ensure import path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cinesphere.models.database import get_db
from cinesphere.services.tmdb_service import tmdb_service

def seed_database():
    db = get_db()
    print("Beginning CineSphere enhanced database seeding (Multi-genre & South Indian movies)...")

    if db.users.estimated_document_count() and os.getenv("CINESPHERE_ALLOW_DEVELOPMENT_RESET") != "YES":
        raise RuntimeError("Refusing to reset a non-empty database. Set CINESPHERE_ALLOW_DEVELOPMENT_RESET=YES only for disposable development data.")

    # Clear previous collections
    db.users.drop()
    db.customers.drop()
    db.staff.drop()
    db.workers.drop()
    db.theaters.drop()
    db.screens.drop()
    db.showtimes.drop()
    db.movies.drop()
    db.reviews.drop()
    db.tasks.drop()
    db.attendance.drop()
    db.notifications.drop()
    db.audit_logs.drop()
    db.payments.drop()
    db.bookings.drop()

    default_password = generate_password_hash("Admin@1234")

    # 1. ADMIN USER
    admin_res = db.users.insert_one({
        "name": "Super Admin",
        "email": "admin@cinesphere.com",
        "phone": "+91 9999900000",
        "password_hash": default_password,
        "role": "admin",
        "status": "active",
        "created_at": datetime.now(UTC).replace(tzinfo=None)
    })
    print(f"Created Admin: admin@cinesphere.com")

    # 2. THEATERS
    theaters_data = [
        {
            "name": "CineSphere Mumbai Central",
            "address": "Bellasis Road, Mumbai Central",
            "city": "Mumbai",
            "phone": "+91 22 2307 1000",
            "email": "mumbaicentral@cinesphere.com",
            "facilities": ["Parking", "Dolby Atmos", "Food Court", "3D", "IMAX", "Wheelchair Access"],
            "staff_capacity": 5,
            "worker_capacity": 20,
            "status": "active",
            "created_at": datetime.now(UTC).replace(tzinfo=None)
        },
        {
            "name": "CineSphere Hyderabad Banjara",
            "address": "Road No. 1, Banjara Hills",
            "city": "Hyderabad",
            "phone": "+91 40 2335 4400",
            "email": "hyderabad@cinesphere.com",
            "facilities": ["Parking", "VIP Recliners", "Dolby Atmos", "4DX", "Gourmet Diner"],
            "staff_capacity": 5,
            "worker_capacity": 20,
            "status": "active",
            "created_at": datetime.now(UTC).replace(tzinfo=None)
        },
        {
            "name": "CineSphere Bengaluru Koramangala",
            "address": "80 Feet Road, 4th Block, Koramangala",
            "city": "Bengaluru",
            "phone": "+91 80 4112 8800",
            "email": "bengaluru@cinesphere.com",
            "facilities": ["Parking", "IMAX Laser", "Dolby Atmos", "Food Court", "Kids Lounge"],
            "staff_capacity": 5,
            "worker_capacity": 20,
            "status": "active",
            "created_at": datetime.now(UTC).replace(tzinfo=None)
        },
        {
            "name": "CineSphere Chennai Express",
            "address": "Whites Road, Royapettah",
            "city": "Chennai",
            "phone": "+91 44 2857 5500",
            "email": "chennai@cinesphere.com",
            "facilities": ["Parking", "Dolby Atmos", "3D", "VIP Lounge", "Cafe"],
            "staff_capacity": 5,
            "worker_capacity": 20,
            "status": "active",
            "created_at": datetime.now(UTC).replace(tzinfo=None)
        }
    ]

    theater_ids = []
    screens_by_theater = {}

    for t_data in theaters_data:
        t_res = db.theaters.insert_one(t_data)
        t_id = t_res.inserted_id
        theater_ids.append(t_id)
        screens_by_theater[t_id] = []

        # Create 4 Screens per theater
        for s_num in range(1, 5):
            s_type = "IMAX Laser" if s_num == 1 else ("4DX" if s_num == 2 else ("3D" if s_num == 3 else "Standard"))
            sc_res = db.screens.insert_one({
                "theater_id": t_id,
                "name": f"Screen {s_num}",
                "screen_number": s_num,
                "screen_type": s_type,
                "capacity": 48,
                "status": "active",
                "created_at": datetime.now(UTC).replace(tzinfo=None)
            })
            screens_by_theater[t_id].append(sc_res.inserted_id)

        city_prefix = t_data['city'].lower().replace(" ", "")[:4]
        # SEED EXACTLY 5 STAFF MEMBERS FOR EACH THEATER (Quota Enforcement)
        for s_idx in range(1, 6):
            s_email = f"staff_{city_prefix}_{s_idx}@cinesphere.com"
            if t_data["name"] == "CineSphere Mumbai Central" and s_idx == 1:
                s_email = "staff1@cinesphere.com"

            staff_u = db.users.insert_one({
                "name": f"Staff {s_idx} ({t_data['city']})",
                "email": s_email,
                "phone": f"+91 98200{s_idx:05d}",
                "password_hash": default_password,
                "role": "staff",
                "status": "active",
                "created_at": datetime.now(UTC).replace(tzinfo=None)
            })

            db.staff.insert_one({
                "user_id": staff_u.inserted_id,
                "employee_code": f"STF-{city_prefix.upper()}-{s_idx:02d}",
                "name": f"Staff {s_idx} ({t_data['city']})",
                "email": s_email,
                "theater_id": t_id,
                "theater_name": t_data["name"],
                "designation": "Operations Lead" if s_idx == 1 else "Floor Supervisor",
                "status": "active",
                "created_at": datetime.now(UTC).replace(tzinfo=None)
            })

        # SEED EXACTLY 20 FIELD WORKERS FOR EACH THEATER (Quota Enforcement)
        departments = [
            "Housekeeping", "Projection & Audio", "Auditorium Operations",
            "Facility Maintenance", "Security & Safety"
        ]
        for w_idx in range(1, 21):
            w_email = f"worker_{city_prefix}_{w_idx}@cinesphere.com"
            if t_data["name"] == "CineSphere Mumbai Central" and w_idx == 1:
                w_email = "worker1@cinesphere.com"

            dept = departments[(w_idx - 1) % len(departments)]
            worker_profile = {
                "employee_code": f"WRK-{city_prefix.upper()}-{w_idx:02d}",
                "theater_id": t_id,
                "theater_name": t_data["name"],
                "department": dept,
                "designation": f"{dept} Specialist",
                "monthly_salary": 22000,
                "hourly_rate": 137.5,
                "shift_timings": "09:00 AM - 05:30 PM",
                "current_task": "Idle / On Standby" if w_idx > 3 else "Auditorium Sound Calibration",
                "authorized_duties": ["admission", "ticket_validation", "ticket_counter"],
                "bonus_coins": 100
            }
            worker_u = db.users.insert_one({
                "name": f"Worker {w_idx} ({t_data['city']})",
                "email": w_email,
                "phone": f"+91 97100{w_idx:05d}",
                "password_hash": default_password,
                "role": "worker",
                "status": "active",
                "worker_profile": worker_profile,
                "created_at": datetime.now(UTC).replace(tzinfo=None)
            })

            if w_idx <= 2:
                db.tasks.insert_one({
                    "theater_id": t_id,
                    "assigned_worker_id": worker_u.inserted_id,
                    "worker_name": f"Field Worker {w_idx}",
                    "title": f"Sanitize & inspect Screen {w_idx} seats",
                    "description": "Clean cup holders, inspect row lighting, and ensure emergency exit clear.",
                    "priority": "High" if w_idx == 1 else "Medium",
                    "status": "In Progress" if w_idx == 1 else "Pending",
                    "created_by": "Floor Supervisor",
                    "created_at": datetime.now(UTC).replace(tzinfo=None),
                    "notes": "Shift task dispatched."
                })

        print(f"Created Theater '{t_data['name']}' with 4 screens, 5 staff (max), and 20 workers (max).")

    # 3. SEED CUSTOMERS
    customer_ids = []
    for c_idx in range(1, 11):
        c_email = f"customer{c_idx}@cinesphere.com"
        c_user = db.users.insert_one({
            "name": f"Customer Patron {c_idx}",
            "email": c_email,
            "phone": f"+91 91000{c_idx:05d}",
            "password_hash": default_password,
            "role": "customer",
            "status": "active",
            "created_at": datetime.now(UTC).replace(tzinfo=None)
        })
        cust_profile = db.customers.insert_one({
            "user_id": c_user.inserted_id,
            "name": f"Customer Patron {c_idx}",
            "email": c_email,
            "phone": f"+91 91000{c_idx:05d}",
            "city": "Mumbai" if c_idx <= 4 else ("Hyderabad" if c_idx <= 7 else "Bengaluru"),
            "favorite_movies": [],
            "total_bookings": 0,
            "total_spent": 0,
            "status": "active",
            "created_at": datetime.now(UTC).replace(tzinfo=None)
        })
        customer_ids.append((c_user.inserted_id, cust_profile.inserted_id))

    print("Created 10 verified customer accounts.")

    # 4. SYNC REAL MOVIES ACROSS ALL GENRES AND SOUTH INDIAN CINEMA
    print("Syncing diverse genres and South Indian movies via TMDB...")
    
    # Specific Curated TMDB IDs:
    # South Indian Blockbusters:
    # 579974: RRR (Telugu)
    # 858485: Kantara (Kannada)
    # 256040: Baahubali: The Beginning (Telugu)
    # 350312: Baahubali 2: The Conclusion (Telugu)
    # 564147: KGF Chapter 1 (Kannada)
    # 587412: KGF Chapter 2 (Kannada)
    # 690957: Pushpa: The Rise (Telugu)
    # 857598: Pushpa 2: The Rule (Telugu)
    # 743563: Vikram (Tamil)
    # 937020: Jailer (Tamil)
    # 949229: Leo (Tamil)
    # 660046: Ponniyin Selvan: Part 1 (Tamil)
    # 587030: Kaithi (Tamil)
    # 1069945: Manjummel Boys (Malayalam)
    # 1084812: Aavesham (Malayalam)
    # 1149791: Premalu (Malayalam)
    # 801688: Kalki 2898 AD (Telugu)
    # 811941: Devara: Part 1 (Telugu)
    # 770906: Salaar: Part 1 (Telugu)
    # 1068452: Hi Nanna (Telugu)
    # 869760: Hanu-Man (Telugu)
    #
    # Global Genre Titans:
    # 27205: Inception (Sci-Fi / Action)
    # 157336: Interstellar (Sci-Fi / Drama)
    # 155: The Dark Knight (Action / Crime)
    # 129: Spirited Away (Animation / Fantasy)
    # 138843: The Conjuring (Horror / Mystery)
    # 872585: Oppenheimer (Biography / History / Drama)
    # 693134: Dune: Part Two (Sci-Fi / Adventure)
    # 98: Gladiator (Action / History / Drama)

    target_tmdb_ids = [
        # South Indian Cinema
        579974, 858485, 256040, 350312, 564147, 587412,
        690957, 857598, 743563, 937020, 949229, 660046,
        1069945, 1084812, 1149791, 801688, 811941, 770906,
        1068452, 869760, 587030,
        # Diverse Global Genres
        27205, 157336, 155, 129, 138843, 872585, 693134, 98
    ]

    synced_movies = []
    for tmdb_id in target_tmdb_ids:
        raw_detail = tmdb_service.get_movie_details(tmdb_id)
        if raw_detail:
            formatted = tmdb_service.format_movie_data(raw_detail)
            if formatted:
                # Coming Soon vs Now Showing distribution
                if tmdb_id in [857598, 811941]:
                    formatted["status"] = "Coming Soon"
                else:
                    formatted["status"] = "Now Showing"

                formatted["created_at"] = datetime.now(UTC).replace(tzinfo=None)
                m_res = db.movies.insert_one(formatted)
                formatted["_id"] = m_res.inserted_id
                synced_movies.append(formatted)
                print(f"Synced: {formatted['title']} ({formatted['language']}) - {', '.join(formatted['genres'])}")

    print(f"Successfully populated {len(synced_movies)} rich movies into database!")

    if not synced_movies:
        print("TMDB is unavailable or unconfigured. No movie metadata or local showtimes were invented.")
        return

    # 5. CREATE COMPREHENSIVE SHOWTIMES
    print("Scheduling multi-city showtimes...")
    start_date = datetime.now(UTC).replace(tzinfo=None).date()
    show_dates = [(start_date + timedelta(days=offset)).isoformat() for offset in range(4)]
    show_times = [
        ("10:30 AM", "01:30 PM", 180),
        ("02:15 PM", "05:15 PM", 220),
        ("06:00 PM", "09:00 PM", 280),
        ("09:45 PM", "12:45 AM", 250)
    ]

    # Distribute movies across theaters
    now_showing_movies = [m for m in synced_movies if m["status"] == "Now Showing"]
    
    for t_idx, t_id in enumerate(theater_ids):
        screens = screens_by_theater[t_id]
        # Rotate movies per theater
        theater_movies = now_showing_movies[(t_idx * 4):(t_idx * 4 + 4)] or now_showing_movies[:4]

        for sc_idx, sc_id in enumerate(screens):
            assigned_movie = theater_movies[sc_idx % len(theater_movies)]
            for s_date in show_dates[:2]:
                for st_time, end_time, price in show_times[:2]:
                    db.showtimes.insert_one({
                        "theater_id": t_id,
                        "screen_id": sc_id,
                        "movie_id": assigned_movie["_id"],
                        "show_date": s_date,
                        "start_time": st_time,
                        "end_time": end_time,
                        "base_price": price,
                        "booked_seats": ["A1", "A2", "D4"],
                        "status": "active",
                        "created_at": datetime.now(UTC).replace(tzinfo=None)
                    })

    # 6. SEED REVIEWS
    for m in synced_movies[:6]:
        db.reviews.insert_one({
            "movie_id": m["_id"],
            "customer_id": customer_ids[0][0],
            "customer_name": "Customer Patron 1",
            "is_verified": True,
            "rating": 5,
            "title": f"Phenomenal theatrical showcase for {m['title']}!",
            "comment": f"High energy cinema! The sound mix in {m['language']} along with the visual fidelity was sheer perfection on the big screen.",
            "status": "Published",
            "created_at": datetime.now(UTC).replace(tzinfo=None)
        })
        db.reviews.insert_one({
            "movie_id": m["_id"],
            "customer_id": customer_ids[1][0],
            "customer_name": "Customer Patron 2",
            "is_verified": True,
            "rating": 4,
            "title": "Loved the acting and pacing",
            "comment": "Incredible screenplay and direction. A must watch with family in IMAX.",
            "status": "Published",
            "created_at": datetime.now(UTC).replace(tzinfo=None)
        })

    print("âœ… CineSphere database seed successfully completed!")
    print(f"Summary: Admin created | 4 Theaters | {len(synced_movies)} Real Movies across South Indian (Telugu, Tamil, Malayalam, Kannada) & Global genres.")

if __name__ == "__main__":
    seed_database()
