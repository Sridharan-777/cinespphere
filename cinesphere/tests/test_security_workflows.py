import unittest
import uuid
from datetime import UTC, datetime, timedelta

from bson import ObjectId

from cinesphere.app import create_app
from cinesphere.models.database import get_db
from cinesphere.services.membership_service import MembershipService
from cinesphere.services.payroll_service import PayrollService
from cinesphere.services.reward_service import RewardService
from cinesphere.services.ticket_service import TicketService
from cinesphere.services.availability_service import list_available_showtimes


class SecurityWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app("development")
        cls.db = get_db(cls.app)

    def setUp(self):
        self.client = self.app.test_client()

    def login_as(self, user):
        with self.client.session_transaction() as browser_session:
            browser_session.clear()
            browser_session["user_id"] = str(user["_id"])
            browser_session["role"] = user["role"]
            browser_session["name"] = user.get("name", "User")
            browser_session["email"] = user.get("email", "")
            if user.get("role") == "worker":
                browser_session["theater_id"] = str(user["worker_profile"]["theater_id"])
            if user.get("role") == "staff":
                staff = self.db.staff.find_one({"user_id": user["_id"]})
                browser_session["theater_id"] = str(staff["theater_id"])

    def csrf(self):
        with self.client.session_transaction() as browser_session:
            return browser_session["_csrf_token"]

    def test_csrf_rejects_unsafe_request(self):
        customer = self.db.users.find_one({"role": "customer", "status": "active"})
        self.login_as(customer)
        response = self.client.post("/customer/support", data={"subject": "x", "message": "y"})
        self.assertEqual(response.status_code, 400)

    def test_expired_paid_membership_falls_back_to_free(self):
        customer = {"tier": "Pro", "membership": {"tier": "PRO", "status": "active",
            "expires_at": datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)}}
        tier, _ = MembershipService.active_membership(customer)
        self.assertEqual(tier, "FREE")
        self.assertEqual(MembershipService.ticket_discount(customer, 1000)["amount"], 0)

    def test_reward_eligibility_handles_missing_schedule(self):
        worker = self.db.users.find_one({"role": "worker", "status": "active"})
        result = RewardService.eligibility(worker["_id"], "2099-01")
        self.assertFalse(result["eligible"])
        self.assertEqual(result["eligible_shifts"], 0)
        self.assertIsNone(result["attendance_score"])
        self.assertTrue(result["reasons"])

    def test_payroll_duplicate_period_is_blocked(self):
        worker = self.db.users.find_one({"role": "worker", "status": "active"})
        actor = self.db.users.find_one({"role": "staff", "status": "active"})
        period = f"2098-{str(uuid.uuid4().int % 12 + 1).zfill(2)}"
        first = None
        try:
            first, error = PayrollService.calculate_draft(worker["_id"], period, actor["_id"])
            self.assertIsNone(error)
            second, error = PayrollService.calculate_draft(worker["_id"], period, actor["_id"])
            self.assertIsNone(second)
            self.assertIn("already exists", error)
        finally:
            if first:
                self.db.payroll.delete_one({"_id": first["_id"]})

    def test_ticket_admission_is_atomic_and_theater_scoped(self):
        booking = self.db.bookings.find_one({"status": "Confirmed"})
        if not booking:
            self.skipTest("No confirmed booking fixture")
        worker_user = self.db.users.find_one({"role": "worker", "status": "active",
            "worker_profile.theater_id": booking["theater_id"]})
        if not worker_user:
            self.skipTest("No worker at booking theater")
        original_duties = worker_user.get("worker_profile", {}).get("authorized_duties")
        original_admission = booking.get("admission")
        try:
            self.db.users.update_one({"_id": worker_user["_id"]}, {"$set": {
                "worker_profile.authorized_duties": ["ticket_validation"]}})
            self.login_as(self.db.users.find_one({"_id": worker_user["_id"]}))
            page = self.client.get("/worker/admission")
            self.assertEqual(page.status_code, 200)
            with self.app.app_context():
                token = TicketService.token_for(booking)
            first = self.client.post("/worker/admission", data={"token": token, "csrf_token": self.csrf()})
            self.assertEqual(first.status_code, 200)
            used = self.db.bookings.find_one({"_id": booking["_id"]})["admission"]
            self.assertEqual(used["validated_by"], worker_user["_id"])
            second = self.client.post("/worker/admission", data={"token": token, "csrf_token": self.csrf()})
            self.assertIn(b"already used", second.data)
        finally:
            if original_duties is None:
                self.db.users.update_one({"_id": worker_user["_id"]}, {"$unset": {"worker_profile.authorized_duties": ""}})
            else:
                self.db.users.update_one({"_id": worker_user["_id"]}, {"$set": {"worker_profile.authorized_duties": original_duties}})
            if original_admission is None:
                self.db.bookings.update_one({"_id": booking["_id"]}, {"$unset": {"admission": ""}})
            else:
                self.db.bookings.update_one({"_id": booking["_id"]}, {"$set": {"admission": original_admission}})

    def test_customer_cannot_open_another_customers_ticket(self):
        booking = self.db.bookings.find_one({"status": "Confirmed"})
        if not booking:
            self.skipTest("No confirmed booking fixture")
        other = self.db.users.find_one({"role": "customer", "status": "active", "_id": {"$ne": booking["customer_id"]}})
        if not other:
            self.skipTest("Insufficient customer booking fixtures")
        self.login_as(other)
        self.assertEqual(self.client.get(f"/booking/ticket/{booking['booking_code']}").status_code, 403)

    def test_staff_customer_directory_is_theater_scoped(self):
        staff_user = self.db.users.find_one({"role": "staff", "status": "active"})
        if not staff_user or not self.db.staff.find_one({"user_id": staff_user["_id"]}):
            self.skipTest("No active staff fixture")
        marker = f"private-{uuid.uuid4()}@example.invalid"
        outsider_id = self.db.users.insert_one({"name": "Out-of-scope customer", "email": marker,
            "role": "customer", "status": "active", "created_at": datetime.now(UTC).replace(tzinfo=None)}).inserted_id
        try:
            self.login_as(staff_user)
            listing = self.client.get("/staff/customers")
            self.assertEqual(listing.status_code, 200)
            self.assertNotIn(marker.encode(), listing.data)
            response = self.client.post(f"/staff/customers/{outsider_id}/edit", data={
                "name": "Changed", "email": marker, "status": "active", "csrf_token": self.csrf()})
            self.assertEqual(response.status_code, 404)
        finally:
            self.db.users.delete_one({"_id": outsider_id})

    def test_staff_can_delete_only_their_own_unlinked_movie(self):
        staff_user = self.db.users.find_one({"role": "staff", "status": "active"})
        if not staff_user:
            self.skipTest("No active staff fixture")
        now = datetime.now(UTC).replace(tzinfo=None)
        owned_id = self.db.movies.insert_one({"title": "Temporary staff movie", "status": "Catalog",
            "created_by_staff": staff_user["_id"], "created_at": now}).inserted_id
        foreign_id = self.db.movies.insert_one({"title": "Administrator movie", "status": "Catalog",
            "created_at": now}).inserted_id
        try:
            self.login_as(staff_user)
            self.assertEqual(self.client.get("/staff/movies").status_code, 200)
            deleted = self.client.post(f"/staff/movies/{owned_id}/delete",
                data={"csrf_token": self.csrf()})
            self.assertEqual(deleted.status_code, 302)
            self.assertIsNone(self.db.movies.find_one({"_id": owned_id}))
            blocked = self.client.post(f"/staff/movies/{foreign_id}/delete",
                data={"csrf_token": self.csrf()})
            self.assertEqual(blocked.status_code, 403)
            self.assertIsNotNone(self.db.movies.find_one({"_id": foreign_id}))
        finally:
            self.db.movies.delete_many({"_id": {"$in": [owned_id, foreign_id]}})

    def test_worker_self_service_records_are_private(self):
        worker = self.db.users.find_one({"role": "worker", "status": "active"})
        other = self.db.users.find_one({"role": "worker", "status": "active", "_id": {"$ne": worker["_id"]}})
        if not worker or not other:
            self.skipTest("Two active worker fixtures are required")

        now = datetime.now(UTC).replace(tzinfo=None)
        target_attendance = self.db.attendance.insert_one({
            "worker_id": worker["_id"], "worker_name": worker["name"],
            "theater_id": worker["worker_profile"]["theater_id"], "date": "2099-12-30",
            "check_in": "TARGET-ATTENDANCE", "check_out": "05:00 PM", "status": "Present",
            "created_at": now,
        }).inserted_id
        other_attendance = self.db.attendance.insert_one({
            "worker_id": other["_id"], "worker_name": other["name"],
            "theater_id": other["worker_profile"]["theater_id"], "date": "2099-12-29",
            "check_in": "OTHER-ATTENDANCE", "check_out": "05:00 PM", "status": "Present",
            "created_at": now,
        }).inserted_id
        target_credit = self.db.reward_credits.insert_one({
            "worker_id": worker["_id"], "award_id": ObjectId(), "sequence": 1,
            "cycle": "TARGET-REWARD", "status": "available", "issued_at": now,
            "expires_at": now + timedelta(days=30), "created_at": now,
        }).inserted_id
        other_credit = self.db.reward_credits.insert_one({
            "worker_id": other["_id"], "award_id": ObjectId(), "sequence": 1,
            "cycle": "OTHER-REWARD", "status": "available", "issued_at": now,
            "expires_at": now + timedelta(days=30), "created_at": now,
        }).inserted_id
        target_payroll = self.db.payroll.insert_one({
            "worker_id": worker["_id"], "worker_name": worker["name"],
            "theater_id": worker["worker_profile"]["theater_id"], "period": "2099-11",
            "base_pay": 77777.77, "allowances": 0, "overtime": 0, "deductions": 0,
            "net_pay": 77777.77, "status": "Approved", "created_at": now,
        }).inserted_id
        other_payroll = self.db.payroll.insert_one({
            "worker_id": other["_id"], "worker_name": other["name"],
            "theater_id": other["worker_profile"]["theater_id"], "period": "2099-12",
            "base_pay": 88888.88, "allowances": 0, "overtime": 0, "deductions": 0,
            "net_pay": 88888.88, "status": "Approved", "created_at": now,
        }).inserted_id

        try:
            self.login_as(worker)
            attendance_page = self.client.get("/worker/attendance")
            self.assertIn(b"TARGET-ATTENDANCE", attendance_page.data)
            self.assertNotIn(b"OTHER-ATTENDANCE", attendance_page.data)

            rewards_page = self.client.get("/worker/rewards")
            self.assertIn(b"TARGET-REWARD", rewards_page.data)
            self.assertNotIn(b"OTHER-REWARD", rewards_page.data)

            payroll_page = self.client.get("/worker/payroll")
            self.assertIn(b"77777.77", payroll_page.data)
            self.assertNotIn(b"88888.88", payroll_page.data)
        finally:
            self.db.attendance.delete_many({"_id": {"$in": [target_attendance, other_attendance]}})
            self.db.reward_credits.delete_many({"_id": {"$in": [target_credit, other_credit]}})
            self.db.payroll.delete_many({"_id": {"$in": [target_payroll, other_payroll]}})

    def test_missing_customer_profile_and_bad_ids_never_raise_500(self):
        temporary_id = self.db.users.insert_one({
            "name": "Profile recovery test", "email": f"recovery-{uuid.uuid4()}@example.invalid",
            "role": "customer", "status": "active", "created_at": datetime.now(UTC).replace(tzinfo=None),
        }).inserted_id
        try:
            self.login_as(self.db.users.find_one({"_id": temporary_id}))
            self.assertEqual(self.client.get("/customer/plans").status_code, 200)

            role_paths = {
                "customer": [("/customer/favorites/toggle/not-an-id", {})],
                "worker": [("/worker/tasks/not-an-id/update-status", {"status": "Completed"})],
                "staff": [
                    ("/staff/workers/not-an-id/attendance", {"action": "check_in"}),
                    ("/staff/workers/not-an-id/edit", {}),
                    ("/staff/workers/not-an-id/delete", {}),
                    ("/staff/rewards/not-an-id/recommend", {}),
                ],
                "admin": [
                    ("/admin/operations/workers/not-an-id", {}),
                    ("/admin/operations/workers/not-an-id/delete", {}),
                    ("/admin/operations/attendance/not-an-id/delete", {"reason": "test"}),
                    ("/admin/operations/tasks/not-an-id", {}),
                    ("/admin/operations/tasks/not-an-id/delete", {}),
                ],
            }
            for role, cases in role_paths.items():
                user = self.db.users.find_one({"role": role, "status": "active"})
                self.login_as(user)
                self.client.get("/")
                for path, data in cases:
                    response = self.client.post(path, data={**data, "csrf_token": self.csrf()})
                    self.assertNotEqual(response.status_code, 500, path)
        finally:
            self.db.users.delete_one({"_id": temporary_id})

    def test_unavailable_theaters_and_full_shows_are_hidden(self):
        movie = self.db.movies.find_one({"status": {"$ne": "Archived"}})
        now = datetime.now(UTC).replace(tzinfo=None)
        inactive_theater = self.db.theaters.insert_one({
            "name": "Unavailable test theater", "city": "Test", "address": "Test",
            "status": "inactive", "created_at": now,
        }).inserted_id
        inactive_screen = self.db.screens.insert_one({
            "theater_id": inactive_theater, "name": "Screen 1", "capacity": 1,
            "status": "active", "created_at": now,
        }).inserted_id
        hidden_show = self.db.showtimes.insert_one({
            "theater_id": inactive_theater, "screen_id": inactive_screen, "movie_id": movie["_id"],
            "show_date": "2099-12-31", "start_time": "10:00 AM", "status": "active",
            "booked_seats": [], "created_at": now,
        }).inserted_id
        active_theater = self.db.theaters.insert_one({
            "name": "Full test theater", "city": "Test", "address": "Test",
            "status": "active", "created_at": now,
        }).inserted_id
        full_screen = self.db.screens.insert_one({
            "theater_id": active_theater, "name": "Screen 1", "capacity": 1,
            "status": "active", "created_at": now,
        }).inserted_id
        full_show = self.db.showtimes.insert_one({
            "theater_id": active_theater, "screen_id": full_screen, "movie_id": movie["_id"],
            "show_date": "2099-12-31", "start_time": "11:00 AM", "status": "active",
            "booked_seats": ["A1"], "created_at": now,
        }).inserted_id
        try:
            visible_ids = {item["_id"] for item in list_available_showtimes(self.db)}
            self.assertNotIn(hidden_show, visible_ids)
            self.assertNotIn(full_show, visible_ids)
            theater_page = self.client.get("/theaters")
            self.assertEqual(theater_page.status_code, 200)
            self.assertNotIn(b"Unavailable test theater", theater_page.data)
            self.assertNotIn(b"Full test theater", theater_page.data)
        finally:
            self.db.showtimes.delete_many({"_id": {"$in": [hidden_show, full_show]}})
            self.db.screens.delete_many({"_id": {"$in": [inactive_screen, full_screen]}})
            self.db.theaters.delete_many({"_id": {"$in": [inactive_theater, active_theater]}})

    def test_admin_movie_staff_and_customer_crud(self):
        admin = self.db.users.find_one({"role": "admin", "status": "active"})
        if not admin:
            self.skipTest("Administrator fixture is required")
        marker = uuid.uuid4().hex[:10]
        theater_id = self.db.theaters.insert_one({
            "name": f"CRUD Theater {marker}", "city": "Test", "address": "Test",
            "status": "active", "created_at": datetime.now(UTC).replace(tzinfo=None),
        }).inserted_id
        theater = self.db.theaters.find_one({"_id": theater_id})
        movie_title = f"CRUD Movie {marker}"
        customer_email = f"crud-customer-{marker}@example.invalid"
        staff_email = f"crud-staff-{marker}@example.invalid"
        movie_id = customer_id = staff_user_id = staff_id = None
        try:
            self.login_as(admin)
            self.client.get("/admin/dashboard")
            created_movie = self.client.post("/admin/movies", data={
                "csrf_token": self.csrf(), "title": movie_title, "language": "Tamil",
                "genres": "Drama, Family", "runtime": "120", "rating": "8.2",
                "status": "Coming Soon", "trailer_url": "https://youtu.be/abcdefghijk",
            })
            self.assertEqual(created_movie.status_code, 302)
            movie = self.db.movies.find_one({"title": movie_title})
            self.assertIsNotNone(movie)
            movie_id = movie["_id"]
            edited_movie = self.client.post(f"/admin/movies/{movie_id}/edit", data={
                "csrf_token": self.csrf(), "title": movie_title + " Updated",
                "original_title": movie_title, "language": "Tamil", "genres": "Action, Drama",
                "runtime": "125", "rating": "8.5", "director": "Test Director",
                "overview": "Updated overview", "release_date": "2099-01-01",
                "trailer_url": "abcdefghijk", "status": "Catalog",
            })
            self.assertEqual(edited_movie.status_code, 302)
            movie = self.db.movies.find_one({"_id": movie_id})
            self.assertEqual(movie["genres"], ["Action", "Drama"])
            self.assertEqual(movie["trailer_url"], "https://www.youtube.com/embed/abcdefghijk")

            self.client.post("/admin/users/create", data={
                "csrf_token": self.csrf(), "name": "CRUD Customer", "email": customer_email,
                "phone": "9000000001", "password": "Customer@123", "role": "customer",
            })
            customer = self.db.users.find_one({"email": customer_email})
            self.assertIsNotNone(customer)
            customer_id = customer["_id"]
            self.client.post(f"/admin/users/{customer_id}/edit", data={
                "csrf_token": self.csrf(), "name": "CRUD Customer Updated",
                "email": customer_email, "phone": "9000000002", "status": "active",
            })
            self.assertEqual(self.db.users.find_one({"_id": customer_id})["phone"], "9000000002")

            self.client.post("/admin/staff", data={
                "csrf_token": self.csrf(), "name": "CRUD Staff", "email": staff_email,
                "phone": "9000000003", "password": "Staff@1234",
                "theater_id": str(theater["_id"]), "designation": "Operations Tester",
            })
            staff_user = self.db.users.find_one({"email": staff_email})
            self.assertIsNotNone(staff_user)
            staff_user_id = staff_user["_id"]
            staff_doc = self.db.staff.find_one({"user_id": staff_user_id})
            staff_id = staff_doc["_id"]
            self.client.post(f"/admin/staff/{staff_id}/edit", data={
                "csrf_token": self.csrf(), "name": "CRUD Staff Updated", "email": staff_email,
                "phone": "9000000004", "designation": "Operations Lead",
                "theater_id": str(theater["_id"]),
            })
            self.assertEqual(self.db.staff.find_one({"_id": staff_id})["designation"], "Operations Lead")

            self.client.post(f"/admin/movies/{movie_id}/delete", data={"csrf_token": self.csrf()})
            self.assertIsNone(self.db.movies.find_one({"_id": movie_id})); movie_id = None
            self.client.post(f"/admin/users/{customer_id}/delete", data={"csrf_token": self.csrf()})
            self.assertIsNone(self.db.users.find_one({"_id": customer_id})); customer_id = None
            self.client.post(f"/admin/staff/{staff_id}/delete", data={"csrf_token": self.csrf()})
            self.assertIsNone(self.db.staff.find_one({"_id": staff_id})); staff_id = staff_user_id = None
        finally:
            if movie_id: self.db.movies.delete_one({"_id": movie_id})
            if customer_id:
                self.db.customers.delete_many({"user_id": customer_id}); self.db.users.delete_one({"_id": customer_id})
            if staff_id: self.db.staff.delete_one({"_id": staff_id})
            if staff_user_id: self.db.users.delete_one({"_id": staff_user_id})
            self.db.theaters.delete_one({"_id": theater_id})


if __name__ == "__main__":
    unittest.main()
