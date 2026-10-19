from copy import deepcopy
from datetime import UTC, datetime

from cinesphere.models.database import get_db


DEFAULT_PLANS = {
    "FREE": {
        "name": "Free", "price": None, "currency": "INR", "status": "active",
        "ticket_discount_percent": 0, "ticket_discount_cap": 0,
        "reward_multiplier": 1.0, "food_discount_percent": 0,
        "benefits": ["Standard booking access", "Favorites and booking history"],
    },
    "PLUS": {
        "name": "Plus", "price": None, "currency": "INR", "status": "draft",
        "ticket_discount_percent": 10, "ticket_discount_cap": 150,
        "reward_multiplier": 1.25, "food_discount_percent": 5,
        "benefits": ["Capped ticket savings", "Reward multiplier", "Selected food offers"],
    },
    "PRO": {
        "name": "Pro", "price": None, "currency": "INR", "status": "draft",
        "ticket_discount_percent": 15, "ticket_discount_cap": 250,
        "reward_multiplier": 1.5, "food_discount_percent": 10,
        "benefits": ["Stronger capped savings", "Priority support", "Selected food offers"],
    },
}


class MembershipService:
    @staticmethod
    def plans():
        doc = get_db().system_config.find_one({"key": "membership_plans"})
        configured = deepcopy(DEFAULT_PLANS)
        for key, value in (doc or {}).get("plans", {}).items():
            if key in configured and isinstance(value, dict):
                configured[key].update(value)
        return configured

    @staticmethod
    def active_membership(customer):
        if not customer:
            return "FREE", None
        membership = customer.get("membership") or {}
        tier = str(membership.get("tier") or customer.get("tier") or "FREE").upper()
        if tier not in DEFAULT_PLANS:
            return "FREE", None
        if tier == "FREE":
            return tier, membership
        now = datetime.now(UTC).replace(tzinfo=None)
        expires_at = membership.get("expires_at")
        if membership.get("status") != "active" or (expires_at and expires_at <= now):
            return "FREE", membership
        return tier, membership

    @classmethod
    def ticket_discount(cls, customer, subtotal):
        tier, membership = cls.active_membership(customer)
        plan = cls.plans()[tier]
        percent = max(0, min(float(plan.get("ticket_discount_percent", 0)), 100))
        cap = max(0, float(plan.get("ticket_discount_cap", 0)))
        discount = round(subtotal * percent / 100)
        if cap:
            discount = min(discount, round(cap))
        return {
            "tier": tier, "amount": discount, "percent": percent, "cap": cap,
            "label": f"{plan['name']} membership" if discount else None,
            "membership_id": str((membership or {}).get("payment_reference", "")) or None,
        }

