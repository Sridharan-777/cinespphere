from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from datetime import UTC, datetime
from bson import ObjectId
from werkzeug.security import generate_password_hash
from cinesphere.models.database import get_db
from cinesphere.models.worker_model import count_workers, list_workers
from cinesphere.utils.decorators import role_required
from cinesphere.services.report_service import ReportService
from cinesphere.services.tmdb_service import tmdb_service
from cinesphere.utils.audit import log_audit

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
    t_id = ObjectId(theater_id)
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
        theater_id = ObjectId(request.form.get("theater_id"))
        designation = request.form.get("designation", "Operations Supervisor").strip()
        password = request.form.get("password", "Staff@1234").strip()

        # Check existing staff count in this theater - STRICT MAX 5
        active_staff = db.staff.count_documents({"theater_id": theater_id, "status": "active"})
        if active_staff >= 5:
            flash("This theater already has the maximum capacity of 5 staff members.", "danger")
            return redirect(url_for("admin.staff_management"))

        if db.users.find_one({"email": email}):
            flash("A user with this email already exists.", "danger")
            return redirect(url_for("admin.staff_management"))

        theater = db.theaters.find_one({"_id": theater_id})
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
    staff_doc = db.staff.find_one({"_id": ObjectId(staff_id)})
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
            "status": request.form.get("status", "Now Showing").strip(),
            "poster_url": request.form.get("poster_url", "").strip() or url_for("static", filename="images/default_poster.svg"),
            "backdrop_url": request.form.get("backdrop_url", "").strip() or url_for("static", filename="images/default_backdrop.svg"),
            "cast": [], "writers": [], "vote_count": 0,
            "created_at": datetime.now(UTC).replace(tzinfo=None),
        }
        result = db.movies.insert_one(movie_doc)
        log_audit("ADMIN_CREATE_MOVIE", "movie", result.inserted_id, f"Admin created movie {title}")
        flash(f"Movie {title} created successfully.", "success")
        return redirect(url_for("admin.movies"))
    movies_list = list(db.movies.find().sort("created_at", -1))
    return render_template("admin/movies.html", movies=movies_list)

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
    updates = {
        "title": title, "language": request.form.get("language", "English").strip(),
        "runtime": max(0, request.form.get("runtime", type=int) or 0),
        "rating": min(10, max(0, request.form.get("rating", type=float) or 0)),
        "director": request.form.get("director", "").strip(),
        "status": request.form.get("status", "Now Showing").strip(),
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
    count_synced = 0
    try:
        popular = tmdb_service.get_popular_movies(page=1)
        now_playing = tmdb_service.get_now_playing_movies(page=1)
        all_raw = {m["id"]: m for m in (popular + now_playing)}.values()

        for raw_movie in list(all_raw)[:15]:
            tmdb_id = raw_movie.get("id")
            # Fetch full details
            full_detail = tmdb_service.get_movie_details(tmdb_id) or raw_movie
            formatted = tmdb_service.format_movie_data(full_detail)
            if not formatted:
                continue

            existing = db.movies.find_one({"tmdb_id": tmdb_id})
            if existing:
                db.movies.update_one({"tmdb_id": tmdb_id}, {"$set": formatted})
            else:
                formatted["created_at"] = datetime.now(UTC).replace(tzinfo=None)
                db.movies.insert_one(formatted)
            count_synced += 1

        log_audit("ADMIN_SYNC_TMDB", "movie", None, f"Synced {count_synced} real movies from TMDB")
        flash(f"Successfully synchronized {count_synced} real movies from TMDB API!", "success")
    except Exception as e:
        flash(f"Error synchronizing movies from TMDB: {e}", "danger")

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
    db.reviews.update_one({"_id": ObjectId(review_id)}, {"$set": {"status": action}})
    log_audit("ADMIN_MODERATE_REVIEW", "review", review_id, f"Review set to {action}")
    flash(f"Review marked as {action}.", "info")
    return redirect(url_for("admin.reviews_moderation"))

@admin_bp.route("/admin/audit-logs")
@role_required("admin")
def audit_logs():
    db = get_db()
    logs = list(db.audit_logs.find().sort("created_at", -1).limit(100))
    return render_template("admin/audit_logs.html", logs=logs)

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
    status = request.form.get("status", "active")
    if not name or not email or status not in {"active", "inactive"}:
        flash("Valid name, email, and status are required.", "danger")
        return redirect(url_for("admin.users_list"))
    if str(object_id) == session.get("user_id") and status != "active":
        flash("You cannot deactivate your own administrator account.", "danger")
        return redirect(url_for("admin.users_list"))
    if db.users.find_one({"email": email, "_id": {"$ne": object_id}}):
        flash("Another user already uses this email address.", "danger")
        return redirect(url_for("admin.users_list"))
    updates = {"name": name, "email": email, "status": status,
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
            "name": name, "email": email, "status": status, "updated_at": updates["updated_at"]
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
