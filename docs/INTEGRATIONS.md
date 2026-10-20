# External integrations

## TMDB

Set `TMDB_API_KEY` on the server, then run:

```powershell
.\venv\Scripts\python.exe -m cinesphere.scripts.sync_movies
```

For titles that discovery misses, pass up to ten exact searches:

```powershell
.\venv\Scripts\python.exe -m cinesphere.scripts.sync_movies "Jailer 2" "Toxic" "Vishwanath & Sons" "Bethlehem Kudumba Unit" "Bha Bha Ba"
```

TMDB supplies catalog metadata only. The importer covers India release pages and Tamil, Telugu, Malayalam, Kannada, and Hindi titles; `TMDB_SYNC_PAGES` controls its page depth. Provider “now playing” lists are tagged as discovery input and do not create CineSphere showtimes. Local availability always requires an active future showtime in MongoDB. `RECENT_RELEASE_DAYS` controls the intended recent-release window (default 120 days); operators should review synchronized records before scheduling them. Preserve TMDB attribution in production UI and comply with its terms.

## Google sign-in

Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. Register this local callback with Google:

`http://localhost:3000/login/google/callback`

Use the corresponding HTTPS origin in production. CineSphere requires Google’s verified-email claim. Google sign-in is customer-only and does not silently attach to an existing password account with the same email.

## Razorpay

Set `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, and `RAZORPAY_WEBHOOK_SECRET`. Configure the webhook to the deployed HTTPS route `/booking/razorpay/webhook` and enable payment captured/failed events. The browser callback signature, provider order, payment status, amount, currency, owner, seats, and internal intent are all verified before confirmation.

Do not treat a browser redirect as proof of payment. Test mode and live mode require their respective keys. Live charges and refunds have not been executed by the automated test suite.

## Stripe

The repository contains a verification adapter, but the primary checkout UI uses Razorpay. Stripe success URLs cannot create a booking without a matching internal payment intent and a provider-confirmed paid session. A complete production Stripe flow still requires intent creation and webhook reconciliation.

## AI assistant

The floating assistant currently provides navigation and reports a clear unavailable state. `AI_PROVIDER_API_KEY` is reserved for a future server-side tool-calling integration. Do not expose provider keys to browser JavaScript. Any future tools must re-check session role and resource ownership on the server and require confirmation for consequential actions.

## Refunds and payroll payouts

Cancellation creates a `Refund Pending` record for eligible cash bookings; an operator or future provider webhook must reconcile the actual refund. Payroll records calculate, approve, and audit payout status but do not move money. Recording “paid” requires the separate `payroll_payout` permission and an external reference.
