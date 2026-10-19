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


if __name__ == "__main__":
    unittest.main()
