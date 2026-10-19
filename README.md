# CineSphere

CineSphere is a Flask and MongoDB cinema platform with separate customer, worker, staff, and administrator experiences. It supports real showtime inventory, atomic seat reservations, verified payment callbacks, signed tickets, theater-scoped operations, attendance, payroll review, support, and an evidence-based worker reward program.

## Run locally (Windows PowerShell)

Requirements: Python 3.11+, a running MongoDB server, and an unused development database.

```powershell
py -3.11 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env with a long SECRET_KEY and your MongoDB settings.
.\venv\Scripts\python.exe -m cinesphere.scripts.seed_database
.\venv\Scripts\python.exe -m cinesphere.run
```

Open `http://localhost:3000`. The seed command intentionally refuses to reset a non-empty database unless `CINESPHERE_ALLOW_DEVELOPMENT_RESET=YES` is explicitly set. Never enable that override against production data.

Run the test suite with:

```powershell
.\venv\Scripts\python.exe -m unittest discover -s cinesphere/tests -v
```

## Production configuration

Set at minimum `SECRET_KEY`, `MONGO_URI`, and `DATABASE_NAME`. Provider setup and verification behavior are documented in [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md). Membership and worker benefits are documented in [docs/MEMBERSHIP_POLICY.md](docs/MEMBERSHIP_POLICY.md) and [docs/WORKER_REWARD_POLICY.md](docs/WORKER_REWARD_POLICY.md).

Important operating constraints:

- “Now Showing” is derived only from active, future local showtimes. TMDB discovery data never implies local availability.
- A seat is confirmed only after a verified provider payment or a successfully reserved worker reward credit.
- Cash cancellations enter `Refund Pending`; CineSphere does not claim that a provider refund happened until an external reconciliation records it.
- Payroll approval and “paid” recording are auditable administrative states. This repository does not transfer salary funds.
- Paid membership plans ship as drafts until an administrator supplies pricing and a verified payment workflow.
- The assistant UI reports itself unavailable until a server-side provider integration is configured; API keys are never requested in the browser.

## Deployment

The repository includes a Vercel entrypoint, but deployment is intentionally not performed automatically. Review environment variables, provider callback URLs, MongoDB network access, and production data-retention requirements before an authorized deployment.
