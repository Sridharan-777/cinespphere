from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from datetime import UTC, datetime
import calendar
from bson import ObjectId
from cinesphere.models.database import get_db
from cinesphere.models.worker_model import find_worker, update_worker
from cinesphere.utils.decorators import role_required
from cinesphere.utils.audit import log_audit
from cinesphere.services.ticket_service import TicketService
from cinesphere.services.reward_service import RewardService
from cinesphere.services.payroll_service import PayrollService

worker_bp = Blueprint("worker", __name__)

@worker_bp.route("/worker/dashboard")
@role_required("worker")
def dashboard():
    db = get_db()
    uid = ObjectId(session["user_id"])
    worker = find_worker(uid)
    if not worker:
        flash("Worker profile not found.", "warning")
        return render_template("errors/403.html"), 403

    # Assigned tasks
    tasks = list(db.tasks.find({"assigned_worker_id": worker["_id"]}).sort("created_at", -1))
    
    # Today attendance
    today_str = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d")
    attendance = db.attendance.find_one({"worker_id": worker["_id"], "date": today_str})

    now = datetime.now(UTC).replace(tzinfo=None)
    month_prefix = now.strftime("%Y-%m")
    attended_days = db.attendance.count_documents({
        "worker_id": worker["_id"], "date": {"$regex": f"^{month_prefix}"}, "status": "Present"
    })
    working_days = sum(1 for day in range(1, now.day + 1)
                       if calendar.weekday(now.year, now.month, day) < 5)
    completed_tasks = db.tasks.count_documents({"assigned_worker_id": worker["_id"], "status": "Completed"})
    eligibility = RewardService.eligibility(worker["_id"])
    reward_coins = int(worker.get("bonus_coins", 0) or 0)
    now = datetime.now(UTC).replace(tzinfo=None)
    available_rewards = db.reward_credits.count_documents({
        "worker_id": uid,
        "status": "available",
        "expires_at": {"$gt": now},
    })
    latest_payroll = db.payroll.find_one(
        {"worker_id": uid, "status": {"$in": ["Approved", "Paid (recorded)"]}},
        sort=[("period", -1)],
    )
    worker_metrics = {
        "attendance_percentage": round((attended_days / working_days) * 100, 1) if working_days else 0,
        "attended_days": attended_days,
        "working_days": working_days,
        "rewards_collected": db.reward_credits.count_documents({"worker_id": uid, "status": "consumed"}),
        "available_rewards": available_rewards,
        "reward_coins": reward_coins,
        "eligibility": eligibility,
        "latest_payroll": latest_payroll,
    }

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

    benefit_bookings = list(db.bookings.find({"customer_id": uid, "funding_source": "worker_reward"}).sort("created_at", -1))
    benefits = {
        "free_remaining": available_rewards,
        "family_remaining": 0,
        "bookings": benefit_bookings,
        "month": RewardService.cycle_key(),
        "policy_status": RewardService.policy().get("status", "draft"),
        "eligibility": eligibility,
    }

    return render_template("worker/dashboard.html",
                           worker=worker,
                           tasks=tasks,
                           attendance=attendance,
                           worker_metrics=worker_metrics,
                           patron_stats=patron_stats,
                           benefits=benefits)

@worker_bp.route("/worker/tasks")
@role_required("worker")
def tasks_list():
    db = get_db()
    uid = ObjectId(session["user_id"])
    worker = find_worker(uid)
    tasks = list(db.tasks.find({"assigned_worker_id": worker["_id"]}).sort("created_at", -1))
    return render_template("worker/tasks.html", worker=worker, tasks=tasks)

@worker_bp.route("/worker/tasks/<task_id>/update-status", methods=["POST"])
@role_required("worker")
def update_task_status(task_id):
    db = get_db()
    uid = ObjectId(session["user_id"])
    worker = find_worker(uid)
    
    try:
        task_oid = ObjectId(task_id)
    except Exception:
        flash("Invalid task identifier.", "danger")
        return redirect(url_for("worker.dashboard"))
    task = db.tasks.find_one({"_id": task_oid, "assigned_worker_id": worker["_id"]})
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
        update_worker(worker["_id"], {"current_task": "Available / Standby"})
    elif new_status == "In Progress":
        update_worker(worker["_id"], {"current_task": task["title"]})

    db.tasks.update_one({"_id": task["_id"]}, {"$set": update_fields})
    log_audit("WORKER_UPDATE_TASK", "task", task["_id"], f"Worker {worker['name']} updated task {task['title']} to {new_status}")
    flash(f"Task status changed to {new_status}.", "success")
    return redirect(url_for("worker.dashboard"))

@worker_bp.route("/worker/attendance", methods=["GET", "POST"])
@role_required("worker")
def attendance():
    db = get_db()
    uid = ObjectId(session["user_id"])
    worker = find_worker(uid)
    today_str = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d")

    if request.method == "POST":
        action = request.form.get("action")
        now = datetime.now(UTC).replace(tzinfo=None)
        existing = db.attendance.find_one({"worker_id": worker["_id"], "date": today_str})
        if action == "check_in" and not existing:
            db.attendance.insert_one({"worker_id": worker["_id"], "worker_name": worker["name"],
                "theater_id": worker["theater_id"], "date": today_str, "check_in": now.strftime("%I:%M %p"),
                "check_out": None, "status": "Present", "source": "worker_self_service", "created_at": now})
            log_audit("WORKER_CHECK_IN", "attendance", worker["_id"], "Worker checked in")
            flash("Check-in recorded.", "success")
        elif action == "check_out" and existing and not existing.get("check_out"):
            db.attendance.update_one({"_id": existing["_id"]}, {"$set": {"check_out": now.strftime("%I:%M %p"), "updated_at": now}})
            log_audit("WORKER_CHECK_OUT", "attendance", existing["_id"], "Worker checked out")
            flash("Check-out recorded.", "success")
        else:
            flash("That attendance action is not currently available.", "warning")
        return redirect(url_for("worker.attendance"))

    records = list(db.attendance.find({"worker_id": worker["_id"]}).sort("date", -1).limit(60))
    today_rec = db.attendance.find_one({"worker_id": worker["_id"], "date": today_str})
    month_prefix = today_str[:7]
    month_records = [record for record in records if str(record.get("date", "")).startswith(month_prefix)]
    present_statuses = {"Present", "Corrected Present"}
    present_days = sum(1 for record in month_records if record.get("status") in present_statuses)
    scheduled_shifts = db.shifts.count_documents({
        "worker_id": worker["_id"],
        "date": {"$regex": f"^{month_prefix}"},
        "status": {"$in": ["Scheduled", "Completed"]},
    })
    attendance_summary = {
        "cycle": month_prefix,
        "present_days": present_days,
        "scheduled_shifts": scheduled_shifts,
        "completed_shifts": sum(1 for record in month_records if record.get("check_out")),
        "open_shift": any(record.get("check_in") and not record.get("check_out") for record in month_records),
        "attendance_rate": round(min(present_days, scheduled_shifts) / scheduled_shifts * 100, 1)
        if scheduled_shifts else None,
    }
    return render_template("worker/attendance.html", worker=worker, records=records,
                           today_rec=today_rec, attendance_summary=attendance_summary)

@worker_bp.route("/worker/profile")
@role_required("worker")
def profile():
    db = get_db()
    uid = ObjectId(session["user_id"])
    worker = find_worker(uid)
    user = db.users.find_one({"_id": uid})
    return render_template("worker/profile.html", worker=worker, user=user)


@worker_bp.route("/worker/rewards")
@role_required("worker")
def rewards():
    db = get_db()
    uid = ObjectId(session["user_id"])
    worker = find_worker(uid)
    now = datetime.now(UTC).replace(tzinfo=None)
    credits = list(db.reward_credits.find({"worker_id": uid}).sort("issued_at", -1))
    awards = list(db.reward_awards.find({"worker_id": uid}).sort("cycle", -1))
    reward_summary = {
        "available": sum(1 for credit in credits if credit.get("status") == "available"
                         and credit.get("expires_at") and credit["expires_at"] > now),
        "reserved": sum(1 for credit in credits if credit.get("status") == "reserved"),
        "used": sum(1 for credit in credits if credit.get("status") == "consumed"),
        "expired": sum(1 for credit in credits if credit.get("status") == "expired"
                       or (credit.get("status") == "available" and credit.get("expires_at")
                           and credit["expires_at"] <= now)),
        "coins": int(worker.get("bonus_coins", 0) or 0) if worker else 0,
    }
    return render_template("worker/rewards.html", policy=RewardService.policy(),
                           eligibility=RewardService.eligibility(uid), credits=credits,
                           awards=awards, reward_summary=reward_summary, now=now)


@worker_bp.route("/worker/payroll", methods=["GET", "POST"])
@role_required("worker")
def payroll():
    db = get_db()
    uid = ObjectId(session["user_id"])
    worker = find_worker(uid)
    if request.method == "POST":
        try:
            payroll_id = ObjectId(request.form.get("payroll_id"))
        except Exception:
            payroll_id = None
        record = db.payroll.find_one({
            "_id": payroll_id,
            "worker_id": uid,
            "status": {"$in": ["Approved", "Paid (recorded)"]},
        }) if payroll_id else None
        message = request.form.get("message", "").strip()
        if not record or not message:
            flash("Choose your payslip and describe the discrepancy.", "danger")
        else:
            db.payroll_disputes.insert_one({"payroll_id": record["_id"], "worker_id": uid,
                "theater_id": record["theater_id"], "message": message[:1000], "status": "Open",
                "created_at": datetime.now(UTC).replace(tzinfo=None)})
            log_audit("PAYROLL_DISCREPANCY_RAISED", "payroll", record["_id"], "Worker raised a discrepancy")
            flash("Payroll discrepancy sent for review.", "success")
        return redirect(url_for("worker.payroll"))
    records = list(db.payroll.find({
        "worker_id": uid,
        "status": {"$in": ["Approved", "Paid (recorded)"]},
    }).sort("period", -1))
    disputes = list(db.payroll_disputes.find({"worker_id": uid}).sort("created_at", -1))
    return render_template("worker/payroll.html", worker=worker, records=records,
                           latest_record=records[0] if records else None,
                           disputes=disputes, policy=PayrollService.policy())


@worker_bp.route("/worker/admission", methods=["GET", "POST"])
@role_required("worker")
def admission():
    db = get_db()
    worker = find_worker(ObjectId(session["user_id"]), active_only=True)
    duties = set(worker.get("authorized_duties") or ["admission", "ticket_validation", "ticket_counter"]) if worker else set()
    if not worker or not duties.intersection({"admission", "ticket_validation", "ticket_counter"}):
        status_code = 403 if request.method == "POST" else 200
        return render_template("worker/admission.html", authorized=False, result=None), status_code
    result = None
    if request.method == "POST":
        token = request.form.get("token", "").strip()
        code = request.form.get("booking_code", "").strip().upper()
        data = TicketService.decode(token) if token else None
        try:
            query = {"_id": ObjectId(data["booking_id"])} if data else ({"booking_code": code} if code else None)
        except Exception:
            query = None
        booking = db.bookings.find_one(query) if query else None
        if not booking:
            result = {"ok": False, "message": "Ticket not found or signature invalid."}
        elif str(booking.get("theater_id")) != str(worker.get("theater_id")):
            result = {"ok": False, "message": "Wrong theater for this ticket."}
        elif booking.get("status") != "Confirmed":
            result = {"ok": False, "message": f"Ticket status is {booking.get('status', 'unknown')}."}
        elif booking.get("admission", {}).get("used_at"):
            result = {"ok": False, "message": "Ticket was already used."}
        elif data and int(data.get("version", 0)) != int(booking.get("admission_token_version", 1)):
            result = {"ok": False, "message": "Ticket token has been replaced."}
        else:
            now = datetime.now(UTC).replace(tzinfo=None)
            updated = db.bookings.update_one({"_id": booking["_id"], "status": "Confirmed",
                "admission.used_at": {"$exists": False}}, {"$set": {"admission": {
                "used_at": now, "validated_by": worker["_id"], "theater_id": worker["theater_id"]}}})
            result = {"ok": updated.modified_count == 1,
                      "message": f"Admission validated for {booking.get('movie_title')} ({len(booking.get('seats', []))} seats)." if updated.modified_count == 1 else "Ticket was already used."}
            log_audit("TICKET_ADMISSION_VALIDATED", "booking", booking["_id"], result["message"])

    recent_admissions = list(db.bookings.find({
        "theater_id": worker.get("theater_id"),
        "admission.used_at": {"$exists": True}
    }).sort("admission.used_at", -1).limit(8)) if worker else []

    return render_template("worker/admission.html", authorized=True, result=result,
                           worker=worker, recent_admissions=recent_admissions)
