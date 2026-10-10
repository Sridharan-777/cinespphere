import uuid
from datetime import UTC, datetime
from bson import ObjectId
from cinesphere.models.database import get_db

class BookingService:
    @staticmethod
    def calculate_price(base_price, seats, screen_type="Standard", is_weekend=False):
        """
        Base Ticket Price + Seat Premium + 3D/4DX Charges + Convenience Fee + GST
        """
        subtotal = 0
        seat_breakdown = []
        
        # Screen surcharge
        screen_surcharge = 0
        if screen_type in ["3D", "IMAX"]:
            screen_surcharge = 80
        elif screen_type == "4DX":
            screen_surcharge = 150
        elif screen_type == "VIP":
            screen_surcharge = 120
            
        weekend_surcharge = 50 if is_weekend else 0

        for seat in seats:
            seat_type = seat.get("type", "Regular")
            multiplier = 1.0
            if seat_type == "Premium":
                multiplier = 1.4
            elif seat_type == "VIP":
                multiplier = 2.0
            elif seat_type == "Couple":
                multiplier = 2.2
                
            seat_price = round((base_price * multiplier) + screen_surcharge + weekend_surcharge)
            subtotal += seat_price
            seat_breakdown.append({
                "seat_number": seat.get("seat_number"),
                "type": seat_type,
                "price": seat_price
            })

        convenience_fee = 35 * len(seats)
        gst = round((subtotal + convenience_fee) * 0.18)
        grand_total = subtotal + convenience_fee + gst

        return {
            "seats": seat_breakdown,
            "subtotal": subtotal,
            "screen_surcharge": screen_surcharge * len(seats),
            "weekend_surcharge": weekend_surcharge * len(seats),
            "convenience_fee": convenience_fee,
            "gst": gst,
            "grand_total": grand_total
        }

    @staticmethod
    def create_booking(customer_id, showtime_id, seat_numbers, payment_method="UPI",
                       pricing_override=None, booking_metadata=None):
        db = get_db()
        showtime = db.showtimes.find_one({"_id": ObjectId(showtime_id)})
        if not showtime:
            return None, "Showtime not found."

        # Fetch theater and screen info
        theater = db.theaters.find_one({"_id": showtime["theater_id"]})
        screen = db.screens.find_one({"_id": showtime["screen_id"]})
        movie = db.movies.find_one({"_id": showtime["movie_id"]})
        customer = db.customers.find_one({"user_id": ObjectId(customer_id)})
        user = db.users.find_one({"_id": ObjectId(customer_id)})
        
        if not movie or not theater or not screen:
            return None, "Show details incomplete."

        # Check existing bookings for these seats
        booked_seats = set(showtime.get("booked_seats", []))
        for s in seat_numbers:
            if s in booked_seats:
                return None, f"Seat {s} has already been booked by another user. Please choose another seat."

        # Generate seat objects with types
        seat_docs = list(db.seats.find({"screen_id": screen["_id"], "seat_number": {"$in": seat_numbers}}))
        # If seat documents aren't stored individually, infer type from row
        seats_pricing_list = []
        for s_num in seat_numbers:
            seat_obj = next((s for s in seat_docs if s["seat_number"] == s_num), None)
            if seat_obj:
                seats_pricing_list.append(seat_obj)
            else:
                s_type = "VIP" if s_num.startswith("E") or s_num.startswith("F") else ("Premium" if s_num.startswith("C") or s_num.startswith("D") else "Regular")
                seats_pricing_list.append({"seat_number": s_num, "type": s_type})

        # Calculate pricing
        pricing = BookingService.calculate_price(
            base_price=showtime.get("base_price", 200),
            seats=seats_pricing_list,
            screen_type=screen.get("screen_type", "Standard")
        )
        if pricing_override:
            pricing = {**pricing, **pricing_override}

        booking_code = f"CS-2026-{uuid.uuid4().hex[:6].upper()}"
        transaction_id = f"TXN-{uuid.uuid4().hex[:8].upper()}"

        # Insert Payment
        payment_doc = {
            "transaction_id": transaction_id,
            "amount": pricing["grand_total"],
            "payment_method": payment_method,
            "status": "Successful",
            "created_at": datetime.now(UTC).replace(tzinfo=None),
            "gateway": {
                "provider": payment_method,
                "currency": "INR",
                "mode": "internal" if payment_method in {"Employee Benefit", "Direct Booking"} else "online"
            }
        }
        pay_res = db.payments.insert_one(payment_doc)

        # Create Booking
        booking_doc = {
            "booking_code": booking_code,
            "customer_id": ObjectId(customer_id),
            "showtime_id": showtime["_id"],
            "movie_id": movie["_id"],
            "movie_title": movie["title"],
            "movie_poster": movie.get("poster_url", ""),
            "theater_id": theater["_id"],
            "theater_name": theater["name"],
            "screen_id": screen["_id"],
            "screen_name": screen["name"],
            "show_date": showtime["show_date"],
            "start_time": showtime["start_time"],
            "end_time": showtime.get("end_time", ""),
            "seats": seat_numbers,
            "pricing": pricing,
            # Embedded snapshots keep a ticket historically accurate even if names or
            # contact details change in their source collections later.
            "customer_snapshot": {
                "name": (customer or {}).get("name") or (user or {}).get("name", "Customer"),
                "email": (customer or {}).get("email") or (user or {}).get("email", ""),
                "tier": (customer or {}).get("tier", "Free")
            },
            "show_snapshot": {
                "movie": {"title": movie["title"], "language": movie.get("language", "")},
                "venue": {"theater": theater["name"], "screen": screen["name"], "screen_type": screen.get("screen_type", "Standard")},
                "schedule": {"date": showtime["show_date"], "start_time": showtime["start_time"], "end_time": showtime.get("end_time", "")}
            },
            "payment_summary": {
                "transaction_id": transaction_id,
                "method": payment_method,
                "amount": pricing["grand_total"],
                "currency": "INR",
                "status": "Successful"
            },
            "schema_version": 2,
            "payment_id": pay_res.inserted_id,
            "transaction_id": transaction_id,
            "status": "Confirmed", # Confirmed, Cancelled, Completed
            "created_at": datetime.now(UTC).replace(tzinfo=None)
        }
        if booking_metadata:
            booking_doc.update(booking_metadata)
        b_res = db.bookings.insert_one(booking_doc)

        # Update Showtime booked_seats
        db.showtimes.update_one(
            {"_id": showtime["_id"]},
            {"$addToSet": {"booked_seats": {"$each": seat_numbers}}}
        )

        # Update customer stats
        db.customers.update_one(
            {"user_id": ObjectId(customer_id)},
            {
                "$inc": {"total_bookings": 1, "total_spent": pricing["grand_total"]},
                "$set": {"updated_at": datetime.now(UTC).replace(tzinfo=None)}
            }
        )

        return booking_doc, None
