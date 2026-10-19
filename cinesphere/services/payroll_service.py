from datetime import UTC, datetime

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from cinesphere.models.database import get_db
from cinesphere.models.worker_model import find_worker


DEFAULT_PAYROLL_POLICY = {
    "pay_basis": "monthly",
    "attendance_rule": "approved_schedule_only",
    "overtime_hourly_multiplier": 1.5,
    "unapproved_absence_daily_deduction": True,
    "notes": "Approved leave, holidays, corrected attendance, and approved shift changes are excluded from absence deductions.",
}


class PayrollService:
    @staticmethod
    def policy():
        saved = get_db().system_config.find_one({"key": "payroll_policy"}) or {}
        return {**DEFAULT_PAYROLL_POLICY, **saved.get("policy", {})}

    @classmethod
    def calculate_draft(cls, worker_id, period, actor_id):
        db = get_db()
        worker = find_worker(worker_id, active_only=True)
        if not worker:
            return None, "Worker not found."
        if not period or len(period) != 7 or period[4] != "-":
            return None, "Use a salary period in YYYY-MM format."
        shifts = list(db.shifts.find({"worker_id": worker["_id"], "date": {"$regex": f"^{period}"},
                                     "status": {"$in": ["Scheduled", "Completed"]}}))
        attendance = db.attendance.count_documents({"worker_id": worker["_id"], "date": {"$regex": f"^{period}"},
            "status": {"$in": ["Present", "Corrected Present"]}})
        approved_exclusions = db.worker_requests.count_documents({"worker_id": worker["_id"],
            "date": {"$regex": f"^{period}"}, "status": "Approved",
            "type": {"$in": ["leave", "shift_change", "attendance_correction"]}})
        eligible = max(0, len(shifts) - approved_exclusions)
        absences = max(0, eligible - attendance)
        monthly_salary = float(worker.get("monthly_salary", 0))
        deduction = round(monthly_salary / eligible * absences, 2) if eligible else 0
        now = datetime.now(UTC).replace(tzinfo=None)
        draft = {"worker_id": worker["_id"], "worker_name": worker["name"], "theater_id": worker["theater_id"],
            "period": period, "pay_basis": "monthly", "base_pay": monthly_salary,
            "scheduled_obligations": len(shifts), "approved_exclusions": approved_exclusions,
            "eligible_obligations": eligible, "attended_obligations": attendance,
            "allowances": 0, "overtime": 0, "deductions": deduction,
            "gross_pay": monthly_salary, "net_pay": round(monthly_salary - deduction, 2),
            "status": "Draft" if eligible else "Needs Review",
            "calculation_note": "No eligible schedule exists; attendance deductions were not applied." if not eligible else "Calculated from approved scheduled obligations.",
            "calculated_by": ObjectId(actor_id), "created_at": now, "updated_at": now}
        try:
            result = db.payroll.insert_one(draft)
            draft["_id"] = result.inserted_id
            return draft, None
        except DuplicateKeyError:
            return None, "A payroll record already exists for this worker and period."
