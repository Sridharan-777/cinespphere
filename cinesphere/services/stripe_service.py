import os

import stripe


class StripeService:
    """Thin Stripe adapter. It never simulates provider responses."""

    def __init__(self):
        self.secret_key = os.getenv("STRIPE_SECRET_KEY", "")
        self.public_key = os.getenv("STRIPE_PUBLIC_KEY", "")
        self.is_configured = bool(self.secret_key.startswith(("sk_test_", "sk_live_")) and self.public_key)
        if self.is_configured:
            stripe.api_key = self.secret_key

    def create_checkout_session(self, amount_inr, order_name, success_url, cancel_url, client_reference_id=None):
        if not self.is_configured:
            return None, "Stripe is not configured. No payment or booking was created."
        try:
            checkout = stripe.checkout.Session.create(
                mode="payment",
                line_items=[{"price_data": {"currency": "inr", "unit_amount": int(amount_inr * 100),
                            "product_data": {"name": order_name}}, "quantity": 1}],
                success_url=success_url,
                cancel_url=cancel_url,
                client_reference_id=client_reference_id,
            )
            return checkout, None
        except Exception as exc:
            return None, f"Stripe checkout could not be created: {exc}"

    def verified_checkout(self, session_id):
        if not self.is_configured or not session_id:
            return None
        try:
            checkout = stripe.checkout.Session.retrieve(session_id)
            return checkout if checkout.payment_status == "paid" else None
        except Exception:
            return None


stripe_service = StripeService()
