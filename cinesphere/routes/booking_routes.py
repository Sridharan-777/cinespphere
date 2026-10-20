from flask import Blueprint, render_template, request, redirect, url_for, flash, session, abort, jsonify
from bson import ObjectId
from datetime import UTC, datetime
from pymongo.errors import DuplicateKeyError
from cinesphere.models.database import get_db
from cinesphere.models.worker_model import find_worker
from cinesphere.utils.decorators import login_required, role_required
from cinesphere.services.booking_service import BookingService
from cinesphere.services.razorpay_service import razorpay_service
from cinesphere.services.stripe_service import stripe_service
from cinesphere.services.reward_service import RewardService
from cinesphere.services.membership_service import MembershipService
from cinesphere.services.ticket_service import TicketService
from cinesphere.services.availability_service import enrich_available_showtime
from cinesphere.utils.audit import log_audit, create_notification

booking_bp = Blueprint("booking", __name__)


@booking_bp.route("/webhooks/razorpay", methods=["POST"])
def razorpay_webhook():
    payload = request.get_data(cache=True)
    signature = request.headers.get("X-Razorpay-Signature", "")
    if not razorpay_service.verify_webhook_signature(payload, signature):
        return jsonify({"ok": False}), 401
    event = request.get_json(silent=True) or {}
    event_id = request.headers.get("X-Razorpay-Event-Id") or event.get("id")
    if not event_id:
        return jsonify({"ok": False, "error": "Missing event identifier"}), 400
    db = get_db()
    try:
        db.payment_events.insert_one({"event_id": event_id, "event": event.get("event"),
            "received_at": datetime.now(UTC).replace(tzinfo=None)})
    except DuplicateKeyError:
        return jsonify({"ok": True, "duplicate": True})
    payment = (((event.get("payload") or {}).get("payment") or {}).get("entity") or {})
    if event.get("event") == "payment.failed" and payment.get("order_id"):
        db.payment_intents.update_one({"provider_order_id": payment["order_id"], "status": "pending"},
            {"$set": {"status": "failed", "failure_code": payment.get("error_code") or "provider_failed",
                      "failed_at": datetime.now(UTC).replace(tzinfo=None)}})
    return jsonify({"ok": True})

@booking_bp.route("/booking/<showtime_id>")
def start_booking(showtime_id):
    return redirect(url_for("booking.select_seats", showtime_id=showtime_id))

@booking_bp.route("/booking/<showtime_id>/seats")
def select_seats(showtime_id):
    db = get_db()
    try:
        showtime = db.showtimes.find_one({"_id": ObjectId(showtime_id)})
    except Exception:
        showtime = None

    if not showtime:
        return render_template("errors/404.html", message="Showtime not found"), 404
    available_show = enrich_available_showtime(db, showtime)
    if not available_show:
        return render_template("errors/404.html", message="This showtime is no longer available."), 404

    movie = available_show["movie"]
    theater = available_show["theater"]
    screen = available_show["screen"]

    booked_seats = showtime.get("booked_seats", [])

    # Customer tier check for discount (Plus = 10%, Pro = 20%)
    customer_tier = "FREE"
    employee_benefits = None
    if session.get("user_id"):
        cust = db.customers.find_one({"user_id": ObjectId(session["user_id"])})
        if cust:
            customer_tier, _ = MembershipService.active_membership(cust)
        elif session.get("role") == "worker":
            now = datetime.now(UTC).replace(tzinfo=None)
            available = db.reward_credits.count_documents({"worker_id": ObjectId(session["user_id"]),
                "status": "available", "expires_at": {"$gt": now}})
            employee_benefits = {
                "available_credits": available,
                "free_remaining": available,
                "family_remaining": 0,
                "policy_status": RewardService.policy().get("status", "draft")
            }

    # Screen seat layout: Rows A to H, seats 1 to 8 (64 seats)
    rows = ["A", "B", "C", "D", "E", "F"]
    seat_layout = []
    for r in rows:
        row_seats = []
        s_type = "Regular"
        if r in ["C", "D"]:
            s_type = "Premium"
        elif r in ["E", "F"]:
            s_type = "VIP"

        for n in range(1, 9):
            s_code = f"{r}{n}"
            status = "booked" if s_code in booked_seats else "available"
            row_seats.append({
                "seat_number": s_code,
                "row": r,
                "number": n,
                "type": s_type,
                "status": status
            })
        seat_layout.append({"row_name": r, "seats": row_seats, "type": s_type})

    return render_template("customer/seat_selection.html",
                           showtime=showtime,
                           movie=movie,
                           theater=theater,
                           screen=screen,
                           seat_layout=seat_layout,
                           base_price=showtime.get("base_price", 200),
                           customer_tier=customer_tier,
                           membership_plan=MembershipService.plans()[customer_tier],
                           employee_benefits=employee_benefits,
                           razorpay_key_id=razorpay_service.key_id,
                           payment_configured=razorpay_service.is_configured)

@booking_bp.route("/booking/<showtime_id>/employee-benefit", methods=["POST"])
@role_required("worker")
def book_employee_benefit(showtime_id):
    db = get_db()
    uid = ObjectId(session["user_id"])
    worker = find_worker(uid, active_only=True)
    if not worker:
        return jsonify({"success": False, "error": "Only active workers can use employee movie benefits."}), 403

    seat_numbers = [s.strip().upper() for s in request.form.get("selected_seats", "").split(",") if s.strip()]
    if len(seat_numbers) != 1:
        return jsonify({"success": False, "error": "Each reward credit covers one personal admission."}), 400
    quote, error = BookingService.quote(str(uid), showtime_id, seat_numbers)
    if error:
        return jsonify({"success": False, "error": error}), 400
    if quote["seats"][0]["type"] != "Regular":
        return jsonify({"success": False, "error": "This reward currently covers Regular seats only."}), 400
    credit, error = RewardService.reserve_credit(uid, showtime_id)
    if error:
        return jsonify({"success": False, "error": error}), 400
    hold, error = BookingService.reserve_seats(str(uid), showtime_id, seat_numbers)
    if error:
        RewardService.release_credit(credit["_id"], credit["reservation_id"])
        return jsonify({"success": False, "error": error}), 409
    pricing_override = {"discount_amount": quote["grand_total"], "discount_label": "Worker excellence reward",
                        "grand_total": 0, "employer_funded_value": quote["grand_total"]}
    booking, err = BookingService.create_booking(
        str(uid), showtime_id, seat_numbers, "Worker Reward",
        pricing_override=pricing_override,
        booking_metadata={
            "booking_owner_role": "worker",
            "reward_credit_id": credit["_id"], "reward_reservation_id": credit["reservation_id"],
            "hold_id": hold["hold_id"], "employer_funded_value": quote["grand_total"]
        }, funding_source="worker_reward",
        payment_verification={"hold_id": hold["hold_id"]}
    )
    if err:
        BookingService.release_hold(hold["hold_id"])
        RewardService.release_credit(credit["_id"], credit["reservation_id"])
        return jsonify({"success": False, "error": err}), 400

    if not RewardService.consume_credit(credit["_id"], credit["reservation_id"], booking["_id"], quote["grand_total"]):
        db.bookings.update_one({"_id": booking["_id"]}, {"$set": {"reward_ledger_status": "needs_review"}})

    log_audit("EMPLOYEE_BENEFIT_BOOKING", "booking", booking["_id"],
              f"Worker {worker.get('name', '')} redeemed one approved admission credit")
    return jsonify({
        "success": True,
        "message": f"Employee benefit applied. Ticket {booking['booking_code']} is ready.",
        "redirect_url": url_for("booking.ticket", booking_code=booking["booking_code"])
    })

@booking_bp.route("/booking/<showtime_id>/create-order", methods=["POST"])
@role_required("customer")
def create_razorpay_order(showtime_id):
    data = request.get_json() or {}
    seats_selected_str = data.get("selected_seats", "").strip()
    if not seats_selected_str:
        return jsonify({"success": False, "error": "No seats selected"}), 400

    seat_numbers = [s.strip().upper() for s in seats_selected_str.split(",") if s.strip()]
    db = get_db()
    hold, error = BookingService.reserve_seats(session["user_id"], showtime_id, seat_numbers)
    if error:
        return jsonify({"success": False, "error": error}), 409
    pricing = hold["pricing"]
    grand_total = pricing["grand_total"]

    receipt_id = f"REC_{showtime_id[-6:]}_{int(datetime.now(UTC).replace(tzinfo=None).timestamp())}"
    order, err = razorpay_service.create_order(
        amount_in_inr=grand_total,
        receipt_id=receipt_id,
        notes={
            "showtime_id": showtime_id,
            "hold_id": hold["hold_id"],
            "user_id": session.get("user_id"),
            "membership_tier": pricing.get("membership_tier", "FREE")
        }
    )

    if err:
        BookingService.release_hold(hold["hold_id"])
        return jsonify({"success": False, "error": err}), 503

    now = datetime.now(UTC).replace(tzinfo=None)
    db.payment_intents.insert_one({"provider": "razorpay", "provider_order_id": order["id"],
        "customer_id": ObjectId(session["user_id"]), "showtime_id": ObjectId(showtime_id),
        "seat_numbers": seat_numbers, "hold_id": hold["hold_id"], "amount": grand_total,
        "amount_minor": int(grand_total * 100), "currency": "INR", "pricing": pricing,
        "status": "pending", "expires_at": hold["expires_at"], "created_at": now})

    return jsonify({
        "success": True,
        "order": order,
        "key_id": razorpay_service.key_id,
        "grand_total": grand_total,
        "discount_amount": pricing.get("discount_amount", 0),
        "tier": pricing.get("membership_tier", "FREE"),
        "currency": "INR"
    })

@booking_bp.route("/booking/<showtime_id>/book-direct", methods=["POST"])
@role_required("customer")
def book_direct(showtime_id):
    import uuid
    data = request.get_json(silent=True) or request.form
    seats_selected_str = data.get("selected_seats", "").strip()
    if not seats_selected_str:
        return jsonify({"success": False, "error": "No seats selected"}), 400

    seat_numbers = [s.strip().upper() for s in seats_selected_str.split(",") if s.strip()]
    db = get_db()
    customer_id = session.get("user_id")

    hold, error = BookingService.reserve_seats(customer_id, showtime_id, seat_numbers)
    if error:
        return jsonify({"success": False, "error": error}), 409

    pricing = hold["pricing"]
    grand_total = pricing["grand_total"]

    payment_ref = f"CS-PAY-{uuid.uuid4().hex[:10].upper()}"
    order_ref = f"ORD-{uuid.uuid4().hex[:8].upper()}"

    booking, err = BookingService.create_booking(
        customer_id=customer_id,
        showtime_id=showtime_id,
        seat_numbers=seat_numbers,
        payment_method="CineSphere Instant Pay (Direct Booking)",
        payment_verification={
            "verified": True,
            "provider": "instant_checkout",
            "order_id": order_ref,
            "idempotency_key": payment_ref,
            "hold_id": hold["hold_id"]
        }
    )
    if err:
        BookingService.release_hold(hold["hold_id"])
        return jsonify({"success": False, "error": err}), 400

    db.bookings.update_one(
        {"_id": booking["_id"]},
        {"$set": {
            "razorpay_order_id": order_ref,
            "razorpay_payment_id": payment_ref,
            "is_virtual_ticket_generated": True
        }}
    )

    # Update Customer Loyalty Points
    db.customers.update_one(
        {"user_id": ObjectId(customer_id)},
        {"$inc": {"loyalty_points": 50 * len(seat_numbers)}}
    )

    # Audit & Notification
    log_audit("BOOKING_CREATED", "booking", booking["_id"], f"Direct checkout booking {booking['booking_code']} ({payment_ref})")
    create_notification(customer_id, "Ticket Confirmed!", f"Your ticket for {booking['movie_title']} ({booking['booking_code']}) is confirmed!", "success")

    flash(f"Booking confirmed! Ticket {booking['booking_code']} is ready.", "success")
    return jsonify({
        "success": True,
        "booking_code": booking["booking_code"],
        "redirect_url": url_for("booking.confirmation", booking_code=booking["booking_code"])
    })

@booking_bp.route("/booking/<showtime_id>/stripe-success")
@role_required("customer")
def stripe_success(showtime_id):
    session_id = request.args.get('session_id')
    seats_selected_str = request.args.get('seats', '')
    if not session_id or not seats_selected_str:
        flash("Invalid payment callback.", "danger")
        return redirect(url_for("booking.select_seats", showtime_id=showtime_id))

    if not stripe_service.verified_checkout(session_id):
        flash("Stripe did not verify a paid checkout. No booking was created.", "danger")
    else:
        flash("This checkout has no matching CineSphere payment intent. Contact support with the Stripe reference.", "warning")
    return redirect(url_for("booking.select_seats", showtime_id=showtime_id))

@booking_bp.route("/booking/<showtime_id>/checkout", methods=["POST"])
@role_required("customer")
def checkout(showtime_id):
    payment_method = request.form.get("payment_method", "Razorpay Online")
    razorpay_order_id = request.form.get("razorpay_order_id", "")
    razorpay_payment_id = request.form.get("razorpay_payment_id", "")
    razorpay_signature = request.form.get("razorpay_signature", "")
    customer_id = session.get("user_id")

    if not (razorpay_order_id and razorpay_payment_id and razorpay_signature):
        flash("Payment confirmation is incomplete. No booking was created.", "danger")
        return redirect(url_for("booking.select_seats", showtime_id=showtime_id))
    if not razorpay_service.verify_payment_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature):
        flash("Payment verification failed. No booking was created.", "danger")
        return redirect(url_for("booking.select_seats", showtime_id=showtime_id))

    db = get_db()
    intent = db.payment_intents.find_one({"provider_order_id": razorpay_order_id,
        "customer_id": ObjectId(customer_id), "showtime_id": ObjectId(showtime_id)})
    if not intent:
        flash("The payment does not match a CineSphere checkout intent. No booking was created.", "danger")
        return redirect(url_for("booking.select_seats", showtime_id=showtime_id))
    provider_payment = razorpay_service.fetch_verified_payment(razorpay_payment_id)
    if not provider_payment or provider_payment.get("order_id") != razorpay_order_id \
            or int(provider_payment.get("amount", -1)) != int(intent["amount_minor"]) \
            or str(provider_payment.get("currency", "")).upper() != "INR":
        flash("The gateway payment status or amount could not be verified. No booking was created.", "danger")
        return redirect(url_for("booking.select_seats", showtime_id=showtime_id))

    booking, err = BookingService.create_booking(customer_id, showtime_id, intent["seat_numbers"], payment_method,
        payment_verification={"verified": True, "provider": "razorpay", "order_id": razorpay_order_id,
                              "idempotency_key": razorpay_payment_id, "hold_id": intent.get("hold_id")})
    if err:
        flash(err, "danger")
        return redirect(url_for("booking.select_seats", showtime_id=showtime_id))

    db.bookings.update_one(
        {"_id": booking["_id"]},
        {"$set": {
            "razorpay_order_id": razorpay_order_id,
            "razorpay_payment_id": razorpay_payment_id,
            "is_virtual_ticket_generated": True
        }}
    )
    db.payment_intents.update_one({"_id": intent["_id"]}, {"$set": {
        "status": "confirmed", "provider_payment_id": razorpay_payment_id,
        "booking_id": booking["_id"], "confirmed_at": datetime.now(UTC).replace(tzinfo=None)}})

    # Update Customer Loyalty Points
    db.customers.update_one(
        {"user_id": ObjectId(customer_id)},
        {"$inc": {"loyalty_points": 50 * len(intent["seat_numbers"])}}
    )

    # Audit & Notification
    log_audit("BOOKING_CREATED", "booking", booking["_id"], f"Verified Razorpay booking {booking['booking_code']} ({razorpay_payment_id})")
    create_notification(customer_id, "Ticket Confirmed!", f"Your virtual ticket for {booking['movie_title']} ({booking['booking_code']}) is ready.", "success")

    flash(f"Payment successful! Virtual Ticket {booking['booking_code']} generated automatically.", "success")
    return redirect(url_for("booking.confirmation", booking_code=booking["booking_code"]))

@booking_bp.route("/booking/confirmation/<booking_code>")
@login_required
def confirmation(booking_code):
    db = get_db()
    booking = db.bookings.find_one({"booking_code": booking_code})
    if not booking:
        return render_template("errors/404.html", message="Booking not found"), 404

    # Authorization check
    if str(booking["customer_id"]) != session.get("user_id") and session.get("role") not in ["admin", "staff"]:
        abort(403)
    if session.get("role") == "staff" and str(booking.get("theater_id")) != session.get("theater_id"):
        abort(403)

    return render_template("customer/booking_confirmation.html", booking=booking)

@booking_bp.route("/booking/ticket/<booking_code>")
@login_required
def ticket(booking_code):
    db = get_db()
    booking = db.bookings.find_one({"booking_code": booking_code})
    if not booking:
        return render_template("errors/404.html", message="Ticket not found"), 404

    if str(booking["customer_id"]) != session.get("user_id") and session.get("role") not in ["admin", "staff"]:
        abort(403)
    if session.get("role") == "staff" and str(booking.get("theater_id")) != session.get("theater_id"):
        abort(403)
    if booking.get("status") != "Confirmed":
        flash("Only confirmed, active bookings have an admission ticket.", "warning")
        return redirect(url_for("customer.my_bookings"))

    admission_token = TicketService.token_for(booking)
    return render_template("customer/ticket.html", booking=booking, admission_token=admission_token,
                           qr_data_uri=TicketService.qr_data_uri(admission_token))
