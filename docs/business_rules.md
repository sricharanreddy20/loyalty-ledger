# Business rules (summary)

The configurable values are in [`core/config.py`](../core/config.py) and are loaded into the `tier` and `rule` tables. Currency is INR. Money is stored to 2 decimals, rounded half-up. Points are whole numbers.

**Tiers.** Earn rates are points per ₹100 of eligible net spend:

| Tier | Qualifying spend from | Earn rate |
|---|---|---|
| Bronze | ₹0 | 1 |
| Silver | ₹25,000 | 1.25 |
| Gold | ₹75,000 | 1.5 |
| Platinum | ₹2,00,000 | 2 |

**Earning.**
- Points are calculated per order line: `floor(eligible_net × rate / 100)`.
- `eligible_net = unit_price × qty − discount`.
- Gift cards earn nothing. Shipping and tax never earn.
- The rate is the account's tier on the order date.
- A line that would earn 0 points gets no ledger entry.

**Lots.**
- Every accrual is a lot.
- Its points are pending through day 30 (the last day a return is allowed) and become available on day 31. A redemption therefore can never use points that a later return would reverse.
- A lot expires 180 days after it was earned.

**Returns.**
- Full or partial returns are allowed up to and including day 30 after the order. Each return is a `line_return` record.
- Points to reverse = original points − `floor(remaining eligible_net × original rate / 100)` − points already reversed.
- Each reversal references the original accrual.
- Points are still pending at return time, so a return never clashes with a redemption.

**Redemptions.**
- The available balance is the remaining points in lots that are available: neither pending nor expired.
- A request is approved only if the reward's cost is no more than the available balance. Otherwise it is rejected with `INSUFFICIENT_POINTS`, and no ledger entry is written.
- Approved redemptions use lots in FIFO order: earliest expiry first, ties broken by `pts_id`.
- Requests with the same timestamp are processed in order of (timestamp, `rdm_id`).

**Expiry.**
- One batch runs on the 1st of each month.
- Each batch writes one EXPIRY entry per lot with `expires_at ≤ run date` and points still remaining.
- A lot is expired at most once, and re-running a batch adds nothing.

**Tier progression.**
- Qualifying spend = eligible net spend − returned eligible net. Gross spend is not used.
- Crossing a threshold (checked at the end of each day, after that day's returns) writes a `tier_change` record. The new rate applies from the next day. An upgrade earned on 31 Dec is recorded with an effective date of 1 Jan 2027.
- There are no downgrades in 2026.

**Ledger.**
- Entries are only ever added, never changed.
- Account balance = the sum of its ledger points. `loyalty_account.current_balance` is a stored copy of that sum and must equal it.
- Nothing is dated after 2026-12-31. Pending and available amounts are evaluated as of a given date, which defaults to 2026-12-31.

**API writes (backend).**
- Every write runs in one transaction and first locks the account row (`SELECT … FOR UPDATE`), so writes for one account happen one at a time. Two simultaneous redemptions cannot both pass the balance check.
- New IDs come from PostgreSQL sequences (`nextval` is atomic). A rolled-back request can leave a gap; IDs are never reused.
- Write timestamps must not rewrite history. They must be on or after both the latest expiry batch date and the account's latest event, and no later than 31 Dec 2026 23:59:59.
- **Tier check timing.** The API checks for a tier upgrade right after each order. The generator checks at the end of each day, after that day's returns. Both write the upgrade effective from the next day, so the outcome is the same: an order never earns at a tier reached that same day.
- **Expiry run dates.** Accepted run dates are the 1st of any 2026 month (an existing date returns `already_run` and changes nothing) or 2027-01-01. The 2027-01-01 batch still gets an ID with the 2026 prefix (`EXP-2026-000013`): the year in IDs identifies the program year of the dataset, not the run date. After that batch, the 2026 write window is closed.

**Where each rule is enforced.** Database constraints enforce only structure: keys, references, allowed values, signs and ID formats. Business rules are checked by validation SQL (Step 6), so labelled anomalies can still be loaded.
