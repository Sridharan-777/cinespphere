# CineSphere

CineSphere is a Flask and MongoDB movie-theater management and ticket-booking application with customer, worker, staff, and administrator portals.

## Run locally

Requirements: Python 3.11+ and MongoDB.

1. Create and activate a virtual environment.
2. Install dependencies with `pip install -r requirements.txt`.
3. Copy `.env.example` to `.env` and configure the required values.
4. Seed the database with `python -m cinesphere.scripts.seed_database` when using a new database.
5. Start the app with `python -m cinesphere.run`.

The local application listens on `http://localhost:3000` by default. Set `PORT` to use another port.

## Deploy to Vercel

The repository includes a Python serverless entrypoint and Vercel routing configuration. Configure these Production environment variables before deploying:

- `SECRET_KEY` - a long, randomly generated Flask session secret
- `MONGO_URI` - a MongoDB Atlas connection string accessible from Vercel
- `DATABASE_NAME` - defaults to `cinesphere`

Optional integrations use `TMDB_API_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `STRIPE_PUBLIC_KEY`, and `STRIPE_SECRET_KEY`.

After linking the intended Vercel project, deploy with `vercel --prod`.
