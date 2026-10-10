from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from bson import ObjectId
from cinesphere.models.user_model import UserModel
from cinesphere.models.database import get_db
from cinesphere.utils.audit import log_audit
from cinesphere.services.user_session_service import start_user_session, close_user_session
from cinesphere.oauth import oauth
auth_bp = Blueprint("auth", __name__)

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        # Redirect based on role
        role = session.get("role")
        if role == "admin":
            return redirect(url_for("admin.dashboard"))
        elif role == "staff":
            return redirect(url_for("staff.dashboard"))
        elif role == "worker":
            return redirect(url_for("worker.dashboard"))
        return redirect(url_for("customer.dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        user, err = UserModel.authenticate(email, password)
        if err:
            flash(err, "danger")
            return render_template("auth/login.html", email=email)

        # Store session
        session.clear()
        session["user_id"] = str(user["_id"])
        session["name"] = user["name"]
        session["email"] = user["email"]
        session["role"] = user["role"]

        # Fetch theater_id if staff or worker
        db = get_db()
        if user["role"] == "staff":
            staff_doc = db.staff.find_one({"user_id": user["_id"]})
            if staff_doc:
                session["theater_id"] = str(staff_doc["theater_id"])
                session["theater_name"] = staff_doc.get("theater_name", "")
        elif user["role"] == "worker":
            worker_doc = db.workers.find_one({"user_id": user["_id"]})
            if worker_doc:
                session["theater_id"] = str(worker_doc["theater_id"])
                session["theater_name"] = worker_doc.get("theater_name", "")

        from datetime import UTC, datetime
        db.users.update_one({"_id": user["_id"]}, {"$set": {"last_login": datetime.now(UTC).replace(tzinfo=None)}})
        start_user_session(user, "password")

        log_audit("LOGIN", "user", user["_id"], f"User {user['email']} logged in as {user['role']}")
        flash(f"Welcome back, {user['name']}!", "success")

        next_url = request.args.get("next")
        if next_url and next_url.startswith("/"):
            return redirect(next_url)

        if user["role"] == "admin":
            return redirect(url_for("admin.dashboard"))
        elif user["role"] == "staff":
            return redirect(url_for("staff.dashboard"))
        elif user["role"] == "worker":
            return redirect(url_for("worker.dashboard"))
        return redirect(url_for("public.index"))

    return render_template("auth/login.html")

@auth_bp.route("/login/google")
def google_login():
    redirect_uri = url_for("auth.google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)

@auth_bp.route("/login/google/callback")
def google_callback():
    token = oauth.google.authorize_access_token()
    user_info = oauth.google.parse_id_token(token, None)
    
    email = user_info.get("email")
    name = user_info.get("name")
    google_id = user_info.get("sub")
    
    db = get_db()
    user = db.users.find_one({"email": email})
    
    if not user:
        # Create a new user if they don't exist
        user_doc, err = UserModel.create_user(name, email, password=None, role="customer", google_id=google_id)
        if err:
            flash(err, "danger")
            return redirect(url_for("auth.login"))
        user = user_doc
        
        # Create customer profile document
        db.customers.insert_one({
            "user_id": user["_id"],
            "name": name,
            "email": email,
            "phone": "",
            "favorite_movies": [],
            "total_bookings": 0,
            "total_spent": 0,
            "status": "active"
        })
    elif not user.get("google_id"):
        # Link existing account to Google
        db.users.update_one({"_id": user["_id"]}, {"$set": {"google_id": google_id}})
        
    if user.get("status") != "active":
        flash("Your account is deactivated.", "danger")
        return redirect(url_for("auth.login"))
        
    # Store session
    session.clear()
    session["user_id"] = str(user["_id"])
    session["name"] = user["name"]
    session["email"] = user["email"]
    session["role"] = user["role"]
    
    from datetime import UTC, datetime
    db.users.update_one({"_id": user["_id"]}, {"$set": {"last_login": datetime.now(UTC).replace(tzinfo=None)}})
    start_user_session(user, "google")
    
    log_audit("LOGIN", "user", user["_id"], f"User {email} logged in via Google")
    flash(f"Welcome, {name}!", "success")
    
    return redirect(url_for("customer.dashboard"))

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if session.get("user_id"):
        return redirect(url_for("public.index"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not name or not email or not password:
            flash("Please fill in all required fields.", "warning")
            return render_template("auth/register.html")

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template("auth/register.html", name=name, email=email, phone=phone)

        if len(password) < 6:
            flash("Password must be at least 6 characters long.", "warning")
            return render_template("auth/register.html", name=name, email=email, phone=phone)

        user, err = UserModel.create_user(name, email, password, role="customer", phone=phone)
        if err:
            flash(err, "danger")
            return render_template("auth/register.html", name=name, email=email, phone=phone)

        # Create customer profile document
        db = get_db()
        db.customers.insert_one({
            "user_id": user["_id"],
            "name": name,
            "email": email,
            "phone": phone,
            "favorite_movies": [],
            "total_bookings": 0,
            "total_spent": 0,
            "status": "active"
        })

        log_audit("REGISTER", "customer", user["_id"], f"Customer {email} registered")
        flash("Registration successful! You can now log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/register.html")

@auth_bp.route("/logout")
def logout():
    uid = session.get("user_id")
    if uid:
        from datetime import UTC, datetime
        db = get_db()
        db.users.update_one({"_id": ObjectId(uid)}, {"$set": {"last_logout": datetime.now(UTC).replace(tzinfo=None)}})
        close_user_session()
        log_audit("LOGOUT", "user", uid, f"User {session.get('email')} logged out")
    session.clear()
    flash("You have been safely logged out.", "info")
    return redirect(url_for("public.index"))
