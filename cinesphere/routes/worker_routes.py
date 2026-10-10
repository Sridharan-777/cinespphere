from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from datetime import UTC, datetime
from bson import ObjectId
from cinesphere.models.database import get_db
from cinesphere.utils.decorators import role_required
from cinesphere.utils.audit import log_audit

worker_bp = Blueprint("worker", __name__)

@worker_bp.route("/worker/dashboard")
@role_required("worker")
def dashboard():
    db = get_db()
    uid = ObjectId(session["user_id"])
    worker = db.workers.find_one({"user_id": uid})
    if not worker:
        flash("Worker profile not found.", "warning")
        return render_template("errors/403.html"), 403

    # Assigned tasks
    tasks = list(db.tasks.find({"assigned_worker_id": worker["_id"]}).sort("created_at", -1))
    
    # Today attendance
    today_str = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d")
    attendance = db.attendance.find_one({"worker_id": worker["_id"], "date": today_str})

    # Footfall & approaching customers statistics
    theater_id = worker.get("theater_id")
    today_bookings = list(db.bookings.find({"theater_id": theater_id, "show_date": today_str, "status": "Confirmed"}))
    today_patrons = sum(len(b.get("seats", [])) for b in today_bookings)

    all_theater_bookings = list(db.bookings.find({"theater_id": theater_id, "status": "Confirmed"}))
    total_patrons = sum(len(b.get("seats", [])) for b in all_theater_bookings)

    active_shows = db.showtimes.count_documents({"theater_id": theater_id, "show_date": today_str, "status": "active"})

    patron_stats = {
        "today_patrons": today_patrons,
        "total_patrons": total_patrons,
        "active_shows": active_shows
    }

    month_key = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m")
    benefit_bookings = list(db.bookings.find({
        "customer_id": uid,
        "employee_benefit.month": month_key,
        "status": {"$ne": "Cancelled"}
    }).sort("created_at", -1))
    free_used = sum(len(b.get("seats", [])) for b in benefit_bookings
                    if b.get("employee_benefit", {}).get("type") == "free_pass")
    family_used = sum(len(b.get("seats", [])) for b in benefit_bookings
                      if b.get("employee_benefit", {}).get("type") == "family_discount")
    benefits = {
        "free_remaining": max(0, 2 - free_used),
        "family_remaining": max(0, 4 - family_used),
        "bookings": benefit_bookings,
        "month": month_key
    }

    return render_template("worker/dashboard.html",
                           worker=worker,
                           tasks=tasks,
                           attendance=attendance,
                           patron_stats=patron_stats,
                           benefits=benefits)

@worker_bp.route("/worker/tasks")
@role_required("worker")
def tasks_list():
    db = get_db()
    uid = ObjectId(session["user_id"])
    worker = db.workers.find_one({"user_id": uid})
    tasks = list(db.tasks.find({"assigned_worker_id": worker["_id"]}).sort("created_at", -1))
    return render_template("worker/tasks.html", worker=worker, tasks=tasks)

@worker_bp.route("/worker/tasks/<task_id>/update-status", methods=["POST"])
@role_required("worker")
def update_task_status(task_id):
    db = get_db()
    uid = ObjectId(session["user_id"])
    worker = db.workers.find_one({"user_id": uid})
    
    task = db.tasks.find_one({"_id": ObjectId(task_id), "assigned_worker_id": worker["_id"]})
    if not task:
        flash("Task not found or not assigned to you.", "danger")
        return redirect(url_for("worker.dashboard"))

    new_status = request.form.get("status") # In Progress, Completed
    notes = request.form.get("notes", "").strip()

    if new_status not in {"In Progress", "Completed"}:
        flash("Invalid task status.", "danger")
        return redirect(url_for("worker.dashboard"))

    update_fields = {"status": new_status}
    if notes:
        update_fields["notes"] = notes
    if new_status == "Completed":
        update_fields["completed_at"] = datetime.now(UTC).replace(tzinfo=None)
        # Clear current task in worker record
        db.workers.update_one({"_id": worker["_id"]}, {"$set": {"current_task": "Available / Standby"}})
    elif new_status == "In Progress":
        db.workers.update_one({"_id": worker["_id"]}, {"$set": {"current_task": task["title"]}})

    db.tasks.update_one({"_id": task["_id"]}, {"$set": update_fields})
    log_audit("WORKER_UPDATE_TASK", "task", task["_id"], f"Worker {worker['name']} updated task {task['title']} to {new_status}")
    flash(f"Task status changed to {new_status}.", "success")
    return redirect(url_for("worker.dashboard"))

@worker_bp.route("/worker/attendance", methods=["GET", "POST"])
@role_required("worker")
def attendance():
    db = get_db()
    uid = ObjectId(session["user_id"])
    worker = db.workers.find_one({"user_id": uid})
    today_str = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d")

    if request.method == "POST":
        action = request.form.get("action") # check_in or check_out
        existing = db.attendance.find_one({"worker_id": worker["_id"], "date": today_str})

        if action == "check_in":
            if not existing:
                db.attendance.insert_one({
                    "worker_id": worker["_id"],
                    "worker_name": worker["name"],
                    "theater_id": worker["theater_id"],
                    "date": today_str,
                    "check_in": datetime.now(UTC).replace(tzinfo=None).strftime("%I:%M %p"),
                    "check_out": None,
                    "status": "Present",
                    "created_at": datetime.now(UTC).replace(tzinfo=None)
                })
                flash("Attendance recorded: Shift check-in successful!", "success")
            else:
                flash("You have already checked in today.", "info")

        elif action == "check_out":
            if existing:
                db.attendance.update_one(
                    {"_id": existing["_id"]},
                    {"$set": {"check_out": datetime.now(UTC).replace(tzinfo=None).strftime("%I:%M %p")}}
                )
                flash("Checked out from shift successfully!", "success")
            else:
                flash("Please check in first before checking out.", "warning")

        return redirect(url_for("worker.attendance"))

    records = list(db.attendance.find({"worker_id": worker["_id"]}).sort("date", -1).limit(30))
    today_rec = db.attendance.find_one({"worker_id": worker["_id"], "date": today_str})
    return render_template("worker/attendance.html", worker=worker, records=records, today_rec=today_rec)

@worker_bp.route("/worker/profile")
@role_required("worker")
def profile():
    db = get_db()
    uid = ObjectId(session["user_id"])
    worker = db.workers.find_one({"user_id": uid})
    user = db.users.find_one({"_id": uid})
    return render_template("worker/profile.html", worker=worker, user=user)
