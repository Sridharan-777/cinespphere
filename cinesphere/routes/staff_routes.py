from flask import Blueprint, render_template, request, redirect, url_for, flash, session, abort
from datetime import UTC, datetime
from bson import ObjectId
from werkzeug.security import generate_password_hash
from cinesphere.models.database import get_db
from cinesphere.models.worker_model import count_workers, find_worker, list_workers, update_worker
from cinesphere.utils.decorators import role_required
from cinesphere.services.report_service import ReportService
from cinesphere.utils.audit import log_audit
from cinesphere.services.reward_service import RewardService
from cinesphere.services.payroll_service import PayrollService

import os
from uuid import uuid4
from flask import current_app
from werkzeug.utils import secure_filename

staff_bp = Blueprint("staff", __name__)


def _staff_profile():
    return get_db().staff.find_one({"user_id": ObjectId(session["user_id"]), "status": "active"})


def _scoped_customer(db, theater_id, customer_id):
    """Return a customer only when this theater has a legitimate service relationship."""
    try:
        customer_oid = ObjectId(customer_id)
    except Exception:
        return None
    has_booking = db.bookings.find_one({"customer_id": customer_oid, "theater_id": theater_id}, {"_id": 1})
    has_access = db.customer_theater_access.find_one({"customer_id": customer_oid, "theater_id": theater_id})
    if not has_booking and not has_access:
        return None
    return db.users.find_one({"_id": customer_oid, "role": "customer"})

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
    workers = list_workers(theater_id, active_only=True, limit=10)

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
    scoped_ids = {ObjectId(customer_id) for customer_id in patrons}
    scoped_ids.update(row["customer_id"] for row in db.customer_theater_access.find(
        {"theater_id": staff["theater_id"]}, {"customer_id": 1}))
    access_by_customer = {str(row["customer_id"]): row for row in db.customer_theater_access.find(
        {"theater_id": staff["theater_id"]})}
    for user in db.users.find({"_id": {"$in": list(scoped_ids)}, "role": "customer"}):
        entry = patrons.setdefault(str(user["_id"]), {
            "name": user.get("name", "Customer"), "email": user.get("email", ""),
            "booking_count": 0, "ticket_count": 0, "theater_spend": 0,
            "last_visit": "", "last_booking_code": ""
        })
        entry["name"] = user.get("name", entry["name"])
        entry["email"] = user.get("email", entry["email"])
        entry["_id"] = user["_id"]
        entry["status"] = access_by_customer.get(str(user["_id"]), {}).get("status", "active")
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

    workers = list_workers(theater_id)
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
    worker = find_worker(worker_oid, staff["theater_id"]) if staff else None
    if not worker:
        abort(404)
    new_status = "inactive" if worker.get("status") == "active" else "active"
    if new_status == "active" and count_workers(staff["theater_id"], active_only=True) >= 20:
        flash("Cannot reactivate this worker because the theater already has 20 active workers.", "danger")
        return redirect(url_for("staff.workers_list"))
    db.users.update_one({"_id": worker_oid, "role": "worker"}, {"$set": {"status": new_status}})
    log_audit("STAFF_TOGGLE_WORKER", "worker", worker_oid, f"Worker status set to {new_status}")
    flash(f"Worker status changed to {new_status}.", "success")
    return redirect(url_for("staff.workers_list"))

@staff_bp.route("/staff/workers/<worker_id>/attendance", methods=["POST"])
@role_required("staff")
def worker_attendance(worker_id):
    db = get_db()
    staff = db.staff.find_one({"user_id": ObjectId(session["user_id"])})
    worker = find_worker(worker_id, staff["theater_id"], active_only=True) if staff else None
    if not worker:
        abort(404)
    now = datetime.now(UTC).replace(tzinfo=None)
    today = now.strftime("%Y-%m-%d")
    action = request.form.get("action")
    existing = db.attendance.find_one({"worker_id": worker["_id"], "date": today})
    if action == "check_in" and not existing:
        db.attendance.insert_one({
            "worker_id": worker["_id"], "worker_name": worker["name"],
            "theater_id": worker["theater_id"], "date": today,
            "check_in": now.strftime("%I:%M %p"), "check_out": None,
            "status": "Present", "marked_by": staff["user_id"], "created_at": now,
        })
        flash(f"Check-in recorded for {worker['name']}.", "success")
    elif action == "check_out" and existing and not existing.get("check_out"):
        db.attendance.update_one({"_id": existing["_id"]}, {"$set": {
            "check_out": now.strftime("%I:%M %p"), "updated_at": now
        }})
        flash(f"Check-out recorded for {worker['name']}.", "success")
    else:
        flash("That attendance action has already been recorded or is invalid.", "warning")
    return redirect(url_for("staff.workers_list"))

@staff_bp.route("/staff/workers/create", methods=["GET", "POST"])
@role_required("staff")
def worker_create():
    db = get_db()
    uid = ObjectId(session["user_id"])
    staff = db.staff.find_one({"user_id": uid})
    theater_id = staff["theater_id"]

    # Enforce strict maximum 20 workers per theater capacity
    current_worker_count = count_workers(theater_id, active_only=True)
    if current_worker_count >= 20:
        flash("Policy limit reached: Exactly 20 field workers maximum allowed per theater location.", "danger")
        return redirect(url_for("staff.workers_list"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        department = request.form.get("department", "Housekeeping")
        designation = request.form.get("designation", "Operations Worker")
        shift = request.form.get("shift_timings", "09:00 AM - 05:30 PM")

        existing_user = db.users.find_one({"email": email})
        if existing_user:
            flash("Email address already registered.", "danger")
            return render_template("staff/worker_form.html")

        worker_profile = {
            "employee_code": f"WRK-{staff['theater_name'][:3].upper()}-{current_worker_count + 1:02d}",
            "theater_id": theater_id,
            "theater_name": staff["theater_name"],
            "department": department,
            "designation": designation,
            "monthly_salary": 0,
            "hourly_rate": 0,
            "shift_timings": shift,
            "current_task": "Idle / On Call"
        }
        user_res = db.users.insert_one({
            "name": name,
            "email": email,
            "phone": phone,
            "password_hash": generate_password_hash("Worker@1234"),
            "role": "worker",
            "status": "active",
            "worker_profile": worker_profile,
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

        worker = find_worker(worker_id, theater_id, active_only=True)
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

        update_worker(worker_id, {"current_task": title})
        log_audit("STAFF_CREATE_TASK", "task", task_res.inserted_id, f"Staff assigned task '{title}' to {worker['name']}")
        flash(f"Operational duty dispatched to {worker['name']}.", "success")
        return redirect(url_for("staff.tasks"))

    tasks_list = list(db.tasks.find({"theater_id": theater_id}).sort("created_at", -1))
    workers = list_workers(theater_id, active_only=True)
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


@staff_bp.route("/staff/rewards")
@role_required("staff")
def reward_reviews():
    db = get_db()
    staff = _staff_profile()
    if not staff:
        abort(403)
    workers = list_workers(staff["theater_id"], active_only=True)
    candidates = [{"worker": worker, "evidence": RewardService.eligibility(worker["_id"])} for worker in workers]
    awards = list(db.reward_awards.find({"theater_id": staff["theater_id"]}).sort("cycle", -1))
    return render_template("staff/rewards.html", candidates=candidates, awards=awards, policy=RewardService.policy())


@staff_bp.route("/staff/rewards/<worker_id>/recommend", methods=["POST"])
@role_required("staff")
def reward_recommend(worker_id):
    db = get_db()
    staff = _staff_profile()
    worker = find_worker(worker_id, staff["theater_id"], active_only=True) if staff else None
    if not worker:
        abort(404)
    cycle = request.form.get("cycle") or RewardService.cycle_key()
    evidence = RewardService.eligibility(worker["_id"], cycle)
    if not evidence["eligible"]:
        flash("The available evidence does not meet the configured thresholds.", "warning")
        return redirect(url_for("staff.reward_reviews"))
    now = datetime.now(UTC).replace(tzinfo=None)
    db.reward_awards.update_one({"worker_id": worker["_id"], "cycle": cycle}, {"$setOnInsert": {
        "worker_id": worker["_id"], "worker_name": worker["name"], "theater_id": staff["theater_id"],
        "cycle": cycle, "status": "Recommended", "evidence": evidence,
        "recommended_by": staff["user_id"], "recommended_at": now, "created_at": now}}, upsert=True)
    log_audit("REWARD_RECOMMENDED", "worker", worker["_id"], f"Recommended cycle {cycle}")
    flash("Reward recommendation recorded for independent approval.", "success")
    return redirect(url_for("staff.reward_reviews"))


@staff_bp.route("/staff/rewards/<award_id>/review", methods=["POST"])
@role_required("staff")
def reward_review(award_id):
    db = get_db()
    staff = _staff_profile()
    try:
        award = db.reward_awards.find_one({"_id": ObjectId(award_id), "theater_id": staff["theater_id"]}) if staff else None
    except Exception:
        award = None
    if not award:
        abort(404)
    decision = request.form.get("decision")
    reason = request.form.get("reason", "").strip()
    if decision not in {"approve", "reject"} or not reason:
        flash("A valid decision and reason are required.", "danger")
        return redirect(url_for("staff.reward_reviews"))
    if award.get("recommended_by") == staff["user_id"] and decision == "approve":
        flash("The recommender cannot also approve this award.", "danger")
        return redirect(url_for("staff.reward_reviews"))
    now = datetime.now(UTC).replace(tzinfo=None)
    status = "Approved" if decision == "approve" else "Rejected"
    db.reward_awards.update_one({"_id": award["_id"], "status": "Recommended"}, {"$set": {
        "status": status, "reviewed_by": staff["user_id"], "reviewed_at": now, "review_reason": reason}})
    award = db.reward_awards.find_one({"_id": award["_id"]})
    if decision == "approve":
        _, error = RewardService.issue_credits(award, staff["user_id"])
        if error:
            flash(error, "warning")
            return redirect(url_for("staff.reward_reviews"))
    log_audit(f"REWARD_{status.upper()}", "reward_award", award["_id"], reason)
    flash(f"Award {status.lower()} with an auditable reason.", "success")
    return redirect(url_for("staff.reward_reviews"))


@staff_bp.route("/staff/payroll", methods=["GET", "POST"])
@role_required("staff")
def payroll():
    db = get_db()
    staff = _staff_profile()
    if not staff:
        abort(403)
    if request.method == "POST":
        worker = find_worker(request.form.get("worker_id"), staff["theater_id"], active_only=True)
        if not worker:
            abort(404)
        _, error = PayrollService.calculate_draft(worker["_id"], request.form.get("period", ""), staff["user_id"])
        flash(error or "Payroll draft calculated. It still requires independent approval.", "danger" if error else "success")
        return redirect(url_for("staff.payroll"))
    records = list(db.payroll.find({"theater_id": staff["theater_id"]}).sort("period", -1))
    return render_template("staff/payroll.html", records=records,
                           workers=list_workers(staff["theater_id"], active_only=True), policy=PayrollService.policy())


@staff_bp.route("/staff/support", methods=["GET", "POST"])
@role_required("staff")
def support():
    db = get_db()
    staff = _staff_profile()
    if not staff:
        abort(403)
    if request.method == "POST":
        try:
            ticket_id = ObjectId(request.form.get("ticket_id"))
        except Exception:
            ticket_id = None
        ticket = db.support_tickets.find_one({"_id": ticket_id, "theater_id": staff["theater_id"]}) if ticket_id else None
        reply = request.form.get("reply", "").strip()
        status = request.form.get("status", "In Progress")
        if not ticket or not reply or status not in {"Open", "In Progress", "Waiting on Customer", "Resolved", "Escalated"}:
            flash("A scoped ticket, reply, and valid status are required.", "danger")
        else:
            now = datetime.now(UTC).replace(tzinfo=None)
            db.support_tickets.update_one({"_id": ticket["_id"]}, {"$push": {"messages": {
                "author_id": staff["user_id"], "author_role": "staff", "body": reply[:3000], "created_at": now}},
                "$set": {"status": status, "assigned_to": staff["user_id"], "updated_at": now}})
            log_audit("SUPPORT_TICKET_REPLIED", "support_ticket", ticket["_id"], f"Status {status}")
            flash("Support reply recorded.", "success")
        return redirect(url_for("staff.support"))
    tickets = list(db.support_tickets.find({"theater_id": staff["theater_id"]}).sort("updated_at", -1))
    return render_template("staff/support.html", tickets=tickets)

@staff_bp.route("/staff/workers/<worker_id>/edit", methods=["POST"])
@role_required("staff")
def worker_edit(worker_id):
    db = get_db()
    staff = db.staff.find_one({"user_id": ObjectId(session["user_id"])})
    worker = find_worker(worker_id, staff["theater_id"]) if staff else None
    if not worker:
        abort(404)
    db.users.update_one({"_id": worker["_id"]}, {"$set": {"name": request.form.get("name", worker["name"]).strip()}})
    update_worker(worker["_id"], {
        "department": request.form.get("department", worker.get("department", "")).strip(),
        "designation": request.form.get("designation", worker.get("designation", "")).strip(),
        "shift_timings": request.form.get("shift_timings", worker.get("shift_timings", "")).strip(),
    })
    flash("Worker updated.", "success")
    return redirect(url_for("staff.workers_list"))

@staff_bp.route("/staff/workers/<worker_id>/delete", methods=["POST"])
@role_required("staff")
def worker_delete(worker_id):
    db = get_db()
    staff = db.staff.find_one({"user_id": ObjectId(session["user_id"])})
    worker = find_worker(worker_id, staff["theater_id"]) if staff else None
    if not worker:
        abort(404)
    now = datetime.now(UTC).replace(tzinfo=None)
    db.users.update_one({"_id": worker["_id"]}, {"$set": {"status": "inactive", "deactivated_at": now}})
    db.workers.update_one({"user_id": worker["_id"]}, {"$set": {"status": "inactive", "deactivated_at": now}})
    db.user_sessions.delete_many({"user_id": worker["_id"]})
    log_audit("WORKER_DEACTIVATED", "worker", worker["_id"], "Employment access retired; history retained")
    flash("Worker access deactivated. Tasks, attendance, payroll, and audit history were retained.", "success")
    return redirect(url_for("staff.workers_list"))

@staff_bp.route("/staff/customers/create", methods=["POST"])
@role_required("staff")
def customer_create():
    db = get_db()
    staff = _staff_profile()
    if not staff:
        abort(403)
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    if not name or not email or len(password) < 10 or db.users.find_one({"email": email}):
        flash("A name, unique email, and password of at least 10 characters are required.", "danger")
        return redirect(url_for("staff.customers_list"))
    now = datetime.now(UTC).replace(tzinfo=None)
    result = db.users.insert_one({"name": name, "email": email, "phone": request.form.get("phone", "").strip(),
        "password_hash": generate_password_hash(password),
        "role": "customer", "status": "active", "created_at": now})
    db.customers.insert_one({"user_id": result.inserted_id, "name": name, "email": email,
        "phone": request.form.get("phone", "").strip(), "tier": "Free", "favorite_movies": [],
        "loyalty_points": 0, "total_bookings": 0, "total_spent": 0, "status": "active"})
    db.customer_theater_access.update_one(
        {"customer_id": result.inserted_id, "theater_id": staff["theater_id"]},
        {"$set": {"status": "active", "created_by": staff["user_id"], "updated_at": now}}, upsert=True)
    log_audit("CUSTOMER_ACCESS_CREATED", "customer", result.inserted_id, "Scoped to staff theater")
    flash("Customer created.", "success")
    return redirect(url_for("staff.customers_list"))

@staff_bp.route("/staff/customers/<customer_id>/edit", methods=["POST"])
@role_required("staff")
def customer_edit(customer_id):
    db = get_db()
    staff = _staff_profile()
    customer = _scoped_customer(db, staff["theater_id"], customer_id) if staff else None
    if not customer:
        abort(404)
    customer_oid = customer["_id"]
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    status = request.form.get("status", "active")
    if not name or not email or status not in {"active", "inactive"} or db.users.find_one({"email": email, "_id": {"$ne": customer_oid}}):
        flash("Valid details and a unique email are required.", "danger")
        return redirect(url_for("staff.customers_list"))
    db.users.update_one({"_id": customer_oid, "role": "customer"}, {"$set": {"name": name, "email": email}})
    db.customers.update_one({"user_id": customer_oid}, {"$set": {"name": name, "email": email}})
    db.customer_theater_access.update_one(
        {"customer_id": customer_oid, "theater_id": staff["theater_id"]},
        {"$set": {"status": status, "updated_by": staff["user_id"],
                  "updated_at": datetime.now(UTC).replace(tzinfo=None)}}, upsert=True)
    log_audit("CUSTOMER_THEATER_ACCESS_UPDATED", "customer", customer_oid, f"Local status {status}")
    flash("Customer details and theater access updated.", "success")
    return redirect(url_for("staff.customers_list"))

@staff_bp.route("/staff/customers/<customer_id>/delete", methods=["POST"])
@role_required("staff")
def customer_delete(customer_id):
    db = get_db()
    staff = _staff_profile()
    customer = _scoped_customer(db, staff["theater_id"], customer_id) if staff else None
    if not customer:
        abort(404)
    now = datetime.now(UTC).replace(tzinfo=None)
    db.customer_theater_access.update_one(
        {"customer_id": customer["_id"], "theater_id": staff["theater_id"]},
        {"$set": {"status": "inactive", "updated_by": staff["user_id"], "updated_at": now}}, upsert=True)
    log_audit("CUSTOMER_THEATER_ACCESS_REVOKED", "customer", customer["_id"], "History retained")
    flash("Customer access for this theater was deactivated; account and booking history were retained.", "success")
    return redirect(url_for("staff.customers_list"))

@staff_bp.route("/staff/movies", methods=["GET", "POST"])
@role_required("staff")
def movies():
    db = get_db()
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        if not title:
            flash("Movie title is required.", "danger")
            return redirect(url_for("staff.movies"))
        poster_url = url_for("static", filename="images/default_poster.svg")
        poster = request.files.get("poster")
        if poster and poster.filename:
            ext = os.path.splitext(secure_filename(poster.filename))[1].lower()
            if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
                flash("Poster must be JPG, PNG, or WebP.", "danger")
                return redirect(url_for("staff.movies"))
            upload_dir = os.path.join(current_app.static_folder, "uploads", "posters")
            os.makedirs(upload_dir, exist_ok=True)
            filename = f"{uuid4().hex}{ext}"
            poster.save(os.path.join(upload_dir, filename))
            poster_url = url_for("static", filename=f"uploads/posters/{filename}")
        db.movies.insert_one({"title": title, "original_title": title,
            "language": request.form.get("language", "English").strip(),
            "genres": [x.strip() for x in request.form.get("genres", "").split(",") if x.strip()],
            "runtime": request.form.get("runtime", type=int) or 0, "rating": 0,
            "director": request.form.get("director", "").strip(), "overview": request.form.get("overview", "").strip(),
            "release_date": request.form.get("release_date", ""), "status": request.form.get("status", "Now Showing"),
            "poster_url": poster_url, "cast": [], "created_by_staff": ObjectId(session["user_id"]),
            "created_at": datetime.now(UTC).replace(tzinfo=None)})
        flash("Movie added with poster.", "success")
        return redirect(url_for("staff.movies"))
    return render_template("staff/movies.html", movies=list(db.movies.find().sort("created_at", -1)))
