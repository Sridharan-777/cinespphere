from flask import Blueprint, render_template, request, redirect, url_for, flash, session, abort
from datetime import UTC, datetime
from bson import ObjectId
from werkzeug.security import generate_password_hash
from cinesphere.models.database import get_db
from cinesphere.utils.decorators import role_required
from cinesphere.services.report_service import ReportService
from cinesphere.utils.audit import log_audit

staff_bp = Blueprint("staff", __name__)

@staff_bp.route("/staff/dashboard")
@role_required("staff")
def dashboard():
    db = get_db()
    uid = ObjectId(session["user_id"])
    staff = db.staff.find_one({"user_id": uid})
    if not staff:
        flash("Staff profile not found.", "warning")
        return render_template("errors/403.html"), 403

    theater_id = staff["theater_id"]
    theater = db.theaters.find_one({"_id": theater_id})

    # Stats for this theater
    stats = ReportService.get_theater_stats(theater_id)

    # Active shows today
    today_str = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d")
    today_shows = list(db.showtimes.find({"theater_id": theater_id, "show_date": today_str}).sort("start_time", 1))
    for s in today_shows:
        movie = db.movies.find_one({"_id": s["movie_id"]})
        screen = db.screens.find_one({"_id": s["screen_id"]})
        s["movie"] = movie
        s["screen"] = screen
        s["booked_count"] = len(s.get("booked_seats", []))

    # Assigned Field Workers for this theater
    workers = list(db.workers.find({"theater_id": theater_id, "status": "active"}).limit(10))

    # Urgent tasks
    urgent_tasks = list(db.tasks.find({"theater_id": theater_id, "status": {"$ne": "Completed"}}).sort("created_at", -1).limit(5))
    recent_bookings = list(db.bookings.find({"theater_id": theater_id}).sort("created_at", -1).limit(8))
    today_capacity = sum((s.get("screen") or {}).get("capacity", 48) for s in today_shows)
    today_reserved = sum(s.get("booked_count", 0) for s in today_shows)
    operations = {
        "shows_today": len(today_shows),
        "seats_reserved": today_reserved,
        "occupancy_today": round((today_reserved / today_capacity) * 100, 1) if today_capacity else 0,
    }

    return render_template("staff/dashboard.html",
                           staff=staff,
                           theater=theater,
                           stats=stats,
                           today_shows=today_shows,
                           workers=workers,
                           urgent_tasks=urgent_tasks,
                           recent_bookings=recent_bookings,
                           operations=operations)

@staff_bp.route("/staff/customers")
@role_required("staff")
def customers_list():
    db = get_db()
    uid = ObjectId(session["user_id"])
    staff = db.staff.find_one({"user_id": uid})
    if not staff:
        abort(403)
    search = request.args.get("search", "").strip().lower()
    bookings = list(db.bookings.find({"theater_id": staff["theater_id"]}).sort("created_at", -1))
    patrons = {}
    for booking in bookings:
        customer_id = booking.get("customer_id")
        if not customer_id:
            continue
        entry = patrons.setdefault(str(customer_id), {
            "name": booking.get("customer_snapshot", {}).get("name", "Customer"),
            "email": booking.get("customer_snapshot", {}).get("email", ""),
            "booking_count": 0,
            "ticket_count": 0,
            "theater_spend": 0,
            "last_visit": booking.get("show_date", ""),
            "last_booking_code": booking.get("booking_code", "")
        })
        entry["booking_count"] += 1
        entry["ticket_count"] += len(booking.get("seats", []))
        if booking.get("status") == "Confirmed":
            entry["theater_spend"] += booking.get("pricing", {}).get("grand_total", 0)
    customers = list(patrons.values())
    if search:
        customers = [c for c in customers if search in c["name"].lower() or search in c["email"].lower()
                     or search in c["last_booking_code"].lower()]
    return render_template("staff/customers.html", customers=customers,
                           search=request.args.get("search", ""),
                           theater_name=staff.get("theater_name", "this theater"))

@staff_bp.route("/staff/workers")
@role_required("staff")
def workers_list():
    db = get_db()
    uid = ObjectId(session["user_id"])
    staff = db.staff.find_one({"user_id": uid})
    theater_id = staff["theater_id"]

    workers = list(db.workers.find({"theater_id": theater_id}).sort("created_at", -1))
    worker_count = sum(1 for worker in workers if worker.get("status") == "active")
    return render_template("staff/workers.html", workers=workers, theater_name=staff["theater_name"],
                           worker_count=worker_count, max_workers=20)

@staff_bp.route("/staff/workers/<worker_id>/toggle-status", methods=["POST"])
@role_required("staff")
def worker_toggle_status(worker_id):
    db = get_db()
    staff = db.staff.find_one({"user_id": ObjectId(session["user_id"])})
    try:
        worker_oid = ObjectId(worker_id)
    except Exception:
        flash("Invalid worker identifier.", "danger")
        return redirect(url_for("staff.workers_list"))
    worker = db.workers.find_one({"_id": worker_oid, "theater_id": staff["theater_id"]}) if staff else None
    if not worker:
        abort(404)
    new_status = "inactive" if worker.get("status") == "active" else "active"
    if new_status == "active" and db.workers.count_documents({"theater_id": staff["theater_id"], "status": "active"}) >= 20:
        flash("Cannot reactivate this worker because the theater already has 20 active workers.", "danger")
        return redirect(url_for("staff.workers_list"))
    db.workers.update_one({"_id": worker_oid}, {"$set": {"status": new_status}})
    db.users.update_one({"_id": worker["user_id"]}, {"$set": {"status": new_status}})
    log_audit("STAFF_TOGGLE_WORKER", "worker", worker_oid, f"Worker status set to {new_status}")
    flash(f"Worker status changed to {new_status}.", "success")
    return redirect(url_for("staff.workers_list"))

@staff_bp.route("/staff/workers/create", methods=["GET", "POST"])
@role_required("staff")
def worker_create():
    db = get_db()
    uid = ObjectId(session["user_id"])
    staff = db.staff.find_one({"user_id": uid})
    theater_id = staff["theater_id"]

    # Enforce strict maximum 20 workers per theater capacity
    current_worker_count = db.workers.count_documents({"theater_id": theater_id, "status": "active"})
    if current_worker_count >= 20:
        flash("Policy limit reached: Exactly 20 field workers maximum allowed per theater location.", "danger")
        return redirect(url_for("staff.workers_list"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        department = request.form.get("department", "Housekeeping")
        designation = request.form.get("designation", "Operations Worker")
        salary = float(request.form.get("monthly_salary", 22000))
        shift = request.form.get("shift_timings", "09:00 AM - 05:30 PM")

        existing_user = db.users.find_one({"email": email})
        if existing_user:
            flash("Email address already registered.", "danger")
            return render_template("staff/worker_form.html")

        user_res = db.users.insert_one({
            "name": name,
            "email": email,
            "phone": phone,
            "password_hash": generate_password_hash("Worker@1234"),
            "role": "worker",
            "status": "active",
            "created_at": datetime.now(UTC).replace(tzinfo=None)
        })

        db.workers.insert_one({
            "user_id": user_res.inserted_id,
            "employee_code": f"WRK-{staff['theater_name'][:3].upper()}-{current_worker_count + 1:02d}",
            "name": name,
            "email": email,
            "theater_id": theater_id,
            "theater_name": staff["theater_name"],
            "department": department,
            "designation": designation,
            "monthly_salary": salary,
            "hourly_rate": round(salary / 160, 2),
            "shift_timings": shift,
            "status": "active",
            "current_task": "Idle / On Call",
            "created_at": datetime.now(UTC).replace(tzinfo=None)
        })

        log_audit("STAFF_CREATE_WORKER", "worker", user_res.inserted_id, f"Staff created worker {name} in {department}")
        flash(f"Field worker {name} onboarded successfully! Quota: {current_worker_count + 1}/20.", "success")
        return redirect(url_for("staff.workers_list"))

    return render_template("staff/worker_form.html")

@staff_bp.route("/staff/shows")
@role_required("staff")
def shows_list():
    db = get_db()
    uid = ObjectId(session["user_id"])
    staff = db.staff.find_one({"user_id": uid})
    theater_id = staff["theater_id"]

    shows = list(db.showtimes.find({"theater_id": theater_id}).sort("show_date", -1))
    for s in shows:
        s["movie"] = db.movies.find_one({"_id": s["movie_id"]})
        s["screen"] = db.screens.find_one({"_id": s["screen_id"]})
        s["booked_count"] = len(s.get("booked_seats", []))

    return render_template("staff/shows.html", shows=shows)

@staff_bp.route("/staff/shows/create", methods=["GET", "POST"])
@role_required("staff")
def show_create():
    db = get_db()
    uid = ObjectId(session["user_id"])
    staff = db.staff.find_one({"user_id": uid})
    theater_id = staff["theater_id"]

    if request.method == "POST":
        try:
            movie_id = ObjectId(request.form.get("movie_id"))
            screen_id = ObjectId(request.form.get("screen_id"))
        except Exception:
            flash("Invalid movie or screen selection.", "danger")
            return redirect(url_for("staff.show_create"))
        show_date = request.form.get("show_date")
        start_time = request.form.get("start_time")
        end_time = request.form.get("end_time")
        base_price = float(request.form.get("base_price", 200))

        if not db.movies.find_one({"_id": movie_id}) or not db.screens.find_one({"_id": screen_id, "theater_id": theater_id, "status": "active"}):
            flash("The selected movie or screen is not available for your theater.", "danger")
            return redirect(url_for("staff.show_create"))

        # Conflict Check: Ensure no overlapping show on the same screen and date
        existing_show = db.showtimes.find_one({
            "screen_id": screen_id,
            "show_date": show_date,
            "start_time": start_time,
            "status": "active"
        })
        if existing_show:
            flash("Schedule conflict! A show is already scheduled on this screen at that time.", "danger")
            return redirect(url_for("staff.show_create"))

        show_res = db.showtimes.insert_one({
            "theater_id": theater_id,
            "screen_id": screen_id,
            "movie_id": movie_id,
            "show_date": show_date,
            "start_time": start_time,
            "end_time": end_time,
            "base_price": base_price,
            "booked_seats": [],
            "status": "active",
            "created_at": datetime.now(UTC).replace(tzinfo=None)
        })

        movie = db.movies.find_one({"_id": movie_id})
        log_audit("STAFF_SCHEDULE_SHOW", "showtime", show_res.inserted_id, f"Staff scheduled show for {movie.get('title')} on {show_date}")
        flash("New movie showtime successfully scheduled!", "success")
        return redirect(url_for("staff.shows_list"))

    movies = list(db.movies.find())
    screens = list(db.screens.find({"theater_id": theater_id, "status": "active"}))
    return render_template("staff/show_form.html", movies=movies, screens=screens)

@staff_bp.route("/staff/tasks", methods=["GET", "POST"])
@role_required("staff")
def tasks():
    db = get_db()
    uid = ObjectId(session["user_id"])
    staff = db.staff.find_one({"user_id": uid})
    theater_id = staff["theater_id"]

    if request.method == "POST":
        try:
            worker_id = ObjectId(request.form.get("worker_id"))
        except Exception:
            flash("Invalid worker selection.", "danger")
            return redirect(url_for("staff.tasks"))
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        priority = request.form.get("priority", "Medium")

        worker = db.workers.find_one({"_id": worker_id, "theater_id": theater_id, "status": "active"})
        if not worker:
            flash("Worker not found.", "danger")
            return redirect(url_for("staff.tasks"))

        task_res = db.tasks.insert_one({
            "theater_id": theater_id,
            "assigned_worker_id": worker_id,
            "worker_name": worker["name"],
            "title": title,
            "description": description,
            "priority": priority,
            "status": "Pending",
            "created_by": staff["name"],
            "notes": "",
            "created_at": datetime.now(UTC).replace(tzinfo=None)
        })

        db.workers.update_one({"_id": worker_id}, {"$set": {"current_task": title}})
        log_audit("STAFF_CREATE_TASK", "task", task_res.inserted_id, f"Staff assigned task '{title}' to {worker['name']}")
        flash(f"Operational duty dispatched to {worker['name']}.", "success")
        return redirect(url_for("staff.tasks"))

    tasks_list = list(db.tasks.find({"theater_id": theater_id}).sort("created_at", -1))
    workers = list(db.workers.find({"theater_id": theater_id, "status": "active"}))
    return render_template("staff/tasks.html", tasks=tasks_list, workers=workers)

@staff_bp.route("/staff/reports")
@role_required("staff")
def reports():
    db = get_db()
    uid = ObjectId(session["user_id"])
    staff = db.staff.find_one({"user_id": uid})
    theater_id = staff["theater_id"]
    theater = db.theaters.find_one({"_id": theater_id})

    stats = ReportService.get_theater_stats(theater_id)
    bookings = list(db.bookings.find({"theater_id": theater_id}).sort("created_at", -1))

    return render_template("staff/reports.html", stats=stats, bookings=bookings, theater=theater)
