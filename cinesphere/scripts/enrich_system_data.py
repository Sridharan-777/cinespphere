"""
Comprehensive Data Enrichment Script for CineSphere.
Fills all empty sections across Admin, Staff, Worker, and Customer portals.
"""

import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from bson import ObjectId
from werkzeug.security import generate_password_hash
from cinesphere.models.database import get_db

def run_enrichment():
    db = get_db()
    now = datetime.now(UTC).replace(tzinfo=None)
    cycle = now.strftime("%Y-%m")
    today_str = now.strftime("%Y-%m-%d")

    print(f"[*] Starting CineSphere enrichment for cycle {cycle} ({today_str})...")

    # 1. FIX MOVIE POSTERS AND TRAILERS
    movie_updates = [
        {
            "match": "Toxic",
            "poster_url": "https://image.tmdb.org/t/p/w500/rAiYT5KGqDCRIIqo674m0qCePtz.jpg",
            "backdrop_url": "https://image.tmdb.org/t/p/w1280/8Z8Jp1fE92t8tO8gV3J1O4dDqWj.jpg",
            "trailer_url": "https://www.youtube.com/embed/f5M1d7r2UNQ"
        },
        {
            "match": "Jailer 2",
            "poster_url": "https://image.tmdb.org/t/p/w500/e782Na1iG6UvXoYwZ9997QoO9a.jpg",
            "backdrop_url": "https://image.tmdb.org/t/p/w1280/t5zCBSB5xMDKcDqe91qRCOE97Jg.jpg",
            "trailer_url": "https://www.youtube.com/embed/aaNq2NL6D4A"
        },
        {
            "match": "Vishwanath",
            "poster_url": "https://image.tmdb.org/t/p/w500/jIsKmkxMzdCZ0Ux1GVSnu8m6Na6.jpg",
            "backdrop_url": "https://image.tmdb.org/t/p/w1280/wPU78OPN4BYEgWYdXyg0phMee61.jpg",
            "trailer_url": "https://www.youtube.com/embed/gpk4bwqg6y4"
        },
        {
            "match": "Bha Bha Ba",
            "poster_url": "https://image.tmdb.org/t/p/w500/yX8ziNTZ5aOszJ3l4R3BlT2gBIa.jpg",
            "backdrop_url": "https://image.tmdb.org/t/p/w1280/4tTrW9dXCByS5wt2pXVWb58zNjz.jpg",
            "trailer_url": "https://www.youtube.com/embed/GHX8UnlRSUM"
        },
        {
            "match": "Bethlehem",
            "poster_url": "https://image.tmdb.org/t/p/w500/bRwnj8WEKBCvmfeUNOukJPwB43K.jpg",
            "backdrop_url": "https://image.tmdb.org/t/p/w1280/rsHEVjzxU8cxz1sm3vboj99daNv.jpg",
            "trailer_url": "https://www.youtube.com/embed/fk0JHh1P9H0"
        },
        {
            "match": "Pushpa 2",
            "trailer_url": "https://www.youtube.com/embed/g3JUbgZXgNw"
        },
        {
            "match": "Kantara",
            "trailer_url": "https://www.youtube.com/embed/8mrVmf239GU"
        },
        {
            "match": "Avengers: Endgame",
            "trailer_url": "https://www.youtube.com/embed/TcMBFSGVi1c"
        },
        {
            "match": "Toy Story 5",
            "trailer_url": "https://www.youtube.com/embed/wmiIUN-7qhE"
        },
        {
            "match": "Resident Evil",
            "trailer_url": "https://www.youtube.com/embed/4q6UdBk2ZJc"
        },
        {
            "match": "Insidious",
            "trailer_url": "https://www.youtube.com/embed/zuZnRUcoW20"
        },
        {
            "match": "The Odyssey",
            "trailer_url": "https://www.youtube.com/embed/dQw4w9WgXcQ"
        }
    ]

    for item in movie_updates:
        query = {"title": {"$regex": item["match"], "$options": "i"}}
        fields = {k: v for k, v in item.items() if k != "match"}
        db.movies.update_many(query, {"$set": fields})

    # Ensure any movie with missing trailer gets a fallback official embed
    db.movies.update_many(
        {"$or": [{"trailer_url": None}, {"trailer_url": ""}]},
        {"$set": {"trailer_url": "https://www.youtube.com/embed/dQw4w9WgXcQ"}}
    )
    print("[-] Movie posters & trailers populated.")

    # 2. UPDATE WORKER PROFILES WITH DUTIES
    theaters = list(db.theaters.find({"status": "active"}))
    workers = list(db.users.find({"role": "worker"}))
    print(f"[-] Updating {len(workers)} workers with active admission duties...")

    for w in workers:
        db.users.update_one(
            {"_id": w["_id"]},
            {"$set": {
                "worker_profile.authorized_duties": ["admission", "ticket_validation", "ticket_counter"],
                "worker_profile.status": "active"
            }}
        )

    # 3. SEED SHIFTS & ATTENDANCE (to give 95%+ attendance scores)
    print("[-] Seeding monthly shifts and attendance records...")
    db.shifts.delete_many({"date": {"$regex": f"^{cycle}"}})

    # Create 20 shifts for each worker in this cycle
    days_in_month = min(20, now.day)
    for w in workers:
        w_id = w["_id"]
        w_name = w.get("name", "Worker")
        t_id = w.get("worker_profile", {}).get("theater_id")
        t_name = w.get("worker_profile", {}).get("theater_name", "Cinema")

        for d in range(1, 22):
            shift_date = f"{cycle}-{d:02d}"
            # Insert shift
            db.shifts.insert_one({
                "worker_id": w_id,
                "theater_id": t_id,
                "date": shift_date,
                "shift_type": "Morning" if d % 2 == 0 else "Evening",
                "start_time": "09:00 AM",
                "end_time": "05:30 PM",
                "status": "Completed" if d <= now.day else "Scheduled",
                "created_at": now - timedelta(days=25)
            })

            # For past and current days, record attendance
            if d <= now.day:
                existing_att = db.attendance.find_one({"worker_id": w_id, "date": shift_date})
                if not existing_att:
                    # 96% present
                    is_present = (d % 15 != 0)
                    status = "Present" if is_present else "Leave"
                    db.attendance.insert_one({
                        "worker_id": w_id,
                        "worker_name": w_name,
                        "theater_id": t_id,
                        "theater_name": t_name,
                        "date": shift_date,
                        "check_in": "08:55 AM" if is_present else None,
                        "check_out": "05:32 PM" if is_present else None,
                        "status": status,
                        "source": "worker_self_service",
                        "created_at": now
                    })

    # 4. SEED VERIFIED TASKS WITH QUALITY REVIEWS
    print("[-] Seeding verified tasks for quality score evaluation...")
    db.tasks.delete_many({"cycle": cycle})

    task_titles = [
        "Calibrate Screen 1 Dolby Atmos decibel curve",
        "Deep sanitize VIP recliner leather seats",
        "Inspect fire exit route illumination Screen 2",
        "Acoustic panel alignment & vibration test",
        "HVAC temperature and humidity sensor audit",
        "Popcorn popper thermal coil maintenance"
    ]

    for idx, w in enumerate(workers[:40]):
        w_id = w["_id"]
        w_name = w.get("name")
        t_id = w.get("worker_profile", {}).get("theater_id")

        # Create 6 completed tasks per worker with verified quality score 92-98%
        for t_idx, title in enumerate(task_titles):
            score = 92.0 + ((idx + t_idx) % 7)
            db.tasks.insert_one({
                "theater_id": t_id,
                "assigned_worker_id": w_id,
                "worker_name": w_name,
                "title": title,
                "description": f"Standard operational compliance task {t_idx + 1}",
                "priority": "High" if t_idx < 2 else "Medium",
                "status": "Completed",
                "cycle": cycle,
                "completed_at": now - timedelta(days=t_idx + 1),
                "created_by": "Theater Supervisor",
                "created_at": now - timedelta(days=t_idx + 3),
                "quality_review": {
                    "verified": True,
                    "score": score,
                    "reviewed_by": "Lead Supervisor",
                    "reviewed_at": now - timedelta(days=t_idx + 1)
                }
            })

    # 5. ACTIVATE REWARD POLICY & SEED AWARDS & CREDITS
    print("[-] Activating reward policy and seeding awards & credit ledger...")
    db.system_config.update_one(
        {"key": "worker_reward_policy"},
        {"$set": {
            "key": "worker_reward_policy",
            "policy": {
                "name": "Worker Excellence Rewards",
                "status": "active",
                "cycle": "monthly",
                "credits_per_award": 3,
                "credit_valid_days": 90,
                "attendance_threshold": 90.0,
                "maintenance_quality_threshold": 85.0,
                "minimum_verified_tasks": 3,
                "attendance_weight": 0.5,
                "quality_weight": 0.5,
                "tie_policy": "approve_all_above_threshold_within_configured_award_limit",
                "eligible_seat_categories": ["Regular"],
                "restore_credit_on_permitted_cancellation": True,
                "notes": "Active quarterly excellence program."
            },
            "updated_at": now
        }},
        upsert=True
    )

    db.reward_awards.delete_many({})
    db.reward_credits.delete_many({})

    # Seed 15 awards across theaters
    for idx, w in enumerate(workers[:15]):
        w_id = w["_id"]
        w_name = w.get("name")
        t_id = w.get("worker_profile", {}).get("theater_id")
        status = "Approved" if idx < 10 else "Recommended"
        reason = "Perfect 100% attendance and flawless auditorium acoustics review" if idx % 2 == 0 else "Exceptional guest safety audit and hygiene excellence"

        award_doc = {
            "worker_id": w_id,
            "worker_name": w_name,
            "theater_id": t_id,
            "cycle": cycle,
            "status": status,
            "review_reason": reason,
            "created_at": now - timedelta(days=5),
            "issued_at": now - timedelta(days=2) if status == "Approved" else None
        }
        res = db.reward_awards.insert_one(award_doc)

        if status == "Approved":
            # Give 3 credits
            for seq in range(1, 4):
                is_used = (seq == 1 and idx % 3 == 0)
                db.reward_credits.insert_one({
                    "worker_id": w_id,
                    "award_id": res.inserted_id,
                    "cycle": cycle,
                    "sequence": seq,
                    "status": "consumed" if is_used else "available",
                    "expires_at": now + timedelta(days=90),
                    "employer_funded_value": 350,
                    "issued_at": now - timedelta(days=2)
                })

            # Bonus coins
            db.users.update_one({"_id": w_id}, {"$inc": {"worker_profile.bonus_coins": 150}})

    # 6. SEED PAYROLL SALARY REGISTER
    print("[-] Seeding payroll salary register for staff and admin...")
    db.payroll.delete_many({"period": cycle})

    for w in workers:
        w_id = w["_id"]
        w_name = w.get("name")
        t_id = w.get("worker_profile", {}).get("theater_id")
        salary = float(w.get("worker_profile", {}).get("monthly_salary", 22000))

        db.payroll.insert_one({
            "worker_id": w_id,
            "worker_name": w_name,
            "theater_id": t_id,
            "period": cycle,
            "pay_basis": "monthly",
            "base_pay": salary,
            "scheduled_obligations": 22,
            "approved_exclusions": 0,
            "eligible_obligations": 22,
            "attended_obligations": 22,
            "allowances": 1500,
            "overtime": 0,
            "deductions": 0,
            "gross_pay": salary + 1500,
            "net_pay": salary + 1500,
            "status": "Approved",
            "calculation_note": "Full scheduled obligations completed. Zero unauthorized absences.",
            "created_at": now,
            "updated_at": now
        })

    # Also previous month
    prev_month = (now - timedelta(days=35)).strftime("%Y-%m")
    db.payroll.delete_many({"period": prev_month})
    for w in workers[:20]:
        w_id = w["_id"]
        w_name = w.get("name")
        t_id = w.get("worker_profile", {}).get("theater_id")
        salary = float(w.get("worker_profile", {}).get("monthly_salary", 22000))

        db.payroll.insert_one({
            "worker_id": w_id,
            "worker_name": w_name,
            "theater_id": t_id,
            "period": prev_month,
            "pay_basis": "monthly",
            "base_pay": salary,
            "scheduled_obligations": 22,
            "approved_exclusions": 0,
            "eligible_obligations": 22,
            "attended_obligations": 22,
            "allowances": 1500,
            "overtime": 0,
            "deductions": 0,
            "gross_pay": salary + 1500,
            "net_pay": salary + 1500,
            "status": "Paid (recorded)",
            "calculation_note": "Direct bank disbursement recorded.",
            "created_at": now - timedelta(days=20),
            "updated_at": now - timedelta(days=15)
        })

    # 7. SEED BOOKINGS AND GATE ADMISSION VALIDATIONS
    print("[-] Seeding realistic bookings and validated admission tickets...")
    customers = list(db.customers.find({"status": "active"}))
    movies = list(db.movies.find({"status": {"$ne": "Archived"}}))
    showtimes = list(db.showtimes.find({"status": "active"}))

    if showtimes and customers and movies:
        for i in range(15):
            st = showtimes[i % len(showtimes)]
            cust = customers[i % len(customers)]
            movie = db.movies.find_one({"_id": st.get("movie_id")}) or movies[0]
            theater = db.theaters.find_one({"_id": st.get("theater_id")})
            screen = db.screens.find_one({"_id": st.get("screen_id")})

            booking_code = f"CS-{now.year}-{"".join(uuid.uuid4().hex[:6].upper())}"
            seats = [f"B{i % 8 + 1}", f"B{(i+1) % 8 + 1}"]
            amount = st.get("base_price", 200) * len(seats) + 70

            # Find a worker from that theater for admission punch
            worker = next((w for w in workers if str(w.get("worker_profile", {}).get("theater_id")) == str(st.get("theater_id"))), workers[0])

            is_admitted = (i < 8)
            admission_data = {
                "used_at": now - timedelta(hours=i*2 + 1),
                "validated_by": worker["_id"],
                "theater_id": st.get("theater_id")
            } if is_admitted else {}

            db.bookings.insert_one({
                "booking_code": booking_code,
                "confirmation_key": uuid.uuid4().hex,
                "customer_id": cust.get("user_id"),
                "showtime_id": st["_id"],
                "movie_id": movie["_id"],
                "movie_title": movie["title"],
                "movie_poster": movie.get("poster_url", ""),
                "theater_id": st["theater_id"],
                "theater_name": theater["name"] if theater else "CineSphere Cinema",
                "screen_id": st["screen_id"],
                "screen_name": screen["name"] if screen else "Screen 1",
                "show_date": today_str if i < 6 else (now + timedelta(days=1)).strftime("%Y-%m-%d"),
                "start_time": st.get("start_time", "06:30 PM"),
                "seats": seats,
                "pricing": {"subtotal": amount - 70, "convenience_fee": 70, "gst": 25, "grand_total": amount},
                "funding_source": "cash",
                "payment_summary": {"transaction_id": uuid.uuid4().hex[:12], "method": "Direct Instant Pay", "amount": amount, "status": "Verified"},
                "customer_snapshot": {"name": cust.get("name", "Patron"), "email": cust.get("email", "")},
                "status": "Confirmed",
                "is_virtual_ticket_generated": True,
                "admission": admission_data,
                "created_at": now - timedelta(hours=i*3)
            })

    print("[SUCCESS] All CineSphere data enrichment successfully completed!")

if __name__ == "__main__":
    run_enrichment()
