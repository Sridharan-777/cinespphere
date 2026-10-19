import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from cinesphere.models.database import get_db


DEFAULT_REWARD_POLICY = {
    "name": "Worker Excellence Rewards",
    "status": "draft",
    "cycle": "monthly",
    "credits_per_award": 3,
    "credit_valid_days": 90,
    "attendance_threshold": 95,
    "maintenance_quality_threshold": 90,
    "attendance_weight": 0.5,
    "quality_weight": 0.5,
    "minimum_verified_tasks": 5,
    "tie_policy": "approve_all_above_threshold_within_configured_award_limit",
    "award_limit": None,
    "eligible_seat_categories": ["Regular"],
    "restore_credit_on_permitted_cancellation": True,
    "notes": "Draft policy. Awards cannot run until an administrator activates it.",
}


class RewardService:
    @staticmethod
    def policy():
        policy = deepcopy(DEFAULT_REWARD_POLICY)
        saved = get_db().system_config.find_one({"key": "worker_reward_policy"}) or {}
        policy.update(saved.get("policy", {}))
        return policy

    @staticmethod
    def cycle_key(moment=None):
        return (moment or datetime.now(UTC).replace(tzinfo=None)).strftime("%Y-%m")

    @classmethod
    def eligibility(cls, worker_id, cycle=None):
        db = get_db()
        worker_oid = ObjectId(worker_id)
        cycle = cycle or cls.cycle_key()
        policy = cls.policy()
        shifts = list(db.shifts.find({"worker_id": worker_oid, "date": {"$regex": f"^{cycle}"},
                                      "status": {"$in": ["Scheduled", "Completed"]}}))
        excluded = db.worker_requests.count_documents({"worker_id": worker_oid, "date": {"$regex": f"^{cycle}"},
            "type": {"$in": ["leave", "shift_change", "attendance_correction"]}, "status": "Approved"})
        eligible_shifts = max(0, len(shifts) - excluded)
        attendance = db.attendance.count_documents({"worker_id": worker_oid, "date": {"$regex": f"^{cycle}"},
                                                     "status": {"$in": ["Present", "Corrected Present"]}})
        attendance_score = round(min(attendance, eligible_shifts) / eligible_shifts * 100, 1) if eligible_shifts else None
        verified_tasks = list(db.tasks.find({"assigned_worker_id": worker_oid, "completed_at": {"$exists": True},
            "quality_review.verified": True, "cycle": cycle}))
        quality_score = round(sum(float(t.get("quality_review", {}).get("score", 0)) for t in verified_tasks) / len(verified_tasks), 1) if verified_tasks else None
        enough_evidence = eligible_shifts > 0 and len(verified_tasks) >= int(policy["minimum_verified_tasks"])
        score = None
        if attendance_score is not None and quality_score is not None:
            score = round(attendance_score * float(policy["attendance_weight"]) + quality_score * float(policy["quality_weight"]), 1)
        eligible = bool(enough_evidence and attendance_score >= float(policy["attendance_threshold"])
                        and quality_score >= float(policy["maintenance_quality_threshold"]))
        reasons = []
        if not eligible_shifts:
            reasons.append("No eligible scheduled shifts were recorded for this cycle.")
        if len(verified_tasks) < int(policy["minimum_verified_tasks"]):
            reasons.append("There is not enough staff-verified maintenance evidence.")
        return {"cycle": cycle, "policy_status": policy["status"], "scheduled_shifts": len(shifts),
                "excluded_obligations": excluded, "eligible_shifts": eligible_shifts,
                "attended_shifts": attendance, "attendance_score": attendance_score,
                "verified_tasks": len(verified_tasks), "quality_score": quality_score,
                "weighted_score": score, "eligible": eligible, "reasons": reasons}

    @classmethod
    def issue_credits(cls, award, approver_id):
        db = get_db()
        policy = cls.policy()
        if policy.get("status") != "active":
            return None, "The reward policy is still a draft and cannot issue credits."
        if str(award.get("worker_id")) == str(approver_id):
            return None, "A worker cannot approve their own award."
        if award.get("status") != "Approved":
            return None, "Only an approved recommendation can issue credits."
        now = datetime.now(UTC).replace(tzinfo=None)
        locked = db.reward_awards.find_one_and_update({"_id": award["_id"], "status": "Approved",
            "issued_at": {"$exists": False}, "issuance_lock": {"$exists": False}},
            {"$set": {"issuance_lock": str(approver_id), "issuance_started_at": now}},
            return_document=ReturnDocument.AFTER)
        if not locked:
            existing = list(db.reward_credits.find({"award_id": award["_id"]}).sort("sequence", 1))
            return ([item["_id"] for item in existing], None) if existing else (None, "Credits are already being issued or were issued.")
        expires_at = now + timedelta(days=int(policy["credit_valid_days"]))
        credit_ids = []
        for number in range(int(policy["credits_per_award"])):
            try:
                result = db.reward_credits.insert_one({"worker_id": award["worker_id"], "award_id": award["_id"],
                    "cycle": award["cycle"], "sequence": number + 1, "status": "available",
                    "eligible_seat_categories": policy["eligible_seat_categories"], "issued_at": now,
                    "expires_at": expires_at, "created_at": now})
                credit_ids.append(result.inserted_id)
            except DuplicateKeyError:
                existing = db.reward_credits.find_one({"award_id": award["_id"], "sequence": number + 1})
                if existing:
                    credit_ids.append(existing["_id"])
        db.reward_awards.update_one({"_id": award["_id"], "issued_at": {"$exists": False}},
            {"$set": {"issued_at": now, "issued_by": ObjectId(approver_id), "credit_ids": credit_ids},
             "$unset": {"issuance_lock": "", "issuance_started_at": ""}})
        return credit_ids, None

    @staticmethod
    def reserve_credit(worker_id, showtime_id):
        db = get_db()
        now = datetime.now(UTC).replace(tzinfo=None)
        db.reward_credits.update_many({"status": "reserved", "reservation_expires_at": {"$lte": now},
            "expires_at": {"$gt": now}}, {"$set": {"status": "available"}, "$unset": {
            "reservation_id": "", "reserved_for_showtime": "", "reserved_at": "", "reservation_expires_at": ""}})
        reservation_id = uuid.uuid4().hex
        credit = db.reward_credits.find_one_and_update({"worker_id": ObjectId(worker_id), "status": "available",
            "expires_at": {"$gt": now}}, {"$set": {"status": "reserved", "reservation_id": reservation_id,
            "reserved_for_showtime": ObjectId(showtime_id), "reserved_at": now,
            "reservation_expires_at": now + timedelta(minutes=10)}}, return_document=ReturnDocument.AFTER)
        return (credit, None) if credit else (None, "No eligible reward admission credit is available.")

    @staticmethod
    def release_credit(credit_id, reservation_id):
        get_db().reward_credits.update_one({"_id": credit_id, "status": "reserved", "reservation_id": reservation_id},
            {"$set": {"status": "available"}, "$unset": {"reservation_id": "", "reserved_for_showtime": "",
             "reserved_at": "", "reservation_expires_at": ""}})

    @staticmethod
    def consume_credit(credit_id, reservation_id, booking_id, employer_funded_value):
        now = datetime.now(UTC).replace(tzinfo=None)
        result = get_db().reward_credits.update_one({"_id": credit_id, "status": "reserved", "reservation_id": reservation_id},
            {"$set": {"status": "consumed", "booking_id": booking_id, "consumed_at": now,
                      "employer_funded_value": employer_funded_value},
             "$unset": {"reservation_expires_at": ""}})
        return result.modified_count == 1
