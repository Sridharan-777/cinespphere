# Membership policy

Membership benefits come from the `membership_policy` system configuration and are calculated on the server. The defaults are deliberately conservative:

| Plan | Initial state | Discount | Convenience-fee reduction |
| --- | --- | ---: | ---: |
| Free | Active | 0% | 0% |
| Plus | Draft | 5% | 25% |
| Pro | Draft | 10% | 50% |

Paid plans must remain unavailable until the business defines a real price, duration, renewal/cancellation terms, and a verified payment activation flow. A plan label in a browser or request body never grants benefits. Benefits apply only when the stored membership is `active` and has not expired.

Discounts are capped by the configured percentage and are calculated from the authoritative showtime price. Taxes and unavoidable charges must be disclosed before provider checkout. Membership policy changes should be treated as commercial changes and reviewed before publication.
