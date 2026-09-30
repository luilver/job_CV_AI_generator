# TODO — ideas for later

Not started. Kept here so the ideas are not lost between sessions.

## Payments

- **Remember the PayPal configuration.** Enter the PayPal client id / secret once and keep
  them (config UI that writes to `credentials.json`, or simply documenting one canonical place
  plus a "is it configured?" indicator), instead of refilling the whole setup form.
- **Recurring billing.** Plans are labelled `$5/mo` but checkout creates a one-time
  `intent: CAPTURE` order. Move to the PayPal Subscriptions API (`/v1/billing/subscriptions`)
  so access does not silently stop after the first payment, and add cancel/resume.
- Remember the subscriber's details on the checkout form so they are not retyped.

## Accounts

- Password reset (currently there is no way to recover an account without the password).
- Optional: "unverified accounts" cleanup job that deletes signups never confirmed after 7 days.
- Consider a "change email address" flow, which should require confirming the new address.

## Email

- A `Contact us` / feedback form that reuses the SMTP mailer.
- Optional bounce handling if the app ever sends more than verification mail.

## Product

- Saved job targets so users can compare a CV against several postings over time.
- Export match history to CSV.
