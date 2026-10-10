from datetime import UTC, datetime, date
from bson import ObjectId
from cinesphere.models.database import get_db
from cinesphere.models.worker_model import count_workers

class ReportService:
    @staticmethod
    def get_theater_stats(theater_id):
        db = get_db()
        t_id = ObjectId(theater_id)
        
        # Today's date string YYYY-MM-DD
        today_str = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d")
        
        # Revenue and bookings count
        bookings = list(db.bookings.find({"theater_id": t_id}))
        total_revenue = sum(b.get("pricing", {}).get("grand_total", 0) for b in bookings if b.get("status") == "Confirmed")
        total_bookings = len(bookings)
        
        today_bookings = [b for b in bookings if b.get("show_date") == today_str and b.get("status") == "Confirmed"]
        today_revenue = sum(b.get("pricing", {}).get("grand_total", 0) for b in today_bookings)
        
        screens_count = db.screens.count_documents({"theater_id": t_id, "status": "active"})
        staff_count = db.staff.count_documents({"theater_id": t_id, "status": "active"})
        worker_count = count_workers(t_id, active_only=True)
        pending_tasks = db.tasks.count_documents({"theater_id": t_id, "status": {"$in": ["Pending", "In Progress", "Assigned"]}})

        # Calculate Average Ticket Value (ATV)
        avg_ticket_value = round(total_revenue / total_bookings) if total_bookings > 0 else 0

        # Calculate Auditorium Occupancy Rate
        total_possible_seats = max(1, screens_count * 48 * max(1, len(db.showtimes.distinct("show_date", {"theater_id": t_id}))))
        total_booked_seats = sum(len(b.get("seats", [])) for b in bookings if b.get("status") == "Confirmed")
        occupancy_rate = min(100, round((total_booked_seats / total_possible_seats) * 100, 1)) if total_possible_seats > 0 else 0

        return {
            "total_revenue": total_revenue,
            "total_bookings": total_bookings,
            "today_revenue": today_revenue,
            "today_bookings": len(today_bookings),
            "screens_count": screens_count,
            "staff_count": staff_count,
            "worker_count": worker_count,
            "pending_tasks": pending_tasks,
            "avg_ticket_value": avg_ticket_value,
            "occupancy_rate": occupancy_rate
        }

    @staticmethod
    def get_global_admin_stats():
        db = get_db()
        today_str = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d")
        
        bookings = list(db.bookings.find())
        total_revenue = sum(b.get("pricing", {}).get("grand_total", 0) for b in bookings if b.get("status") == "Confirmed")
        today_bookings = [b for b in bookings if b.get("show_date") == today_str and b.get("status") == "Confirmed"]
        today_revenue = sum(b.get("pricing", {}).get("grand_total", 0) for b in today_bookings)

        total_theaters = db.theaters.count_documents({"status": "active"})
        total_screens = db.screens.count_documents({"status": "active"})
        total_movies = db.movies.count_documents({})
        total_users = db.users.count_documents({"status": "active"})
        total_customers = db.customers.count_documents({"status": "active"})
        total_staff = db.staff.count_documents({"status": "active"})
        total_workers = count_workers(active_only=True)

        return {
            "total_revenue": total_revenue,
            "today_revenue": today_revenue,
            "total_bookings": len(bookings),
            "today_bookings": len(today_bookings),
            "total_theaters": total_theaters,
            "total_screens": total_screens,
            "total_movies": total_movies,
            "total_users": total_users,
            "total_customers": total_customers,
            "total_staff": total_staff,
            "total_workers": total_workers
        }
