import os
import hmac
import hashlib
from datetime import datetime

class RazorpayService:
    def __init__(self, key_id=None, key_secret=None):
        self.key_id = key_id or os.getenv("RAZORPAY_KEY_ID", "")
        self.key_secret = key_secret or os.getenv("RAZORPAY_KEY_SECRET", "")
        self.webhook_secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
        self.client = None
        self.is_configured = bool(self.key_id.startswith(("rzp_test_", "rzp_live_")) and self.key_secret)
        try:
            if not self.is_configured:
                return
            import razorpay
            self.client = razorpay.Client(auth=(self.key_id, self.key_secret))
        except Exception as e:
            print(f"Razorpay Client initialization notice: {e}")

    def create_order(self, amount_in_inr, receipt_id, notes=None):
        amount_in_paise = int(amount_in_inr * 100)
        order_payload = {
            "amount": amount_in_paise,
            "currency": "INR",
            "receipt": receipt_id,
            "payment_capture": 1,
            "notes": notes or {}
        }
        
        if not self.is_configured:
            return None, "Razorpay is not configured. Add RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET."

        if self.client:
            try:
                order = self.client.order.create(data=order_payload)
                return order, None
            except Exception as e:
                return None, f"Razorpay order could not be created: {e}"
        return None, "Razorpay client is unavailable."

    def verify_payment_signature(self, razorpay_order_id, razorpay_payment_id, razorpay_signature):
        if not self.client or not razorpay_signature:
            return False
        
        if self.client:
            try:
                self.client.utility.verify_payment_signature({
                    "razorpay_order_id": razorpay_order_id,
                    "razorpay_payment_id": razorpay_payment_id,
                    "razorpay_signature": razorpay_signature
                })
                return True
            except Exception:
                return False
        return False

    def fetch_verified_payment(self, payment_id):
        """Return a captured/authorized provider payment or None."""
        if not self.client or not payment_id:
            return None
        try:
            payment = self.client.payment.fetch(payment_id)
            return payment if payment.get("status") in {"captured", "authorized"} else None
        except Exception:
            return None

    def verify_webhook_signature(self, payload, signature):
        if not self.webhook_secret or not signature:
            return False
        expected = hmac.new(self.webhook_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

razorpay_service = RazorpayService()
