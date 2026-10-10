# CineSphere — Movie Theater Management & Ticket Booking Web Application

Built with **Python Flask**, **MongoDB**, **The Movie Database (TMDB) Real Movie API**, **Pure HTML5 + CSS3**, and **Jinja2**.

---

## 🌟 Highlights
- **Strict Role-Based Access Control (RBAC):** Customer, Staff, Field Worker, and Admin.
- **Theaters & Multi-Screen Topology:** Seeded with Mumbai Central, Andheri, and Thane.
- **Strict Capacity Constraints:**
  - Exactly **5 Staff members** per theater limit.
  - Exactly **20 Field Workers** per theater limit.
- **Real Movie Data via TMDB API:** Real posters, backdrops, cast filmography, ratings, trailers, and directors.
- **Interactive Seat Selection & Booking Engine:** Real-time seat layout, double-booking prevention, breakdown of fees & GST.
- **Verified Customer Reviews:** Only accounts with completed bookings receive the **✓ Verified Booking** badge.
- **Field Worker Maintenance & Attendance:** Task tracking (Cleaning, audio, projection) and check-in/check-out logs.
- **Auditing & Telemetry:** Security audit trail logging all actions with user ID, role, and IP address.

---

## 🔑 Default Seed Demo Accounts

| Role | Email | Password |
|---|---|---|
| **Admin** | `admin@cinesphere.com` | `Admin@1234` |
| **Staff (Mumbai Central)** | `staff1@cinesphere.com` | `Admin@1234` |
| **Worker (Housekeeping)** | `worker1@cinesphere.com` | `Admin@1234` |
| **Customer** | `customer1@cinesphere.com` | `Admin@1234` |

---

## 🛠 Local Setup & Installation

### 1. Clone & Set Up Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment (.env)
```bash
cp .env.example .env
```
Add your TMDB API key to `.env`:
```ini
TMDB_API_KEY=your_key_here
MONGO_URI=mongodb://localhost:27017/
DATABASE_NAME=cinesphere
```

### 3. Start MongoDB
Ensure MongoDB is running locally:
```bash
mongod --dbpath /path/to/data
```
Open **MongoDB Compass** and connect to `mongodb://localhost:27017/` to inspect collections (`theaters`, `movies`, `bookings`, `users`, `tasks`, etc.).

### 4. Seed the Database
```bash
python scripts/seed_database.py
```

### 5. Run the Application
```bash
python run.py
```
Open your browser at `http://127.0.0.1:3000`.

---

## 🧪 Running Automated Tests
```bash
python -m unittest discover tests
```
