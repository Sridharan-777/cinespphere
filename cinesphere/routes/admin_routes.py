from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from datetime import UTC, datetime
import re
from flask import current_app, abort
from bson import ObjectId
from werkzeug.security import generate_password_hash
from cinesphere.models.database import get_db
from cinesphere.models.worker_model import count_workers, find_worker, list_workers
from cinesphere.utils.decorators import role_required
from cinesphere.services.report_service import ReportService
from cinesphere.services.tmdb_service import tmdb_service
from cinesphere.utils.audit import log_audit
from cinesphere.services.reward_service import RewardService
from cinesphere.services.payroll_service import PayrollService
from cinesphere.utils.media import youtube_embed_url

admin_bp = Blueprint("admin", __name__)

@admin_bp.route("/admin/dashboard")
@role_required("admin")
def dashboard():
    db = get_db()
    stats = ReportService.get_global_admin_stats()
    theaters = list(db.theaters.find({"status": "active"}))
    for t in theaters:
        t["staff_count"] = db.staff.count_documents({"theater_id": t["_id"], "status": "active"})
        t["worker_count"] = count_workers(t["_id"], active_only=True)
        t["screen_count"] = db.screens.count_documents({"theater_id": t["_id"], "status": "active"})

    recent_logs = list(db.audit_logs.find().sort("created_at", -1).limit(6))
    return render_template("admin/dashboard.html",
                           stats=stats,
                           theaters=theaters,
                           recent_logs=recent_logs)

@admin_bp.route("/admin/theaters", methods=["GET", "POST"])
@role_required("admin")
def theaters():
    db = get_db()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        address = request.form.get("address", "").strip()
        city = request.form.get("city", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()

        if not name or not address or not city:
            flash("Name, address, and city are required.", "danger")
            return redirect(url_for("admin.theaters"))

        theater_doc = {
            "name": name,
            "address": address,
            "city": city,
            "phone": phone,
            "email": email,
            "facilities": ["Parking", "Dolby Atmos", "Food Court", "3D", "VIP Lounge"],
            "staff_capacity": 5,
            "worker_capacity": 20,
            "status": "active",
            "created_at": datetime.now(UTC).replace(tzinfo=None)
        }
        res = db.theaters.insert_one(theater_doc)

        # Create 3 default screens
        for i in range(1, 4):
            db.screens.insert_one({
                "theater_id": res.inserted_id,
                "name": f"Screen {i}",
                "screen_number": i,
                "screen_type": "IMAX" if i == 1 else "Standard",
                "capacity": 48,
                "status": "active",
                "created_at": datetime.now(UTC).replace(tzinfo=None)
            })

        log_audit("ADMIN_CREATE_THEATER", "theater", res.inserted_id, f"Admin created theater {name}")
        flash(f"Theater {name} created successfully with default screens.", "success")
        return redirect(url_for("admin.theaters"))

    theaters_list = list(db.theaters.find().sort("created_at", -1))
    for t in theaters_list:
        t["staff_count"] = db.staff.count_documents({"theater_id": t["_id"], "status": "active"})
        t["worker_count"] = count_workers(t["_id"], active_only=True)
        t["screen_count"] = db.screens.count_documents({"theater_id": t["_id"], "status": "active"})

    return render_template("admin/theaters.html", theaters=theaters_list)

@admin_bp.route("/admin/theaters/<theater_id>/edit", methods=["POST"])
@role_required("admin")
def edit_theater(theater_id):
    db = get_db()
    try:
        t_id = ObjectId(theater_id)
    except Exception:
        flash("Invalid theater identifier.", "danger")
        return redirect(url_for("admin.theaters"))

    updates = {
        "name": request.form.get("name", "").strip(),
        "address": request.form.get("address", "").strip(),
        "city": request.form.get("city", "").strip(),
        "phone": request.form.get("phone", "").strip(),
        "email": request.form.get("email", "").strip(),
        "status": request.form.get("status", "active") if request.form.get("status", "active") in {"active", "inactive"} else "active",
        "updated_at": datetime.now(UTC).replace(tzinfo=None)
    }
    if not updates["name"] or not updates["address"] or not updates["city"]:
        flash("Name, address, and city are required.", "danger")
        return redirect(url_for("admin.theater_details", theater_id=theater_id))

    result = db.theaters.update_one({"_id": t_id}, {"$set": updates})
    if not result.matched_count:
        flash("Theater not found.", "danger")
        return redirect(url_for("admin.theaters"))

    db.staff.update_many({"theater_id": t_id}, {"$set": {"theater_name": updates["name"]}})
    db.users.update_many({"role": "worker", "worker_profile.theater_id": t_id}, {"$set": {"worker_profile.theater_name": updates["name"]}})
    log_audit("ADMIN_UPDATE_THEATER", "theater", t_id, f"Admin updated theater {updates['name']}")
    flash("Theater details updated.", "success")
    return redirect(url_for("admin.theater_details", theater_id=theater_id))

@admin_bp.route("/admin/theaters/<theater_id>/delete", methods=["POST"])
@role_required("admin")
def delete_theater(theater_id):
    db = get_db()
    try:
        t_id = ObjectId(theater_id)
    except Exception:
        flash("Invalid theater identifier.", "danger")
        return redirect(url_for("admin.theaters"))

    theater = db.theaters.find_one({"_id": t_id})
    if not theater:
        flash("Theater not found.", "danger")
        return redirect(url_for("admin.theaters"))
    if db.bookings.count_documents({"theater_id": t_id}) or db.staff.count_documents({"theater_id": t_id}) or count_workers(t_id):
        flash("This theater has linked bookings or personnel. Deactivate it instead of deleting historical data.", "danger")
        return redirect(url_for("admin.theater_details", theater_id=theater_id))

    db.showtimes.delete_many({"theater_id": t_id})
    db.screens.delete_many({"theater_id": t_id})
    db.theaters.delete_one({"_id": t_id})
    log_audit("ADMIN_DELETE_THEATER", "theater", t_id, f"Admin deleted theater {theater.get('name', '')}")
    flash("Theater and its empty screen configuration were deleted.", "success")
    return redirect(url_for("admin.theaters"))

@admin_bp.route("/admin/theaters/<theater_id>")
@role_required("admin")
def theater_details(theater_id):
    db = get_db()
    try:
        t_id = ObjectId(theater_id)
    except Exception:
        flash("Invalid theater identifier.", "danger")
        return redirect(url_for("admin.theaters"))
    theater = db.theaters.find_one({"_id": t_id})
    if not theater:
        flash("Theater not found", "danger")
        return redirect(url_for("admin.theaters"))

    screens = list(db.screens.find({"theater_id": t_id}))
    staff_members = list(db.staff.find({"theater_id": t_id}))
    workers = list_workers(t_id)
    stats = ReportService.get_theater_stats(t_id)

    return render_template("admin/theater_details.html",
                           theater=theater,
                           screens=screens,
                           staff_members=staff_members,
                           workers=workers,
                           stats=stats)

@admin_bp.route("/admin/staff", methods=["GET", "POST"])
@role_required("admin")
def staff_management():
    db = get_db()
    theaters = list(db.theaters.find({"status": "active"}))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        try:
            theater_id = ObjectId(request.form.get("theater_id", ""))
        except Exception:
            flash("Choose a valid active theater.", "danger")
            return redirect(url_for("admin.staff_management"))
        designation = request.form.get("designation", "Operations Supervisor").strip()
        password = request.form.get("password", "Staff@1234").strip()

        theater = db.theaters.find_one({"_id": theater_id, "status": "active"})
        if not name or not email or len(password) < 8 or not theater:
            flash("Name, email, an 8-character password, and an active theater are required.", "danger")
            return redirect(url_for("admin.staff_management"))

        # Check existing staff count in this theater - STRICT MAX 5
        active_staff = db.staff.count_documents({"theater_id": theater_id, "status": "active"})
        if active_staff >= 5:
            flash("This theater already has the maximum capacity of 5 staff members.", "danger")
            return redirect(url_for("admin.staff_management"))

        if db.users.find_one({"email": email}):
            flash("A user with this email already exists.", "danger")
            return redirect(url_for("admin.staff_management"))

        emp_code = f"STF-{active_staff + 1:03d}"

        u_res = db.users.insert_one({
            "name": name,
            "email": email,
            "phone": phone,
            "password_hash": generate_password_hash(password),
            "role": "staff",
            "status": "active",
            "created_at": datetime.now(UTC).replace(tzinfo=None)
        })

        db.staff.insert_one({
            "user_id": u_res.inserted_id,
            "employee_code": emp_code,
            "name": name,
            "email": email,
            "phone": phone,
            "theater_id": theater_id,
            "theater_name": theater["name"] if theater else "CineSphere",
            "designation": designation,
            "status": "active",
            "created_at": datetime.now(UTC).replace(tzinfo=None)
        })

        log_audit("ADMIN_CREATE_STAFF", "staff", u_res.inserted_id, f"Admin added staff {name} to {theater['name'] if theater else ''}")
        flash(f"Staff member {name} assigned successfully ({active_staff + 1}/5).", "success")
        return redirect(url_for("admin.staff_management"))

    staff_list = list(db.staff.find().sort("created_at", -1))
    return render_template("admin/staff.html", staff_list=staff_list, theaters=theaters)

@admin_bp.route("/admin/staff/<staff_id>/toggle-status", methods=["POST"])
@role_required("admin")
def staff_toggle_status(staff_id):
    db = get_db()
    try:
        staff_object_id = ObjectId(staff_id)
    except Exception:
        flash("Invalid staff identifier.", "danger")
        return redirect(url_for("admin.staff_management"))
    staff_doc = db.staff.find_one({"_id": staff_object_id})
    if not staff_doc:
        flash("Staff member not found.", "danger")
        return redirect(url_for("admin.staff_management"))

    new_status = "inactive" if staff_doc.get("status") == "active" else "active"
    if new_status == "active":
        count = db.staff.count_documents({"theater_id": staff_doc["theater_id"], "status": "active"})
        if count >= 5:
            flash("Cannot reactivate: Theater already has 5 active staff members.", "danger")
            return redirect(url_for("admin.staff_management"))

    db.staff.update_one({"_id": staff_doc["_id"]}, {"$set": {"status": new_status}})
    db.users.update_one({"_id": staff_doc["user_id"]}, {"$set": {"status": new_status}})
    log_audit("ADMIN_TOGGLE_STAFF", "staff", staff_doc["_id"], f"Staff status set to {new_status}")
    flash(f"Staff status updated to {new_status}.", "info")
    return redirect(url_for("admin.staff_management"))

@admin_bp.route("/admin/staff/<staff_id>/edit", methods=["POST"])
@role_required("admin")
def staff_edit(staff_id):
    db = get_db()
    try:
        staff_object_id = ObjectId(staff_id)
        theater_id = ObjectId(request.form.get("theater_id", ""))
    except Exception:
        flash("Invalid staff or theater identifier.", "danger")
        return redirect(url_for("admin.staff_management"))

    staff_doc = db.staff.find_one({"_id": staff_object_id})
    theater = db.theaters.find_one({"_id": theater_id, "status": "active"})
    if not staff_doc or not theater:
        flash("Staff member or active theater not found.", "danger")
        return redirect(url_for("admin.staff_management"))

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    designation = request.form.get("designation", "").strip()
    phone = request.form.get("phone", "").strip()
    if not name or not email or not designation:
        flash("Name, email, and designation are required.", "danger")
        return redirect(url_for("admin.staff_management"))
    duplicate = db.users.find_one({"email": email, "_id": {"$ne": staff_doc["user_id"]}})
    if duplicate:
        flash("Another user already uses this email address.", "danger")
        return redirect(url_for("admin.staff_management"))
    if theater_id != staff_doc.get("theater_id") and staff_doc.get("status") == "active":
        active_count = db.staff.count_documents({"theater_id": theater_id, "status": "active"})
        if active_count >= 5:
            flash("The selected theater already has 5 active staff members.", "danger")
            return redirect(url_for("admin.staff_management"))

    updates = {"name": name, "email": email, "phone": phone, "designation": designation,
               "theater_id": theater_id, "theater_name": theater["name"],
               "updated_at": datetime.now(UTC).replace(tzinfo=None)}
    db.staff.update_one({"_id": staff_object_id}, {"$set": updates})
    db.users.update_one({"_id": staff_doc["user_id"]}, {"$set": {
        "name": name, "email": email, "phone": phone, "updated_at": updates["updated_at"]
    }})
    log_audit("ADMIN_UPDATE_STAFF", "staff", staff_object_id, f"Admin updated staff {email}")
    flash("Staff member updated successfully.", "success")
    return redirect(url_for("admin.staff_management"))

@admin_bp.route("/admin/staff/<staff_id>/delete", methods=["POST"])
@role_required("admin")
def staff_delete(staff_id):
    db = get_db()
    try:
        staff_object_id = ObjectId(staff_id)
    except Exception:
        flash("Invalid staff identifier.", "danger")
        return redirect(url_for("admin.staff_management"))
    staff_doc = db.staff.find_one({"_id": staff_object_id})
    if not staff_doc:
        flash("Staff member not found.", "danger")
        return redirect(url_for("admin.staff_management"))
    if db.tasks.count_documents({"created_by_id": staff_doc.get("user_id")}):
        flash("This staff member has task history. Deactivate the account instead of deleting it.", "danger")
        return redirect(url_for("admin.staff_management"))
    db.user_sessions.delete_many({"user_id": staff_doc["user_id"]})
    db.staff.delete_one({"_id": staff_object_id})
    db.users.delete_one({"_id": staff_doc["user_id"]})
    log_audit("ADMIN_DELETE_STAFF", "staff", staff_object_id, f"Admin deleted staff {staff_doc.get('email', '')}")
    flash("Staff account deleted.", "success")
    return redirect(url_for("admin.staff_management"))

@admin_bp.route("/admin/movies", methods=["GET", "POST"])
@role_required("admin")
def movies():
    db = get_db()
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        if not title:
            flash("Movie title is required.", "danger")
            return redirect(url_for("admin.movies"))
        status = request.form.get("status", "Catalog").strip()
        status = status if status in {"Catalog", "Coming Soon", "Now Showing", "Archived"} else "Catalog"
        supplied_trailer = request.form.get("trailer_url", "").strip()
        trailer_url = youtube_embed_url(supplied_trailer)
        if supplied_trailer and not trailer_url:
            flash("Use a valid YouTube trailer URL or video ID.", "danger")
            return redirect(url_for("admin.movies"))
        movie_doc = {
            "title": title,
            "original_title": request.form.get("original_title", title).strip() or title,
            "language": request.form.get("language", "English").strip(),
            "genres": [value.strip() for value in request.form.get("genres", "").split(",") if value.strip()],
            "runtime": max(0, request.form.get("runtime", type=int) or 0),
            "rating": min(10, max(0, request.form.get("rating", type=float) or 0)),
            "director": request.form.get("director", "").strip(),
            "overview": request.form.get("overview", "").strip(),
            "release_date": request.form.get("release_date", "").strip(),
            "status": status,
            "poster_url": request.form.get("poster_url", "").strip() or url_for("static", filename="images/default_poster.svg"),
            "backdrop_url": request.form.get("backdrop_url", "").strip() or url_for("static", filename="images/default_backdrop.svg"),
            "trailer_url": trailer_url,
            "cast": [], "writers": [], "vote_count": 0,
            "created_at": datetime.now(UTC).replace(tzinfo=None),
        }
        result = db.movies.insert_one(movie_doc)
        log_audit("ADMIN_CREATE_MOVIE", "movie", result.inserted_id, f"Admin created movie {title}")
        flash(f"Movie {title} created successfully.", "success")
        return redirect(url_for("admin.movies"))
    movies_list = list(db.movies.find().sort("created_at", -1))
    return render_template("admin/movies.html", movies=movies_list,
                           tmdb_configured=tmdb_service.is_configured)

@admin_bp.route("/admin/movies/<movie_id>/edit", methods=["POST"])
@role_required("admin")
def movie_edit(movie_id):
    db = get_db()
    try:
        object_id = ObjectId(movie_id)
    except Exception:
        flash("Invalid movie identifier.", "danger")
        return redirect(url_for("admin.movies"))
    title = request.form.get("title", "").strip()
    if not title:
        flash("Movie title is required.", "danger")
        return redirect(url_for("admin.movies"))
    supplied_trailer = request.form.get("trailer_url", "").strip()
    trailer_url = youtube_embed_url(supplied_trailer)
    if supplied_trailer and not trailer_url:
        flash("Use a valid YouTube trailer URL or video ID.", "danger")
        return redirect(url_for("admin.movies"))
    status = request.form.get("status", "Catalog").strip()
    status = status if status in {"Catalog", "Coming Soon", "Now Showing", "Archived"} else "Catalog"
    updates = {
        "title": title,
        "original_title": request.form.get("original_title", title).strip() or title,
        "language": request.form.get("language", "English").strip(),
        "genres": [value.strip() for value in request.form.get("genres", "").split(",") if value.strip()],
        "runtime": max(0, request.form.get("runtime", type=int) or 0),
        "rating": min(10, max(0, request.form.get("rating", type=float) or 0)),
        "director": request.form.get("director", "").strip(),
        "overview": request.form.get("overview", "").strip(),
        "release_date": request.form.get("release_date", "").strip(),
        "poster_url": request.form.get("poster_url", "").strip() or url_for("static", filename="images/default_poster.svg"),
        "backdrop_url": request.form.get("backdrop_url", "").strip() or url_for("static", filename="images/default_backdrop.svg"),
        "trailer_url": trailer_url,
        "status": status,
        "updated_at": datetime.now(UTC).replace(tzinfo=None),
    }
    result = db.movies.update_one({"_id": object_id}, {"$set": updates})
    if not result.matched_count:
        flash("Movie not found.", "danger")
    else:
        log_audit("ADMIN_UPDATE_MOVIE", "movie", object_id, f"Admin updated movie {title}")
        flash("Movie updated successfully.", "success")
    return redirect(url_for("admin.movies"))

@admin_bp.route("/admin/movies/<movie_id>/delete", methods=["POST"])
@role_required("admin")
def movie_delete(movie_id):
    db = get_db()
    try:
        object_id = ObjectId(movie_id)
    except Exception:
        flash("Invalid movie identifier.", "danger")
        return redirect(url_for("admin.movies"))
    movie = db.movies.find_one({"_id": object_id})
    if not movie:
        flash("Movie not found.", "danger")
        return redirect(url_for("admin.movies"))
    if db.bookings.count_documents({"movie_id": object_id}):
        flash("This movie has booking history. Change its status instead of deleting it.", "danger")
        return redirect(url_for("admin.movies"))
    db.showtimes.delete_many({"movie_id": object_id})
    db.reviews.delete_many({"movie_id": object_id})
    db.movies.delete_one({"_id": object_id})
    log_audit("ADMIN_DELETE_MOVIE", "movie", object_id, f"Admin deleted movie {movie.get('title', '')}")
    flash("Movie deleted successfully.", "success")
    return redirect(url_for("admin.movies"))

@admin_bp.route("/admin/movies/sync", methods=["POST"])
@role_required("admin")
def sync_tmdb_movies():
    db = get_db()
    try:
        from cinesphere.scripts.sync_movies import sync_movies
        queries = [value.strip() for value in re.split(r"[,\n]", request.form.get("queries", "")) if value.strip()]
        result = sync_movies(queries, db)
        if result["error"]:
            flash(result["error"] + " Add the TMDB key to .env, restart, and retry.", "danger")
            return redirect(url_for("admin.movies"))
        log_audit("ADMIN_SYNC_TMDB", "movie", None,
                  f"Synced {result['synced']} TMDB movies; requested={queries[:10]}")
        message = f"Synchronized {result['synced']} recent and upcoming Indian movies from TMDB."
        if result["requested_missing"]:
            message += " No match for: " + ", ".join(result["requested_missing"])
        flash(message, "warning" if result["requested_missing"] else "success")
    except Exception:
        current_app.logger.exception("TMDB catalog synchronization failed")
        flash("TMDB synchronization failed. Check the server log and provider configuration.", "danger")

    return redirect(url_for("admin.movies"))

@admin_bp.route("/admin/reviews")
@role_required("admin")
def reviews_moderation():
    db = get_db()
    reviews = list(db.reviews.find().sort("created_at", -1))
    for r in reviews:
        movie = db.movies.find_one({"_id": r["movie_id"]})
        r["movie_title"] = movie["title"] if movie else "Unknown Movie"
    return render_template("admin/reviews.html", reviews=reviews)

@admin_bp.route("/admin/reviews/<review_id>/moderate", methods=["POST"])
@role_required("admin")
def moderate_review(review_id):
    db = get_db()
    action = request.form.get("action") # Published, Rejected, Flagged
    if action not in {"Published", "Rejected", "Flagged"}:
        flash("Invalid moderation action.", "danger")
        return redirect(url_for("admin.reviews_moderation"))
    try:
        review_oid = ObjectId(review_id)
    except Exception:
        flash("Invalid review identifier.", "danger")
        return redirect(url_for("admin.reviews_moderation"))
    result = db.reviews.update_one({"_id": review_oid}, {"$set": {"status": action}})
    if not result.matched_count:
        flash("Review not found.", "warning")
        return redirect(url_for("admin.reviews_moderation"))
    log_audit("ADMIN_MODERATE_REVIEW", "review", review_id, f"Review set to {action}")
    flash(f"Review marked as {action}.", "info")
    return redirect(url_for("admin.reviews_moderation"))

@admin_bp.route("/admin/audit-logs")
@role_required("admin")
def audit_logs():
    db = get_db()
    logs = list(db.audit_logs.find().sort("created_at", -1).limit(100))
    return render_template("admin/audit_logs.html", logs=logs)


@admin_bp.route("/admin/rewards")
@role_required("admin")
def rewards_management():
    db = get_db()
    awards = list(db.reward_awards.find().sort([("cycle", -1), ("created_at", -1)]).limit(200))
    credits = list(db.reward_credits.find().sort("issued_at", -1).limit(200))
    return render_template("admin/rewards.html", policy=RewardService.policy(), awards=awards, credits=credits)


@admin_bp.route("/admin/rewards/policy", methods=["POST"])
@role_required("admin")
def reward_policy_update():
    try:
        policy = {
            "status": request.form.get("status", "draft"),
            "cycle": request.form.get("cycle", "monthly"),
            "credits_per_award": int(request.form.get("credits_per_award", 3)),
            "credit_valid_days": int(request.form.get("credit_valid_days", 90)),
            "attendance_threshold": float(request.form.get("attendance_threshold", 95)),
            "maintenance_quality_threshold": float(request.form.get("maintenance_quality_threshold", 90)),
            "minimum_verified_tasks": int(request.form.get("minimum_verified_tasks", 5)),
            "attendance_weight": float(request.form.get("attendance_weight", .5)),
            "quality_weight": float(request.form.get("quality_weight", .5)),
            "tie_policy": request.form.get("tie_policy", "approve_all_above_threshold_within_configured_award_limit").strip(),
            "eligible_seat_categories": request.form.getlist("eligible_seat_categories") or ["Regular"],
            "restore_credit_on_permitted_cancellation": bool(request.form.get("restore_credit_on_permitted_cancellation")),
        }
    except (TypeError, ValueError):
        flash("Policy values must be valid numbers.", "danger")
        return redirect(url_for("admin.rewards_management"))
    if policy["status"] not in {"draft", "active", "paused"} or policy["cycle"] not in {"monthly", "quarterly"} \
            or not 1 <= policy["credits_per_award"] <= 10 or not 1 <= policy["credit_valid_days"] <= 365 \
            or not 0 <= policy["attendance_threshold"] <= 100 or not 0 <= policy["maintenance_quality_threshold"] <= 100 \
            or abs(policy["attendance_weight"] + policy["quality_weight"] - 1) > .001 or not policy["tie_policy"]:
        flash("Policy values are outside the safe configurable limits or weights do not total 1.", "danger")
        return redirect(url_for("admin.rewards_management"))
    now = datetime.now(UTC).replace(tzinfo=None)
    get_db().system_config.update_one({"key": "worker_reward_policy"}, {"$set": {
        "policy": policy, "updated_at": now, "updated_by": ObjectId(session["user_id"])}}, upsert=True)
    log_audit("REWARD_POLICY_UPDATED", "system_config", "worker_reward_policy", f"Policy set to {policy['status']}")
    flash("Reward policy saved with an audit entry.", "success")
    return redirect(url_for("admin.rewards_management"))


@admin_bp.route("/admin/payroll")
@role_required("admin")
def payroll_management():
    records = list(get_db().payroll.find().sort([("period", -1), ("created_at", -1)]).limit(250))
    summary = {"total": len(records), "draft": sum(1 for r in records if r.get("status") == "Draft"),
               "review": sum(1 for r in records if r.get("status") == "Needs Review"),
               "approved": sum(1 for r in records if r.get("status") == "Approved"),
               "payable": sum(float(r.get("net_pay", 0) or 0) for r in records if r.get("status") in {"Draft", "Approved"})}
    return render_template("admin/payroll.html", records=records, summary=summary, policy=PayrollService.policy())


@admin_bp.route("/admin/payroll/<record_id>/approve", methods=["POST"])
@role_required("admin")
def payroll_approve(record_id):
    db = get_db()
    try:
        record = db.payroll.find_one({"_id": ObjectId(record_id)})
    except Exception:
        record = None
    reason = request.form.get("reason", "").strip()
    approver = ObjectId(session["user_id"])
    if not record or record.get("status") != "Draft":
        flash("Only a valid draft can be approved.", "danger")
    elif record.get("calculated_by") == approver:
        flash("The payroll calculator cannot approve the same record.", "danger")
    elif not reason:
        flash("An approval reason is required.", "danger")
    else:
        now = datetime.now(UTC).replace(tzinfo=None)
        db.payroll.update_one({"_id": record["_id"], "status": "Draft"}, {"$set": {
            "status": "Approved", "approved_by": approver, "approved_at": now, "approval_reason": reason}})
        log_audit("PAYROLL_APPROVED", "payroll", record["_id"], reason)
        flash("Payroll approved. This does not transfer money.", "success")
    return redirect(url_for("admin.payroll_management"))


@admin_bp.route("/admin/payroll/<record_id>/record-payment", methods=["POST"])
@role_required("admin")
def payroll_record_payment(record_id):
    db = get_db()
    actor = db.users.find_one({"_id": ObjectId(session["user_id"]), "status": "active"})
    try:
        record = db.payroll.find_one({"_id": ObjectId(record_id)})
    except Exception:
        record = None
    reference = request.form.get("reference", "").strip()
    method = request.form.get("method", "").strip()
    paid_date = request.form.get("paid_date", "").strip()
    if not actor or "payroll_payout" not in actor.get("permissions", []):
        flash("Your account does not have the separate payroll payout-recording permission.", "danger")
    elif not record or record.get("status") != "Approved" or record.get("approved_by") == actor["_id"]:
        flash("A different authorized person must record payment for an approved payroll.", "danger")
    elif not reference or not method or not paid_date:
        flash("Payment date, method, and reference are required.", "danger")
    else:
        now = datetime.now(UTC).replace(tzinfo=None)
        db.payroll.update_one({"_id": record["_id"], "status": "Approved"}, {"$set": {
            "status": "Paid (recorded)", "payment_recorded_by": actor["_id"], "payment_recorded_at": now,
            "payment_date": paid_date, "payment_method": method, "payment_reference": reference}})
        log_audit("PAYROLL_PAYMENT_RECORDED", "payroll", record["_id"], f"Manual reference {reference}")
        flash("Manual payment record saved. CineSphere did not transfer funds.", "success")
    return redirect(url_for("admin.payroll_management"))

@admin_bp.route("/admin/users")
@role_required("admin")
def users_list():
    db = get_db()
    role_filter = request.args.get("role")
    query = {}
    if role_filter:
        query["role"] = role_filter
    users = list(db.users.find(query).sort("created_at", -1))
    return render_template("admin/users.html", users=users, selected_role=role_filter)

@admin_bp.route("/admin/users/create", methods=["POST"])
@role_required("admin")
def user_create():
    db = get_db()
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "").strip()
    role = request.form.get("role", "customer").strip()
    if role not in {"customer", "admin"}:
        flash("Create staff and worker accounts from their dedicated management pages.", "danger")
        return redirect(url_for("admin.users_list"))
    if not name or not email or len(password) < 8:
        flash("Name, email, and a password of at least 8 characters are required.", "danger")
        return redirect(url_for("admin.users_list"))
    if db.users.find_one({"email": email}):
        flash("A user with this email already exists.", "danger")
        return redirect(url_for("admin.users_list"))
    now = datetime.now(UTC).replace(tzinfo=None)
    result = db.users.insert_one({
        "name": name, "email": email, "phone": request.form.get("phone", "").strip(),
        "password_hash": generate_password_hash(password), "role": role,
        "status": "active", "created_at": now, "updated_at": now,
    })
    if role == "customer":
        db.customers.insert_one({
            "user_id": result.inserted_id, "name": name, "email": email,
            "phone": request.form.get("phone", "").strip(), "tier": "Free",
            "favorite_movies": [], "total_bookings": 0, "total_spent": 0,
            "loyalty_points": 0, "status": "active", "created_at": now,
        })
    log_audit("ADMIN_CREATE_USER", "user", result.inserted_id, f"Admin created {role} {email}")
    flash(f"{role.title()} account created successfully.", "success")
    return redirect(url_for("admin.users_list", role=role))

@admin_bp.route("/admin/users/<user_id>/edit", methods=["POST"])
@role_required("admin")
def user_edit(user_id):
    db = get_db()
    try:
        object_id = ObjectId(user_id)
    except Exception:
        flash("Invalid user identifier.", "danger")
        return redirect(url_for("admin.users_list"))
    user = db.users.find_one({"_id": object_id})
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("admin.users_list"))
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    phone = request.form.get("phone", user.get("phone", "")).strip()
    status = request.form.get("status", "active")
    if not name or not email or status not in {"active", "inactive"}:
        flash("Valid name, email, and status are required.", "danger")
        return redirect(url_for("admin.users_list"))
    if str(object_id) == session.get("user_id") and status != "active":
        flash("You cannot deactivate your own administrator account.", "danger")
        return redirect(url_for("admin.users_list"))
    if user.get("role") == "admin" and user.get("status") == "active" and status != "active" \
            and db.users.count_documents({"role": "admin", "status": "active"}) <= 1:
        flash("The final active administrator cannot be deactivated.", "danger")
        return redirect(url_for("admin.users_list"))
    if db.users.find_one({"email": email, "_id": {"$ne": object_id}}):
        flash("Another user already uses this email address.", "danger")
        return redirect(url_for("admin.users_list"))
    updates = {"name": name, "email": email, "phone": phone, "status": status,
               "updated_at": datetime.now(UTC).replace(tzinfo=None)}
    password = request.form.get("password", "").strip()
    if password:
        if len(password) < 8:
            flash("A new password must contain at least 8 characters.", "danger")
            return redirect(url_for("admin.users_list"))
        updates["password_hash"] = generate_password_hash(password)
    db.users.update_one({"_id": object_id}, {"$set": updates})
    profile_collection = {"customer": db.customers, "staff": db.staff}.get(user.get("role"))
    if profile_collection is not None:
        profile_collection.update_one({"user_id": object_id}, {"$set": {
            "name": name, "email": email, "phone": phone, "status": status, "updated_at": updates["updated_at"]
        }})
    if status == "inactive":
        db.user_sessions.update_many({"user_id": object_id, "status": "active"}, {"$set": {
            "status": "revoked", "logout_at": updates["updated_at"]
        }})
    log_audit("ADMIN_UPDATE_USER", "user", object_id, f"Admin updated user {email}")
    flash("User updated successfully.", "success")
    return redirect(url_for("admin.users_list", role=user.get("role")))

@admin_bp.route("/admin/users/<user_id>/delete", methods=["POST"])
@role_required("admin")
def user_delete(user_id):
    db = get_db()
    try:
        object_id = ObjectId(user_id)
    except Exception:
        flash("Invalid user identifier.", "danger")
        return redirect(url_for("admin.users_list"))
    user = db.users.find_one({"_id": object_id})
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("admin.users_list"))
    if str(object_id) == session.get("user_id"):
        flash("You cannot delete your own administrator account.", "danger")
        return redirect(url_for("admin.users_list"))
    if user.get("role") == "admin" and db.users.count_documents({"role": "admin", "status": "active"}) <= 1:
        flash("The final active administrator cannot be deleted.", "danger")
        return redirect(url_for("admin.users_list"))
    if user.get("role") in {"staff", "worker"}:
        flash("Delete personnel from the dedicated staff or worker management page.", "danger")
        return redirect(url_for("admin.users_list", role=user.get("role")))
    if db.bookings.count_documents({"customer_id": object_id}):
        flash("This customer has booking history. Deactivate the account instead of deleting it.", "danger")
        return redirect(url_for("admin.users_list", role=user.get("role")))
    db.customers.delete_many({"user_id": object_id})
    db.notifications.delete_many({"user_id": object_id})
    db.user_sessions.delete_many({"user_id": object_id})
    db.users.delete_one({"_id": object_id})
    log_audit("ADMIN_DELETE_USER", "user", object_id, f"Admin deleted user {user.get('email', '')}")
    flash("User deleted successfully.", "success")
    return redirect(url_for("admin.users_list", role=user.get("role")))

@admin_bp.route("/admin/sessions")
@role_required("admin")
def session_history():
    db = get_db()
    status_filter = request.args.get("status", "").strip()
    role_filter = request.args.get("role", "").strip()
    query = {}
    if status_filter in {"active", "logged_out"}:
        query["status"] = status_filter
    if role_filter in {"admin", "staff", "worker", "customer"}:
        query["role"] = role_filter
    sessions = list(db.user_sessions.find(query).sort("login_at", -1).limit(250))
    return render_template("admin/sessions.html", sessions=sessions,
                           selected_status=status_filter, selected_role=role_filter)

import csv
import io
from flask import Response

@admin_bp.route("/admin/export/report")
@role_required("admin")
def export_report():
    db = get_db()
    users = db.users.find()
    
    # Generate CSV in memory
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(["User ID", "Name", "Email", "Role", "Tier", "Status", "Joined At"])
    
    for u in users:
        cw.writerow([
            str(u.get("_id")),
            u.get("name", "Unknown"),
            u.get("email", ""),
            u.get("role", "customer"),
            u.get("tier", "Free"),
            u.get("status", "active"),
            u.get("created_at", "")
        ])
        
    output = si.getvalue()
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=cinesphere_users_report.csv"}
    )

@admin_bp.route("/admin/operations")
@role_required("admin")
def operations():
    db = get_db()
    workers = list_workers()
    attendance = list(db.attendance.find().sort("date", -1).limit(100))
    tasks = list(db.tasks.find().sort("created_at", -1).limit(100))
    bookings = list(db.bookings.find().sort("created_at", -1).limit(100))
    customers = list(db.users.find({"role": "customer"}).sort("name", 1))
    rewards = list(db.reward_transactions.find().sort("created_at", -1).limit(100))
    theaters = list(db.theaters.find({"status": "active"}).sort("name", 1))
    return render_template("admin/operations.html", workers=workers, attendance=attendance,
        tasks=tasks, bookings=bookings, customers=customers, rewards=rewards, theaters=theaters)

@admin_bp.route("/admin/operations/workers/create", methods=["POST"])
@role_required("admin")
def operations_worker_create():
    db = get_db()
    name = request.form.get("name", "").strip(); email = request.form.get("email", "").strip().lower()
    try: theater_id = ObjectId(request.form.get("theater_id"))
    except Exception: theater_id = None
    theater = db.theaters.find_one({"_id": theater_id}) if theater_id else None
    if not name or not email or not theater or db.users.find_one({"email": email}):
        flash("Valid worker details, unique email and theater are required.", "danger")
        return redirect(url_for("admin.operations"))
    now = datetime.now(UTC).replace(tzinfo=None); salary = request.form.get("salary", type=float) or 22000
    db.users.insert_one({"name": name, "email": email, "phone": request.form.get("phone", ""),
        "password_hash": generate_password_hash(request.form.get("password", "Worker@1234")), "role": "worker",
        "status": "active", "created_at": now, "worker_profile": {"employee_code": f"WRK-ADM-{int(now.timestamp())}",
        "theater_id": theater_id, "theater_name": theater["name"], "department": request.form.get("department", "Operations"),
        "designation": request.form.get("designation", "Operations Worker"), "monthly_salary": salary,
        "hourly_rate": round(salary / 160, 2), "shift_timings": request.form.get("shift_timings", "09:00 AM - 05:30 PM"),
        "current_task": "Idle / On Call", "bonus_coins": 0}})
    flash("Worker created.", "success"); return redirect(url_for("admin.operations"))

@admin_bp.route("/admin/operations/workers/<worker_id>", methods=["POST"])
@role_required("admin")
def operations_worker_update(worker_id):
    db = get_db()
    try: oid = ObjectId(worker_id)
    except Exception: abort(404)
    salary = request.form.get("salary", type=float) or 22000
    result = db.users.update_one({"_id": oid, "role": "worker"}, {"$set": {"name": request.form.get("name", "").strip(),
        "status": request.form.get("status", "active"), "worker_profile.department": request.form.get("department", ""),
        "worker_profile.monthly_salary": salary, "worker_profile.hourly_rate": round(salary / 160, 2),
        "worker_profile.bonus_coins": request.form.get("bonus_coins", type=int) or 0}})
    flash("Worker updated." if result.matched_count else "Worker not found.", "success" if result.matched_count else "warning")
    return redirect(url_for("admin.operations"))

@admin_bp.route("/admin/operations/workers/<worker_id>/delete", methods=["POST"])
@role_required("admin")
def operations_worker_delete(worker_id):
    db = get_db()
    try: oid = ObjectId(worker_id)
    except Exception: abort(404)
    now = datetime.now(UTC).replace(tzinfo=None)
    result = db.users.update_one({"_id": oid, "role": "worker"}, {"$set": {"status": "inactive", "deactivated_at": now}})
    if result.modified_count:
        db.user_sessions.update_many({"user_id": oid, "status": "active"}, {"$set": {"status": "revoked", "logout_at": now}})
        log_audit("ADMIN_DEACTIVATE_WORKER", "worker", oid, request.form.get("reason", "Administrative deactivation"))
        flash("Worker deactivated. Attendance, task, payroll, and reward history was preserved.", "success")
    else: flash("Worker not found or already inactive.", "warning")
    return redirect(url_for("admin.operations"))

@admin_bp.route("/admin/operations/attendance/create", methods=["POST"])
@role_required("admin")
def operations_attendance_create():
    db = get_db(); worker = find_worker(request.form.get("worker_id"))
    if not worker: abort(404)
    db.attendance.update_one({"worker_id": worker["_id"], "date": request.form.get("date")}, {"$set": {
        "worker_name": worker["name"], "theater_id": worker["theater_id"], "check_in": request.form.get("check_in"),
        "check_out": request.form.get("check_out") or None, "status": request.form.get("status", "Present")}}, upsert=True)
    flash("Attendance saved.", "success"); return redirect(url_for("admin.operations"))

@admin_bp.route("/admin/operations/attendance/<record_id>/delete", methods=["POST"])
@role_required("admin")
def operations_attendance_delete(record_id):
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("Attendance corrections require a reason; the original record is preserved.", "danger")
    else:
        try: attendance_oid = ObjectId(record_id)
        except Exception: abort(404)
        result = get_db().attendance.update_one({"_id": attendance_oid}, {"$set": {"status": "Voided by correction",
            "correction_reason": reason, "corrected_by": ObjectId(session["user_id"]),
            "corrected_at": datetime.now(UTC).replace(tzinfo=None)}})
        if not result.matched_count: abort(404)
        log_audit("ADMIN_CORRECT_ATTENDANCE", "attendance", record_id, reason)
        flash("Attendance record voided with correction history.", "success")
    return redirect(url_for("admin.operations"))

@admin_bp.route("/admin/operations/tasks/create", methods=["POST"])
@role_required("admin")
def operations_task_create():
    db = get_db(); worker = find_worker(request.form.get("worker_id"))
    if not worker: abort(404)
    db.tasks.insert_one({"theater_id": worker["theater_id"], "assigned_worker_id": worker["_id"],
        "worker_name": worker["name"], "title": request.form.get("title", "").strip(),
        "description": request.form.get("description", "").strip(), "priority": request.form.get("priority", "Medium"),
        "status": "Pending", "created_by": "Administrator", "notes": "", "created_at": datetime.now(UTC).replace(tzinfo=None)})
    flash("Task created.", "success"); return redirect(url_for("admin.operations"))

@admin_bp.route("/admin/operations/tasks/<task_id>", methods=["POST"])
@role_required("admin")
def operations_task_update(task_id):
    try: task_oid = ObjectId(task_id)
    except Exception: abort(404)
    result = get_db().tasks.update_one({"_id": task_oid}, {"$set": {"title": request.form.get("title", "").strip(),
        "status": request.form.get("status", "Pending"), "priority": request.form.get("priority", "Medium")}})
    if not result.matched_count: abort(404)
    flash("Task updated.", "success"); return redirect(url_for("admin.operations"))

@admin_bp.route("/admin/operations/tasks/<task_id>/delete", methods=["POST"])
@role_required("admin")
def operations_task_delete(task_id):
    reason = request.form.get("reason", "").strip() or "Administrative cancellation"
    try: task_oid = ObjectId(task_id)
    except Exception: abort(404)
    result = get_db().tasks.update_one({"_id": task_oid, "status": {"$ne": "Completed"}}, {"$set": {
        "status": "Cancelled", "cancelled_reason": reason, "cancelled_at": datetime.now(UTC).replace(tzinfo=None)}})
    if not result.matched_count: abort(404)
    log_audit("ADMIN_CANCEL_TASK", "task", task_id, reason)
    flash("Task cancelled; its history was preserved.", "success"); return redirect(url_for("admin.operations"))

@admin_bp.route("/admin/operations/bookings/<booking_id>", methods=["POST"])
@role_required("admin")
def operations_booking_update(booking_id):
    flash("Booking financial state cannot be edited directly. Use the audited cancellation/refund workflow.", "danger")
    return redirect(url_for("admin.operations"))

@admin_bp.route("/admin/operations/bookings/<booking_id>/delete", methods=["POST"])
@role_required("admin")
def operations_booking_delete(booking_id):
    flash("Bookings are historical financial records and cannot be deleted from the dashboard.", "danger")
    return redirect(url_for("admin.operations"))

@admin_bp.route("/admin/operations/rewards/create", methods=["POST"])
@role_required("admin")
def operations_reward_create():
    db = get_db()
    try: user_id = ObjectId(request.form.get("user_id", ""))
    except Exception: abort(404)
    amount = request.form.get("amount", type=int) or 0
    user = db.users.find_one({"_id": user_id})
    if not user: abort(404)
    if user.get("role") == "customer": db.customers.update_one({"user_id": user_id}, {"$inc": {"loyalty_points": amount}})
    elif user.get("role") == "worker": db.users.update_one({"_id": user_id}, {"$inc": {"worker_profile.bonus_coins": amount}})
    db.reward_transactions.insert_one({"user_id": user_id, "name": user.get("name"), "role": user.get("role"),
        "amount": amount, "reason": request.form.get("reason", "Admin adjustment"), "created_at": datetime.now(UTC).replace(tzinfo=None)})
    flash("Reward transaction created.", "success"); return redirect(url_for("admin.operations"))

@admin_bp.route("/admin/operations/rewards/<reward_id>/delete", methods=["POST"])
@role_required("admin")
def operations_reward_delete(reward_id):
    flash("Reward ledger history cannot be deleted. Record a reasoned adjustment instead.", "danger")
    return redirect(url_for("admin.operations"))
