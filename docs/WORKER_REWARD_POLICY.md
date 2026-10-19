# Worker movie reward policy

The default policy is a draft until an administrator activates it. It awards three personal Regular-seat admission credits to an approved monthly winner. Credits are employer funded; no fake payment record is created.

Eligibility requires:

- an active worker assigned to the theater;
- scheduled shifts for the evaluation cycle;
- attendance evidence after approved leave, shift changes, and corrections are excluded;
- staff-verified quality/task evidence;
- no disqualifying unresolved record configured by policy.

The score combines attendance and verified-quality components using the configured weights and thresholds. Missing schedule or quality evidence is reported as missing, never converted to a perfect score. A staff member may recommend with a written reason, but the recommender cannot approve the same award. Every recommendation, decision, issued credit, reservation, use, expiry, and eligible cancellation restoration is retained in the ledger.

Each credit covers one admission for the worker only. Credits cannot be combined with cash discounts, transferred, or converted to money. Booking temporarily reserves the credit; confirmation consumes it atomically. A failed booking releases the reservation. Expired credits are not restored.
