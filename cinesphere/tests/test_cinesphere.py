import unittest
import uuid
from datetime import UTC, datetime
from bson import ObjectId
from werkzeug.security import check_password_hash
from cinesphere.app import create_app
from cinesphere.models.database import get_db
from cinesphere.services.booking_service import BookingService
from cinesphere.services.razorpay_service import razorpay_service

class CineSphereTestSuite(unittest.TestCase):
    def setUp(self):
        self.app = create_app("development")
        self.client = self.app.test_client()
        self.db = get_db(self.app)

    def test_homepage(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"CineSphere", response.data)

    def test_movie_pages_include_trailers_and_available_theaters(self):
        movie = self.db.movies.find_one({"status": {"$ne": "Archived"}})
        self.assertIsNotNone(movie)
        movie_page = self.client.get(f"/movies/{movie['_id']}")
        self.assertEqual(movie_page.status_code, 200)
        self.assertIn(b"Watch the trailer", movie_page.data)

        theater_page = self.client.get("/theaters")
        self.assertEqual(theater_page.status_code, 200)
        available_show = next(iter(self.db.showtimes.find({
            "status": "active", "show_date": {"$gte": datetime.now(UTC).date().isoformat()}
        })), None)
        self.assertIsNotNone(available_show)
        theater = self.db.theaters.find_one({"_id": available_show["theater_id"]})
        self.assertIn(theater["name"].encode(), theater_page.data)
        self.assertEqual(self.client.get(f"/theaters/{theater['_id']}").status_code, 200)
        self.assertEqual(self.client.get(f"/booking/{available_show['_id']}/seats").status_code, 200)
        self.assertEqual(self.client.get(f"/api/showtimes/{available_show['_id']}/seats").status_code, 200)
        self.assertEqual(self.client.get("/api/theaters").status_code, 200)

    def test_role_portals_and_cross_role_denial(self):
        role_pages = {
            "admin": ["/admin/dashboard", "/admin/theaters", "/admin/staff", "/admin/movies", "/admin/reviews", "/admin/users", "/admin/sessions", "/admin/audit-logs", "/admin/rewards", "/admin/payroll", "/admin/export/report", "/admin/operations"],
            "staff": ["/staff/dashboard", "/staff/customers", "/staff/workers", "/staff/shows", "/staff/tasks", "/staff/reports", "/staff/rewards", "/staff/payroll", "/staff/support", "/staff/movies"],
            "worker": ["/worker/dashboard", "/worker/tasks", "/worker/attendance", "/worker/profile", "/worker/rewards", "/worker/payroll", "/worker/admission"],
            "customer": ["/customer/dashboard", "/customer/bookings", "/customer/favorites", "/customer/reviews", "/customer/profile", "/customer/notifications", "/customer/rewards", "/customer/plans", "/customer/support", "/customer/live-shows"]
        }
        for role, pages in role_pages.items():
            user = self.db.users.find_one({"role": role, "status": "active"})
            self.assertIsNotNone(user, f"Missing active {role} test user")
            with self.client.session_transaction() as user_session:
                user_session.clear()
                user_session["user_id"] = str(user["_id"])
                user_session["role"] = role
                user_session["name"] = user.get("name", role.title())
            for page in pages:
                self.assertEqual(self.client.get(page).status_code, 200, f"{role} could not open {page}")

        customer = self.db.users.find_one({"role": "customer", "status": "active"})
        with self.client.session_transaction() as user_session:
            user_session.clear()
            user_session["user_id"] = str(customer["_id"])
            user_session["role"] = "customer"
        self.assertEqual(self.client.get("/admin/dashboard").status_code, 403)
        self.assertEqual(self.client.get("/staff/dashboard").status_code, 403)
        self.assertEqual(self.client.get("/worker/dashboard").status_code, 403)

    def test_theaters_capacity_rule(self):
        theaters = list(self.db.theaters.find())
        for t in theaters:
            staff_count = self.db.staff.count_documents({"theater_id": t["_id"], "status": "active"})
            worker_count = self.db.users.count_documents({"role": "worker", "worker_profile.theater_id": t["_id"], "status": "active"})
            self.assertLessEqual(staff_count, 5, f"Theater {t['name']} exceeded 5 staff members")
            self.assertLessEqual(worker_count, 20, f"Theater {t['name']} exceeded 20 field workers")

    def test_membership_tiers(self):
        # Existing datasets need not contain a subscriber in every tier.
        customers = list(self.db.customers.find())
        tiers = set(c.get("tier", "Free") for c in customers)
        self.assertTrue(tiers.issubset({"Free", "Plus", "Pro"}))

    def test_mongodb_crud_round_trip(self):
        marker = str(uuid.uuid4())
        collection = self.db.integration_checks
        inserted = collection.insert_one({"marker": marker, "state": "created"})
        self.assertEqual(collection.find_one({"_id": inserted.inserted_id})["state"], "created")
        collection.update_one({"_id": inserted.inserted_id}, {"$set": {"state": "updated"}})
        self.assertEqual(collection.find_one({"_id": inserted.inserted_id})["state"], "updated")
        collection.delete_one({"_id": inserted.inserted_id})
        self.assertIsNone(collection.find_one({"_id": inserted.inserted_id}))

    def test_worker_salary_and_shift(self):
        worker = self.db.users.find_one({"role": "worker", "worker_profile": {"$exists": True}})
        self.assertIsNotNone(worker)
        self.assertIn("monthly_salary", worker["worker_profile"])
        self.assertGreater(worker["worker_profile"]["monthly_salary"], 10000)

    def test_razorpay_order_creation(self):
        order, err = razorpay_service.create_order(500, "REC_TEST_101", {"tier": "Pro"})
        if razorpay_service.is_configured:
            self.assertIsNone(err)
            self.assertIsNotNone(order)
            self.assertEqual(order["amount"], 50000) # 500 INR in paise
        else:
            self.assertIsNone(order)
            self.assertIn("not configured", err)

    def test_payment_page_renders_and_rejects_unsigned_checkout(self):
        user = self.db.users.find_one({"role": "customer", "status": "active"})
        showtime = self.db.showtimes.find_one({"status": "active"}) or self.db.showtimes.find_one()
        with self.client.session_transaction() as user_session:
            user_session["user_id"] = str(user["_id"])
            user_session["role"] = "customer"
            user_session["name"] = user.get("name", "Customer")
        page = self.client.get(f"/booking/{showtime['_id']}/seats")
        self.assertEqual(page.status_code, 200)
        with self.client.session_transaction() as user_session:
            csrf_token = user_session["_csrf_token"]
        before = self.db.bookings.count_documents({"customer_id": user["_id"]})
        response = self.client.post(
            f"/booking/{showtime['_id']}/checkout",
            data={"selected_seats": "A8", "payment_method": "Razorpay Online", "csrf_token": csrf_token},
            follow_redirects=False
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.db.bookings.count_documents({"customer_id": user["_id"]}), before)

    def test_prevent_double_booking(self):
        today = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d")
        showtime = next((show for show in self.db.showtimes.find({
            "status": "active", "show_date": {"$gte": today}
        }) if self.db.theaters.find_one({"_id": show.get("theater_id"), "status": "active"})
            and self.db.screens.find_one({"_id": show.get("screen_id"), "status": "active"})), None)
        if not showtime:
            self.skipTest("No currently available showtime fixture")
        customer = self.db.customers.find_one()
        valid_seats = [f"{row}{number}" for row in "ABCDEF" for number in range(1, 9)]
        unique_seat = next(seat for seat in valid_seats if seat not in showtime.get("booked_seats", []))
        key_one = f"test-pay-{uuid.uuid4()}"
        key_two = f"test-pay-{uuid.uuid4()}"

        booking1 = None
        try:
            booking1, err1 = BookingService.create_booking(customer["user_id"], str(showtime["_id"]), [unique_seat],
                payment_verification={"verified": True, "provider": "test", "idempotency_key": key_one})
            self.assertIsNone(err1)
            self.assertIsNotNone(booking1)
            self.assertEqual(booking1["schema_version"], 3)
            self.assertIn("venue", booking1["show_snapshot"])
            self.assertIn("grand_total", booking1["pricing"])
            self.assertGreater(len(booking1["pricing"]["seats"]), 0)

            booking2, err2 = BookingService.create_booking(customer["user_id"], str(showtime["_id"]), [unique_seat],
                payment_verification={"verified": True, "provider": "test", "idempotency_key": key_two})
            self.assertIsNone(booking2)
            self.assertIsNotNone(err2)
            self.assertIn("just booked", err2)
        finally:
            if booking1:
                self.db.showtimes.update_one({"_id": showtime["_id"]}, {"$pull": {"booked_seats": unique_seat}})
                self.db.bookings.delete_one({"_id": booking1["_id"]})
                self.db.payments.delete_many({"transaction_id": {"$in": [key_one, key_two]}})
                self.db.customers.update_one({"user_id": customer["user_id"]}, {"$inc": {
                    "total_bookings": -1, "total_spent": -booking1["pricing"]["grand_total"]}})

if __name__ == "__main__":
    unittest.main()
