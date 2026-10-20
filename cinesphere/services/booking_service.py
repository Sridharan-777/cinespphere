import uuid
from datetime import UTC, datetime, timedelta

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from cinesphere.models.database import get_db
from cinesphere.services.membership_service import MembershipService


class BookingService:
    VALID_STATUSES = {"Pending", "Confirmed", "Cancelled", "Refund Pending", "Refunded", "Completed"}

    @staticmethod
    def calculate_price(base_price, seats, screen_type="Standard", is_weekend=False):
        subtotal = 0
        seat_breakdown = []
        screen_surcharge = 80 if screen_type in {"3D", "IMAX", "IMAX Laser"} else (150 if screen_type == "4DX" else (120 if screen_type == "VIP" else 0))
        weekend_surcharge = 50 if is_weekend else 0
        for seat in seats:
            seat_type = seat.get("type", "Regular")
            multiplier = {"Premium": 1.4, "VIP": 2.0, "Couple": 2.2}.get(seat_type, 1.0)
            seat_price = round((float(base_price) * multiplier) + screen_surcharge + weekend_surcharge)
            subtotal += seat_price
            seat_breakdown.append({"seat_number": seat.get("seat_number"), "type": seat_type, "price": seat_price})
        convenience_fee = 35 * len(seats)
        gst = round((subtotal + convenience_fee) * 0.18)
        return {"seats": seat_breakdown, "subtotal": subtotal,
                "screen_surcharge": screen_surcharge * len(seats),
                "weekend_surcharge": weekend_surcharge * len(seats),
                "convenience_fee": convenience_fee, "gst": gst,
                "grand_total": subtotal + convenience_fee + gst}

    @staticmethod
    def _context(customer_id, showtime_id, seat_numbers):
        db = get_db()
        try:
            customer_oid, showtime_oid = ObjectId(customer_id), ObjectId(showtime_id)
        except Exception:
            return None, "Invalid customer or showtime identifier."
        normalized = [str(seat).strip().upper() for seat in seat_numbers if str(seat).strip()]
        if not normalized or len(normalized) != len(set(normalized)) or len(normalized) > 10:
            return None, "Select between one and ten unique seats."
        today = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d")
        showtime = db.showtimes.find_one({"_id": showtime_oid, "status": "active",
                                          "show_date": {"$gte": today}})
        if not showtime:
            return None, "Showtime is unavailable."
        theater = db.theaters.find_one({"_id": showtime.get("theater_id"), "status": "active"})
        screen = db.screens.find_one({"_id": showtime.get("screen_id"),
                                      "theater_id": showtime.get("theater_id"), "status": "active"})
        movie = db.movies.find_one({"_id": showtime.get("movie_id"), "status": {"$ne": "Archived"}})
        user = db.users.find_one({"_id": customer_oid, "status": "active"})
        if not theater or not screen or not movie or not user:
            return None, "Show or account details are incomplete."
        seat_docs = list(db.seats.find({"screen_id": screen["_id"], "seat_number": {"$in": normalized}}))
        by_number = {seat["seat_number"]: seat for seat in seat_docs}
        capacity = int(screen.get("capacity", 48))
        fallback = {f"{row}{number}" for row in "ABCDEF" for number in range(1, max(8, capacity // 6) + 1)}
        if seat_docs and any(number not in by_number for number in normalized):
            return None, "One or more selected seats do not exist in this screen."
        if not seat_docs and any(number not in fallback for number in normalized):
            return None, "One or more selected seats are invalid."
        seats = []
        for number in normalized:
            stored = by_number.get(number, {})
            if stored.get("status") == "blocked":
                return None, f"Seat {number} is blocked."
            inferred = "VIP" if number[0] in "EF" else ("Premium" if number[0] in "CD" else "Regular")
            seats.append({"seat_number": number, "type": stored.get("type", inferred)})
        customer = db.customers.find_one({"user_id": customer_oid})
        pricing = BookingService.calculate_price(showtime.get("base_price", 200), seats, screen.get("screen_type", "Standard"))
        discount = MembershipService.ticket_discount(customer, pricing["subtotal"])
        pricing.update({"discount_amount": discount["amount"], "discount_label": discount["label"],
                        "membership_tier": discount["tier"],
                        "grand_total": max(0, pricing["grand_total"] - discount["amount"])})
        return {"db": db, "customer_oid": customer_oid, "customer": customer, "user": user,
                "showtime": showtime, "theater": theater, "screen": screen, "movie": movie,
                "seat_numbers": normalized, "pricing": pricing}, None

    @classmethod
    def quote(cls, customer_id, showtime_id, seat_numbers):
        context, error = cls._context(customer_id, showtime_id, seat_numbers)
        return (context["pricing"], None) if context else (None, error)

    @classmethod
    def reserve_seats(cls, customer_id, showtime_id, seat_numbers, hold_seconds=600):
        context, error = cls._context(customer_id, showtime_id, seat_numbers)
        if error:
            return None, error
        db = context["db"]
        now = datetime.now(UTC).replace(tzinfo=None)
        db.seat_holds.delete_many({"expires_at": {"$lte": now}})
        if any(seat in context["showtime"].get("booked_seats", []) for seat in context["seat_numbers"]):
            return None, "One or more seats are no longer available."
        hold_id = uuid.uuid4().hex
        expires_at = now + timedelta(seconds=hold_seconds)
        created = []
        try:
            for seat in context["seat_numbers"]:
                result = db.seat_holds.insert_one({"showtime_id": context["showtime"]["_id"],
                    "seat_number": seat, "customer_id": context["customer_oid"], "hold_id": hold_id,
                    "expires_at": expires_at, "created_at": now})
                created.append(result.inserted_id)
        except DuplicateKeyError:
            if created:
                db.seat_holds.delete_many({"_id": {"$in": created}})
            return None, "One or more seats are currently held by another customer."
        return {"hold_id": hold_id, "expires_at": expires_at, "pricing": context["pricing"],
                "seat_numbers": context["seat_numbers"]}, None

    @staticmethod
    def release_hold(hold_id):
        if hold_id:
            get_db().seat_holds.delete_many({"hold_id": hold_id})

    @classmethod
    def create_booking(cls, customer_id, showtime_id, seat_numbers, payment_method="UPI",
                       pricing_override=None, booking_metadata=None, payment_verification=None,
                       funding_source="cash"):
        """Atomically claim seats and create an idempotent confirmed booking.

        Cash bookings require a verified provider reference. Reward-funded bookings use
        ``funding_source='worker_reward'`` and intentionally create no fake payment.
        """
        context, error = cls._context(customer_id, showtime_id, seat_numbers)
        if error:
            return None, error
        db = context["db"]
        payment_verification = payment_verification or {}
        idempotency_key = payment_verification.get("idempotency_key")
        if funding_source == "cash":
            if not payment_verification.get("verified") or not idempotency_key:
                return None, "Verified payment confirmation is required."
        elif funding_source != "worker_reward":
            return None, "Unsupported booking funding source."
        else:
            idempotency_key = idempotency_key or (booking_metadata or {}).get("reward_reservation_id")
            if not idempotency_key:
                return None, "An approved reward reservation is required."
            reward_credit = db.reward_credits.find_one({"worker_id": context["customer_oid"],
                "status": "reserved", "reservation_id": idempotency_key,
                "reserved_for_showtime": context["showtime"]["_id"],
                "reservation_expires_at": {"$gt": datetime.now(UTC).replace(tzinfo=None)}})
            if not reward_credit or str((booking_metadata or {}).get("reward_credit_id")) != str(reward_credit["_id"]):
                return None, "The approved reward reservation is invalid or expired."

        existing = db.bookings.find_one({"confirmation_key": idempotency_key})
        if existing:
            return existing, None

        pricing = dict(context["pricing"])
        if pricing_override:
            allowed = {"discount_amount", "discount_label", "grand_total", "employer_funded_value"}
            pricing.update({key: value for key, value in pricing_override.items() if key in allowed})

        claimed = db.showtimes.update_one(
            {"_id": context["showtime"]["_id"], "status": "active",
             "booked_seats": {"$nin": context["seat_numbers"]}},
            {"$addToSet": {"booked_seats": {"$each": context["seat_numbers"]}}},
        )
        if claimed.modified_count != 1:
            return None, "One or more seats were just booked. Refresh and choose again."

        now = datetime.now(UTC).replace(tzinfo=None)
        booking_code = f"CS-{now.year}-{uuid.uuid4().hex[:6].upper()}"
        booking = {
            "booking_code": booking_code, "confirmation_key": idempotency_key,
            "customer_id": context["customer_oid"], "showtime_id": context["showtime"]["_id"],
            "movie_id": context["movie"]["_id"], "movie_title": context["movie"]["title"],
            "movie_poster": context["movie"].get("poster_url", ""),
            "theater_id": context["theater"]["_id"], "theater_name": context["theater"]["name"],
            "screen_id": context["screen"]["_id"], "screen_name": context["screen"]["name"],
            "show_date": context["showtime"]["show_date"], "start_time": context["showtime"]["start_time"],
            "end_time": context["showtime"].get("end_time", ""), "seats": context["seat_numbers"],
            "pricing": pricing, "funding_source": funding_source,
            "customer_snapshot": {"name": context["user"].get("name", "Customer"),
                                  "email": context["user"].get("email", ""),
                                  "tier": pricing.get("membership_tier", "FREE")},
            "show_snapshot": {"movie": {"title": context["movie"]["title"], "language": context["movie"].get("language", "")},
                              "venue": {"theater": context["theater"]["name"], "screen": context["screen"]["name"],
                                        "screen_type": context["screen"].get("screen_type", "Standard")},
                              "schedule": {"date": context["showtime"]["show_date"], "start_time": context["showtime"]["start_time"],
                                           "end_time": context["showtime"].get("end_time", "")}},
            "schema_version": 3, "status": "Confirmed", "is_virtual_ticket_generated": True,
            "created_at": now,
        }
        if booking_metadata:
            booking.update(booking_metadata)
        try:
            if funding_source == "cash":
                payment = {"transaction_id": idempotency_key, "provider": payment_verification.get("provider"),
                           "provider_order_id": payment_verification.get("order_id"),
                           "amount": pricing["grand_total"], "currency": "INR", "payment_method": payment_method,
                           "status": "Verified", "verified_at": now, "created_at": now}
                payment_result = db.payments.insert_one(payment)
                booking["payment_id"] = payment_result.inserted_id
                booking["payment_summary"] = {"transaction_id": idempotency_key, "method": payment_method,
                                              "amount": pricing["grand_total"], "currency": "INR", "status": "Verified"}
            result = db.bookings.insert_one(booking)
            booking["_id"] = result.inserted_id
        except DuplicateKeyError:
            db.showtimes.update_one({"_id": context["showtime"]["_id"]}, {"$pull": {"booked_seats": {"$in": context["seat_numbers"]}}})
            return db.bookings.find_one({"confirmation_key": idempotency_key}), None
        except Exception:
            db.showtimes.update_one({"_id": context["showtime"]["_id"]}, {"$pull": {"booked_seats": {"$in": context["seat_numbers"]}}})
            raise
        db.customers.update_one({"user_id": context["customer_oid"]}, {"$inc": {
            "total_bookings": 1, "total_spent": pricing["grand_total"]}, "$set": {"updated_at": now}})
        cls.release_hold(payment_verification.get("hold_id") or (booking_metadata or {}).get("hold_id"))
        return booking, None
