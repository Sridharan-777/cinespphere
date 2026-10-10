from flask import Blueprint, render_template, request, redirect, url_for, flash, session, abort, jsonify
from bson import ObjectId
from datetime import UTC, datetime
from cinesphere.models.database import get_db
from cinesphere.models.worker_model import find_worker
from cinesphere.utils.decorators import login_required, role_required
from cinesphere.services.booking_service import BookingService
from cinesphere.services.razorpay_service import razorpay_service
from cinesphere.services.stripe_service import stripe_service
from cinesphere.utils.audit import log_audit, create_notification

booking_bp = Blueprint("booking", __name__)

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

    movie = db.movies.find_one({"_id": showtime["movie_id"]})
    theater = db.theaters.find_one({"_id": showtime["theater_id"]})
    screen = db.screens.find_one({"_id": showtime["screen_id"]})

    booked_seats = showtime.get("booked_seats", [])

    # Customer tier check for discount (Plus = 10%, Pro = 20%)
    customer_tier = "Free"
    employee_benefits = None
    if session.get("user_id"):
        cust = db.customers.find_one({"user_id": ObjectId(session["user_id"])})
        if cust:
            customer_tier = cust.get("tier", "Free")
        elif session.get("role") == "worker":
            month_key = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m")
            used = list(db.bookings.find({
                "customer_id": ObjectId(session["user_id"]),
                "employee_benefit.month": month_key,
                "status": {"$ne": "Cancelled"}
            }))
            free_used = sum(len(b.get("seats", [])) for b in used
                            if b.get("employee_benefit", {}).get("type") == "free_pass")
            family_used = sum(len(b.get("seats", [])) for b in used
                              if b.get("employee_benefit", {}).get("type") == "family_discount")
            employee_benefits = {
                "free_remaining": max(0, 2 - free_used),
                "family_remaining": max(0, 4 - family_used),
                "month": month_key
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

    benefit_type = request.form.get("benefit_type", "")
    if benefit_type not in {"free_pass", "family_discount"}:
        return jsonify({"success": False, "error": "Choose a valid employee benefit."}), 400

    seat_numbers = [s.strip().upper() for s in request.form.get("selected_seats", "").split(",") if s.strip()]
    if not seat_numbers:
        return jsonify({"success": False, "error": "Please select at least one seat."}), 400
    valid_seats = {f"{row}{number}" for row in "ABCDEF" for number in range(1, 9)}
    if len(seat_numbers) != len(set(seat_numbers)) or any(seat not in valid_seats for seat in seat_numbers):
        return jsonify({"success": False, "error": "One or more selected seats are invalid."}), 400

    month_key = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m")
    prior = list(db.bookings.find({
        "customer_id": uid,
        "employee_benefit.month": month_key,
        "employee_benefit.type": benefit_type,
        "status": {"$ne": "Cancelled"}
    }))
    used = sum(len(b.get("seats", [])) for b in prior)
    allowance = 2 if benefit_type == "free_pass" else 4
    if len(seat_numbers) > allowance - used:
        label = "free-pass" if benefit_type == "free_pass" else "family-discount"
        return jsonify({"success": False, "error": f"Only {max(0, allowance - used)} {label} ticket(s) remain this month."}), 400

    try:
        showtime = db.showtimes.find_one({"_id": ObjectId(showtime_id)})
    except Exception:
        showtime = None
    screen = db.screens.find_one({"_id": showtime["screen_id"]}) if showtime else None
    if not showtime or not screen:
        return jsonify({"success": False, "error": "Showtime not found."}), 404

    seats = []
    for number in seat_numbers:
        row = number[0].upper()
        seat_type = "VIP" if row in {"E", "F"} else ("Premium" if row in {"C", "D"} else "Regular")
        seats.append({"seat_number": number, "type": seat_type})
    standard = BookingService.calculate_price(showtime.get("base_price", 200), seats, screen.get("screen_type", "Standard"))
    discount = standard["grand_total"] if benefit_type == "free_pass" else round(standard["subtotal"] * 0.25)
    final_total = max(0, standard["grand_total"] - discount)
    pricing_override = {
        "discount_amount": discount,
        "discount_label": "Employee free pass" if benefit_type == "free_pass" else "Worker family discount (25%)",
        "grand_total": final_total
    }
    booking, err = BookingService.create_booking(
        str(uid), showtime_id, seat_numbers, "Employee Benefit",
        pricing_override=pricing_override,
        booking_metadata={
            "booking_owner_role": "worker",
            "employee_benefit": {"type": benefit_type, "month": month_key, "worker_id": worker["_id"]},
            "is_virtual_ticket_generated": True
        }
    )
    if err:
        return jsonify({"success": False, "error": err}), 400

    log_audit("EMPLOYEE_BENEFIT_BOOKING", "booking", booking["_id"],
              f"Worker {worker.get('name', '')} used {benefit_type} for {len(seat_numbers)} ticket(s)")
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

    seat_numbers = [s.strip() for s in seats_selected_str.split(",") if s.strip()]
    db = get_db()
    showtime = db.showtimes.find_one({"_id": ObjectId(showtime_id)})
    if not showtime:
        return jsonify({"success": False, "error": "Showtime not found"}), 404

    # Calculate pricing
    screen = db.screens.find_one({"_id": showtime["screen_id"]})
    seats_data = []
    for s_code in seat_numbers:
        row = s_code[0].upper()
        s_type = "VIP" if row in ["E", "F"] else ("Premium" if row in ["C", "D"] else "Regular")
        seats_data.append({"seat_number": s_code, "type": s_type})

    pricing = BookingService.calculate_price(
        base_price=showtime.get("base_price", 200),
        seats=seats_data,
        screen_type=screen.get("screen_type", "Standard") if screen else "Standard"
    )

    # Membership Tier Discount
    cust = db.customers.find_one({"user_id": ObjectId(session.get("user_id"))})
    tier = cust.get("tier", "Free") if cust else "Free"
    discount_pct = 0.20 if tier == "Pro" else (0.10 if tier == "Plus" else 0.0)
    discount_amount = round(pricing["subtotal"] * discount_pct)
    grand_total = max(1, pricing["grand_total"] - discount_amount)

    receipt_id = f"REC_{showtime_id[-6:]}_{int(datetime.now(UTC).replace(tzinfo=None).timestamp())}"
    order, err = razorpay_service.create_order(
        amount_in_inr=grand_total,
        receipt_id=receipt_id,
        notes={
            "showtime_id": showtime_id,
            "seats": seats_selected_str,
            "user_id": session.get("user_id"),
            "tier": tier
        }
    )

    if err:
        return jsonify({"success": False, "error": err}), 500

    return jsonify({
        "success": True,
        "order": order,
        "key_id": razorpay_service.key_id,
        "grand_total": grand_total,
        "discount_amount": discount_amount,
        "tier": tier,
        "currency": "INR"
    })

@booking_bp.route("/booking/<showtime_id>/book-direct", methods=["POST"])
@role_required("customer")
def book_direct(showtime_id):
    seats_selected_str = request.form.get("selected_seats", "").strip()
    if not seats_selected_str:
        return jsonify({"success": False, "error": "Please select at least one seat to proceed."}), 400

    seat_numbers = [s.strip() for s in seats_selected_str.split(",") if s.strip()]
    customer_id = session.get("user_id")

    # Create a direct booking when the online payment gateway is unavailable.
    booking, err = BookingService.create_booking(customer_id, showtime_id, seat_numbers, "Direct Booking")
    if err:
        return jsonify({"success": False, "error": err}), 400

    # Set as virtual ticket generated
    db = get_db()
    db.bookings.update_one(
        {"_id": booking["_id"]},
        {"$set": {
            "is_virtual_ticket_generated": True
        }}
    )

    # Update Customer Loyalty Points
    db.customers.update_one(
        {"user_id": ObjectId(customer_id)},
        {"$inc": {"loyalty_points": 50 * len(seat_numbers)}}
    )

    # Audit & Notification
    log_audit("BOOKING_CREATED", "booking", booking["_id"], f"Customer booked {', '.join(seat_numbers)} for {booking['movie_title']}")
    create_notification(customer_id, "Ticket Confirmed!", f"Your virtual ticket for {booking['movie_title']} ({booking['booking_code']}) is ready.", "success")

    return jsonify({
        "success": True,
        "message": f"Booking successful! Ticket {booking['booking_code']} generated.",
        "redirect_url": url_for("booking.ticket", booking_code=booking["booking_code"])
    })

@booking_bp.route("/booking/<showtime_id>/stripe-success")
@role_required("customer")
def stripe_success(showtime_id):
    session_id = request.args.get('session_id')
    seats_selected_str = request.args.get('seats', '')
    if not session_id or not seats_selected_str:
        flash("Invalid payment callback.", "danger")
        return redirect(url_for("booking.select_seats", showtime_id=showtime_id))

    seat_numbers = [s.strip() for s in seats_selected_str.split(",") if s.strip()]
    customer_id = session.get("user_id")

    # Create Booking
    booking, err = BookingService.create_booking(customer_id, showtime_id, seat_numbers, "Stripe Checkout")
    if err:
        flash(err, "danger")
        return redirect(url_for("booking.select_seats", showtime_id=showtime_id))

    # Append Stripe Transaction details
    db = get_db()
    db.bookings.update_one(
        {"_id": booking["_id"]},
        {"$set": {
            "stripe_session_id": session_id,
            "is_virtual_ticket_generated": True
        }}
    )

    # Update Customer Loyalty Points
    db.customers.update_one(
        {"user_id": ObjectId(customer_id)},
        {"$inc": {"loyalty_points": 50 * len(seat_numbers)}}
    )

    # Audit & Notification
    log_audit("BOOKING_CREATED", "booking", booking["_id"], f"Customer booked {', '.join(seat_numbers)} for {booking['movie_title']} via Stripe")
    create_notification(customer_id, "Ticket Confirmed!", f"Your virtual ticket for {booking['movie_title']} ({booking['booking_code']}) is ready.", "success")

    flash(f"Stripe payment successful! Virtual Ticket {booking['booking_code']} generated.", "success")
    return redirect(url_for("booking.confirmation", booking_code=booking["booking_code"]))

@booking_bp.route("/booking/<showtime_id>/checkout", methods=["POST"])
@role_required("customer")
def checkout(showtime_id):
    seats_selected_str = request.form.get("selected_seats", "").strip()
    payment_method = request.form.get("payment_method", "Razorpay Online")
    razorpay_order_id = request.form.get("razorpay_order_id", "")
    razorpay_payment_id = request.form.get("razorpay_payment_id", f"pay_rzp_{int(datetime.now(UTC).replace(tzinfo=None).timestamp())}")
    razorpay_signature = request.form.get("razorpay_signature", "")

    if not seats_selected_str:
        flash("Please select at least one seat to proceed.", "warning")
        return redirect(url_for("booking.select_seats", showtime_id=showtime_id))

    seat_numbers = [s.strip() for s in seats_selected_str.split(",") if s.strip()]
    customer_id = session.get("user_id")

    if not (razorpay_order_id and razorpay_payment_id and razorpay_signature):
        flash("Payment confirmation is incomplete. No booking was created.", "danger")
        return redirect(url_for("booking.select_seats", showtime_id=showtime_id))
    if not razorpay_service.verify_payment_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature):
        flash("Payment verification failed. No booking was created.", "danger")
        return redirect(url_for("booking.select_seats", showtime_id=showtime_id))

    # Create Booking
    booking, err = BookingService.create_booking(customer_id, showtime_id, seat_numbers, payment_method)
    if err:
        flash(err, "danger")
        return redirect(url_for("booking.select_seats", showtime_id=showtime_id))

    # Append Razorpay Transaction details
    db = get_db()
    db.bookings.update_one(
        {"_id": booking["_id"]},
        {"$set": {
            "razorpay_order_id": razorpay_order_id,
            "razorpay_payment_id": razorpay_payment_id,
            "is_virtual_ticket_generated": True
        }}
    )

    # Update Customer Loyalty Points
    db.customers.update_one(
        {"user_id": ObjectId(customer_id)},
        {"$inc": {"loyalty_points": 50 * len(seat_numbers)}}
    )

    # Audit & Notification
    log_audit("BOOKING_CREATED", "booking", booking["_id"], f"Customer booked {', '.join(seat_numbers)} for {booking['movie_title']} via Razorpay ({razorpay_payment_id})")
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

    return render_template("customer/ticket.html", booking=booking)
