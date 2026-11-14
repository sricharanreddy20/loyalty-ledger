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
- Its points are pending for 30 days (the return window), then become available.
- A lot expires 180 days after it was earned.

**Returns.**
- Full or partial returns are allowed within 30 days of the order. Each return is a `line_return` record.
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
- Crossing a threshold writes a `tier_change` record. The new rate applies from the next day.
- There are no downgrades in 2026.

**Ledger.**
- Entries are only ever added, never changed.
- Account balance = the sum of its ledger points. `loyalty_account.current_balance` is a stored copy of that sum and must equal it.
- Nothing is dated after 2026-12-31. Pending and available amounts are evaluated as of a given date, which defaults to 2026-12-31.

**Where each rule is enforced.** Database constraints enforce only structure: keys, references, allowed values, signs and ID formats. Business rules are checked by validation SQL (Step 6), so labelled anomalies can still be loaded.
