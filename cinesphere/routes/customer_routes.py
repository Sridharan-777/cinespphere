from flask import Blueprint, render_template, request, redirect, url_for, flash, session, abort
from datetime import UTC, datetime
from bson import ObjectId
from werkzeug.security import generate_password_hash
from cinesphere.models.database import get_db
from cinesphere.utils.decorators import role_required, login_required
from cinesphere.utils.audit import log_audit, create_notification
from cinesphere.services.membership_service import MembershipService
from cinesphere.services.reward_service import RewardService

customer_bp = Blueprint("customer", __name__)

@customer_bp.route("/customer/live-shows")
@role_required("customer")
def live_shows():
    db = get_db()
    today = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d")
    shows = list(db.showtimes.find({"status": "active", "show_date": {"$gte": today}}).sort([("show_date", 1), ("start_time", 1)]))
    for show in shows:
        show["movie"] = db.movies.find_one({"_id": show["movie_id"]})
        show["theater"] = db.theaters.find_one({"_id": show["theater_id"]})
        screen = db.screens.find_one({"_id": show["screen_id"]})
        show["available_seats"] = max(0, (screen or {}).get("capacity", 48) - len(show.get("booked_seats", [])))
    return render_template("customer/live_shows.html", shows=shows)

@customer_bp.route("/customer/plans")
@role_required("customer")
def plans():
    db = get_db()
    uid = ObjectId(session["user_id"])
    customer = db.customers.find_one({"user_id": uid}) or {
        "user_id": uid, "name": user.get("name", ""), "phone": user.get("phone", ""),
        "address": "", "city": "", "notification_preferences": {}
    }
    customer.setdefault("notification_preferences", {})
    current_tier, membership = MembershipService.active_membership(customer)
    return render_template("customer/plans.html", customer=customer, current_tier=current_tier,
                           membership=membership, plans=MembershipService.plans())

@customer_bp.route("/customer/plans/change", methods=["POST"])
@role_required("customer")
def change_plan():
    new_tier = request.form.get("tier", "FREE").upper()
    if new_tier not in {"FREE", "PLUS", "PRO"}:
        flash("Invalid membership tier.", "danger")
        return redirect(url_for("customer.plans"))

    db = get_db()
    uid = ObjectId(session["user_id"])
    if new_tier != "FREE":
        plan = MembershipService.plans()[new_tier]
        if plan.get("status") != "active" or plan.get("price") is None:
            flash("This paid plan is not commercially configured yet. Your current plan was not changed.", "warning")
        else:
            flash("Paid membership activation requires a verified payment. No plan change was made.", "warning")
        return redirect(url_for("customer.plans"))
    now = datetime.now(UTC).replace(tzinfo=None)
    db.customers.update_one({"user_id": uid}, {"$set": {"tier": "Free",
        "membership": {"tier": "FREE", "status": "active", "started_at": now}, "plan_updated_at": now}})
    
    log_audit("MEMBERSHIP_TIER_CHANGED", "customer", uid, f"Customer changed membership tier to {new_tier}")
    create_notification(session["user_id"], "Membership updated", "Your CineSphere account is now on the Free plan.", "info")
    flash("Your membership is now on the Free plan.", "success")
    return redirect(url_for("customer.dashboard"))

@customer_bp.route("/customer/dashboard")
@role_required("customer")
def dashboard():
    db = get_db()
    uid = ObjectId(session["user_id"])
    customer = db.customers.find_one({"user_id": uid})
    
    # Bookings
    bookings = list(db.bookings.find({"customer_id": uid}).sort("created_at", -1))
    today = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d")
    upcoming_booking = next((b for b in bookings if b.get("status") == "Confirmed" and b.get("show_date", "") >= today), None)
    
    # Recommendations: Based on genres customer has watched or top rated movies
    booked_movie_ids = [b["movie_id"] for b in bookings]
    recommended_movies = list(db.movies.find({"_id": {"$nin": booked_movie_ids}}).sort("rating", -1).limit(4))
    
    # Favorites
    fav_ids = customer.get("favorite_movies", []) if customer else []
    fav_movies = list(db.movies.find({"_id": {"$in": fav_ids}})) if fav_ids else []

    # Reviews written
    reviews = list(db.reviews.find({"customer_id": uid}).sort("created_at", -1).limit(5))

    stats = {
        "total_bookings": len(bookings),
        "total_spent": sum(b.get("pricing", {}).get("grand_total", 0) for b in bookings
                           if b.get("status") in {"Confirmed", "Completed"} and b.get("funding_source", "cash") == "cash"),
        "total_reviews": len(reviews),
        "loyalty_points": customer.get("loyalty_points", 150) if customer else 150,
        "tier": customer.get("tier", "Free") if customer else "Free"
    }

    return render_template("customer/dashboard.html",
                           customer=customer,
                           upcoming_booking=upcoming_booking,
                           recommended_movies=recommended_movies,
                           fav_movies=fav_movies,
                           recent_reviews=reviews,
                           stats=stats)

@customer_bp.route("/customer/bookings")
@role_required("customer")
def my_bookings():
    db = get_db()
    uid = ObjectId(session["user_id"])
    bookings = list(db.bookings.find({"customer_id": uid}).sort("created_at", -1))
    return render_template("customer/bookings.html", bookings=bookings)

@customer_bp.route("/customer/bookings/<booking_code>/cancel", methods=["POST"])
@role_required("customer")
def cancel_booking(booking_code):
    db = get_db()
    uid = ObjectId(session["user_id"])
    booking = db.bookings.find_one({"booking_code": booking_code, "customer_id": uid})
    
    if not booking:
        flash("Booking not found.", "danger")
        return redirect(url_for("customer.my_bookings"))
        
    if booking.get("status") == "Cancelled":
        flash("Booking is already cancelled.", "info")
        return redirect(url_for("customer.my_bookings"))

    try:
        show_at = datetime.strptime(f"{booking['show_date']} {booking['start_time']}", "%Y-%m-%d %I:%M %p")
    except (KeyError, TypeError, ValueError):
        flash("This booking has incomplete schedule data. Contact support for a reviewed cancellation.", "warning")
        return redirect(url_for("customer.my_bookings"))
    now = datetime.now(UTC).replace(tzinfo=None)
    if show_at <= now:
        flash("This show has started or finished and cannot be cancelled online.", "warning")
        return redirect(url_for("customer.my_bookings"))

    # Release seats in showtime
    db.showtimes.update_one(
        {"_id": booking["showtime_id"]},
        {"$pull": {"booked_seats": {"$in": booking.get("seats", [])}}}
    )

    # Update Booking status
    cancellation = {"status": "Cancelled", "cancelled_at": now, "cancelled_by": uid,
                    "cancellation_reason": request.form.get("reason", "Customer request").strip()[:250]}
    if booking.get("funding_source") == "cash" and booking.get("payment_id"):
        cancellation["refund_status"] = "Pending provider review"
    db.bookings.update_one(
        {"_id": booking["_id"]},
        {"$set": cancellation}
    )

    db.customers.update_one(
        {"user_id": uid},
        {"$inc": {
            "total_bookings": -1,
            "total_spent": 0
        }}
    )

    refund_message = "No cash payment was attached to this booking."
    if booking.get("payment_id"):
        db.payments.update_one(
            {"_id": booking["payment_id"]},
            {"$set": {"status": "Refund Pending", "refund_requested_at": now}}
        )
        db.refunds.update_one({"booking_id": booking["_id"]}, {"$setOnInsert": {
            "booking_id": booking["_id"], "payment_id": booking["payment_id"],
            "customer_id": uid, "amount": booking.get("pricing", {}).get("grand_total", 0),
            "status": "Pending", "reason": cancellation["cancellation_reason"], "created_at": now}}, upsert=True)
        refund_message = "A refund request is pending provider confirmation."
    elif booking.get("reward_credit_id") and RewardService.policy().get("restore_credit_on_permitted_cancellation"):
        restored = db.reward_credits.update_one({"_id": booking["reward_credit_id"], "status": "consumed",
            "booking_id": booking["_id"], "expires_at": {"$gt": now}}, {"$set": {"status": "available",
            "restored_at": now, "restored_reason": "Permitted booking cancellation"},
            "$unset": {"booking_id": "", "consumed_at": ""}})
        refund_message = "The admission credit was restored." if restored.modified_count else "The expired admission credit was not restored."

    log_audit("BOOKING_CANCELLED", "booking", booking["_id"], f"Customer cancelled booking {booking_code}")
    create_notification(session["user_id"], "Booking cancelled", f"Booking {booking_code} was cancelled. {refund_message}", "warning")
    flash(f"Booking {booking_code} was cancelled. {refund_message}", "success")
    return redirect(url_for("customer.my_bookings"))

@customer_bp.route("/customer/favorites")
@role_required("customer")
def favorites():
    db = get_db()
    uid = ObjectId(session["user_id"])
    cust = db.customers.find_one({"user_id": uid})
    fav_ids = cust.get("favorite_movies", []) if cust else []
    movies = list(db.movies.find({"_id": {"$in": fav_ids}})) if fav_ids else []
    return render_template("customer/favorites.html", movies=movies)

@customer_bp.route("/customer/favorites/toggle/<movie_id>", methods=["POST"])
@role_required("customer")
def toggle_favorite(movie_id):
    db = get_db()
    uid = ObjectId(session["user_id"])
    m_id = ObjectId(movie_id)
    cust = db.customers.find_one({"user_id": uid})
    
    fav_ids = cust.get("favorite_movies", []) if cust else []
    if m_id in fav_ids:
        db.customers.update_one({"user_id": uid}, {"$pull": {"favorite_movies": m_id}})
        flash("Movie removed from favorites.", "info")
    else:
        db.customers.update_one({"user_id": uid}, {"$addToSet": {"favorite_movies": m_id}})
        flash("Movie added to favorites!", "success")
    return redirect(request.referrer or url_for("customer.favorites"))

@customer_bp.route("/customer/reviews", methods=["GET", "POST"])
@role_required("customer")
def my_reviews():
    db = get_db()
    uid = ObjectId(session["user_id"])
    
    if request.method == "POST":
        movie_id = request.form.get("movie_id")
        try:
            rating = int(request.form.get("rating", 5))
            movie_object_id = ObjectId(movie_id)
        except (TypeError, ValueError):
            flash("Invalid review data.", "danger")
            return redirect(url_for("customer.my_reviews"))
        if rating not in range(1, 6) or not db.movies.find_one({"_id": movie_object_id}):
            flash("Choose a valid movie and a rating from 1 to 5.", "danger")
            return redirect(url_for("customer.my_reviews"))
        title = request.form.get("title", "").strip()
        comment = request.form.get("comment", "").strip()

        # Check if customer has verified booking for this movie
        has_booking = db.bookings.find_one({"customer_id": uid, "movie_id": movie_object_id, "status": {"$in": ["Confirmed", "Completed"]}})
        
        # Check if already reviewed
        existing = db.reviews.find_one({"customer_id": uid, "movie_id": movie_object_id})
        if existing:
            db.reviews.update_one(
                {"_id": existing["_id"]},
                {"$set": {"rating": rating, "title": title, "comment": comment, "updated_at": datetime.now(UTC).replace(tzinfo=None)}}
            )
            flash("Your review has been updated.", "success")
        else:
            db.reviews.insert_one({
                "movie_id": movie_object_id,
                "customer_id": uid,
                "customer_name": session.get("name"),
                "is_verified": bool(has_booking),
                "rating": rating,
                "title": title,
                "comment": comment,
                "status": "Published",
                "created_at": datetime.now(UTC).replace(tzinfo=None),
                "updated_at": datetime.now(UTC).replace(tzinfo=None)
            })
            flash("Your review has been submitted!", "success")
        return redirect(url_for("customer.my_reviews"))

    reviews = list(db.reviews.find({"customer_id": uid}).sort("created_at", -1))
    for r in reviews:
        movie = db.movies.find_one({"_id": r["movie_id"]})
        r["movie"] = movie

    return render_template("customer/reviews.html", reviews=reviews)

@customer_bp.route("/customer/profile", methods=["GET", "POST"])
@role_required("customer")
def profile():
    db = get_db()
    uid = ObjectId(session["user_id"])
    user = db.users.find_one({"_id": uid})
    customer = db.customers.find_one({"user_id": uid}) or {
        "user_id": uid, "name": user.get("name", ""), "phone": user.get("phone", ""),
        "address": "", "city": "", "notification_preferences": {}
    }
    customer.setdefault("notification_preferences", {})

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()
        city = request.form.get("city", "").strip()
        notification_preferences = {
            "email_show_alerts": bool(request.form.get("email_show_alerts")),
            "email_booking_updates": bool(request.form.get("email_booking_updates")),
            "in_app_updates": bool(request.form.get("in_app_updates")),
        }

        db.users.update_one({"_id": uid}, {"$set": {"name": name, "phone": phone, "updated_at": datetime.now(UTC).replace(tzinfo=None)}})
        db.customers.update_one({"user_id": uid}, {"$set": {
            "name": name,
            "phone": phone,
            "address": address,
            "city": city,
            "notification_preferences": notification_preferences,
            "updated_at": datetime.now(UTC).replace(tzinfo=None)
        }})

        # Check password change
        new_pwd = request.form.get("new_password", "").strip()
        if new_pwd:
            if len(new_pwd) >= 10:
                db.users.update_one({"_id": uid}, {"$set": {"password_hash": generate_password_hash(new_pwd)}})
                flash("Profile and password updated successfully!", "success")
            else:
                flash("Password must be at least 10 characters.", "warning")
        else:
            flash("Profile updated successfully!", "success")

        session["name"] = name
        return redirect(url_for("customer.profile"))

    return render_template("customer/profile.html", user=user, customer=customer)

@customer_bp.route("/customer/notifications")
@role_required("customer")
def notifications():
    db = get_db()
    uid_str = session["user_id"]
    notifs = list(db.notifications.find({"user_id": uid_str}).sort("created_at", -1))
    db.notifications.update_many({"user_id": uid_str, "is_read": False}, {"$set": {"is_read": True}})
    return render_template("customer/notifications.html", notifications=notifs)

@customer_bp.route("/customer/rewards")
@role_required("customer")
def rewards():
    db = get_db()
    uid = ObjectId(session["user_id"])
    customer = db.customers.find_one({"user_id": uid})
    points = customer.get("loyalty_points", 0) if customer else 0
    return render_template("customer/rewards.html", loyalty_points=points)

@customer_bp.route("/customer/rewards/redeem", methods=["POST"])
@role_required("customer")
def redeem_reward():
    reward_catalog = {
        "popcorn": {"name": "Free Popcorn", "cost": 500},
        "upgrade": {"name": "Ticket Upgrade", "cost": 1000},
        "lounge": {"name": "VIP Lounge", "cost": 2000}
    }
    reward = reward_catalog.get(request.form.get("reward_id", ""))
    # Backwards-compatible lookup for the current template; cost is never trusted from the browser.
    if not reward:
        requested_name = request.form.get("reward_name", "")
        reward = next((item for item in reward_catalog.values() if item["name"] == requested_name), None)
    if not reward:
        flash("Invalid reward selection.", "danger")
        return redirect(url_for("customer.rewards"))
    reward_cost = reward["cost"]
    reward_name = reward["name"]
    db = get_db()
    uid = ObjectId(session["user_id"])
    customer = db.customers.find_one({"user_id": uid})
    points = customer.get("loyalty_points", 0) if customer else 0

    if points >= reward_cost and reward_cost > 0:
        db.customers.update_one({"user_id": uid}, {"$inc": {"loyalty_points": -reward_cost}})
        flash(f"Successfully redeemed {reward_name}!", "success")
        log_audit("REWARD_REDEEMED", "customer", uid, f"Customer redeemed {reward_name} for {reward_cost} points")
        create_notification(session["user_id"], "Reward Claimed!", f"Enjoy your {reward_name}! Your loyalty points have been updated.", "success")
    else:
        flash("Not enough loyalty points.", "danger")
        
    return redirect(url_for("customer.rewards"))


@customer_bp.route("/customer/support", methods=["GET", "POST"])
@role_required("customer")
def support():
    db = get_db()
    uid = ObjectId(session["user_id"])
    if request.method == "POST":
        booking_code = request.form.get("booking_code", "").strip().upper()
        booking = db.bookings.find_one({"booking_code": booking_code, "customer_id": uid}) if booking_code else None
        if booking_code and not booking:
            flash("That booking does not belong to your account.", "danger")
            return redirect(url_for("customer.support"))
        subject = request.form.get("subject", "").strip()
        message = request.form.get("message", "").strip()
        if not subject or not message:
            flash("Subject and message are required.", "danger")
        else:
            now = datetime.now(UTC).replace(tzinfo=None)
            result = db.support_tickets.insert_one({"customer_id": uid, "booking_id": booking.get("_id") if booking else None,
                "theater_id": booking.get("theater_id") if booking else None, "subject": subject[:150],
                "priority": "Normal", "status": "Open", "messages": [{"author_id": uid, "author_role": "customer",
                "body": message[:3000], "created_at": now}], "created_at": now, "updated_at": now})
            log_audit("SUPPORT_TICKET_CREATED", "support_ticket", result.inserted_id, subject[:150])
            flash("Support ticket created.", "success")
        return redirect(url_for("customer.support"))
    tickets = list(db.support_tickets.find({"customer_id": uid}).sort("updated_at", -1))
    bookings = list(db.bookings.find({"customer_id": uid}).sort("created_at", -1).limit(50))
    return render_template("customer/support.html", tickets=tickets, bookings=bookings)
