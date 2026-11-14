# Loyalty Points Ledger: Synthetic Data Generator

## What this project is
A reproducible Python generator for a loyalty-points dataset: customers → loyalty accounts → tiers and rules → orders and order lines → points ledger (accrual, reversal, redemption, expiry). It follows documented business rules, injects labelled anomalies into a separate ground-truth file, validates itself, exports CSV/JSON/SQL, and proves same-seed reproducibility.

The brief is in `brief/` (git-ignored). NEVER commit it, copy it into other files, or quote it in the README. Paraphrase in your own words.

## Working rules (hackathon, 7 hours, owner must explain every line)
- ONE step of the build plan at a time. Never start the next step unless asked.
- Before coding a step: give a short plan (what, why), then wait for "go".
- After coding: run tests, give a 5-line walkthrough, then STOP. Suggest a commit message. Do not commit yourself.
- Simple readable code, standard library first. Ask before adding any dependency.
- Never invent counts or metrics. README numbers come only from real runs.
- Develop on `--scale starter`; run `--scale full` only at the end.
- Keep it fast: full scale must generate in under ~2 minutes.

## Architecture (REQUIRED: 3 tiers, kept separate)
```
frontend/   -> static HTML + JS (no build step), served by nginx. Talks ONLY to the backend over HTTP (fetch).
backend/    -> FastAPI. The only tier that talks to the database. Pydantic request/response models.
db/         -> PostgreSQL 16. schema.sql (tables, PK, FK, CHECK constraints, indexes).
generator/  -> Python CLI that generates the synthetic dataset, writes CSV/JSON/SQL exports, and loads them into Postgres.
core/       -> pure business-rule functions (no DB, no I/O) shared by generator AND backend:
               points_for(eligible_net, rate), reversal_points(...), fifo_consume(lots, cost),
               lots_to_expire(lots, run_date), tier_for(qualifying_spend). Unit-tested.
docker-compose.yml -> services: db, backend, frontend. `docker compose up` runs everything.
```
- If Docker is not available, run Postgres locally (Homebrew) and start backend/frontend by hand; document it.
- DATABASE_URL comes from environment (.env, git-ignored; provide .env.example).

## Stack
Python 3.11+, standard library for the generator (`random.Random(seed)`, `decimal.Decimal`, `dataclasses`, `csv`, `json`, `hashlib`, `argparse`), FastAPI + uvicorn, psycopg (v3) with plain SQL (no ORM), PostgreSQL 16, pytest, plain HTML/CSS/JS frontend, Docker Compose. Ask before adding anything else.

## Backend API (FastAPI, auto docs at /docs)
- GET  /health
- GET  /accounts?search=&tier=&limit=      list accounts
- GET  /accounts/{loy_id}                  balance (ledger sum), available, pending, tier, qualifying spend
- GET  /accounts/{loy_id}/ledger           ledger entries in time order
- GET  /accounts/{loy_id}/lots             accrual lots with remaining / status / expires_at
- POST /orders                             create order + lines -> ACCRUAL entries (per line)
- POST /orders/{ord_id}/returns            full/partial return -> REVERSAL entries referencing the original accrual
- POST /redemptions                        validate against available balance; FIFO lots. Concurrency: lock the account row with SELECT ... FOR UPDATE inside one transaction, so two simultaneous requests cannot both succeed. Rejected -> 409 with reason.
- POST /expiry-batches/run?run_date=       idempotent: running the same date twice expires nothing new
- GET  /tiers, /rules, /rewards
- GET  /validation                         validation summary (all checks, pass/fail counts)
- GET  /anomalies                          ground truth + detector results + precision/recall/F1
Every write runs in a single DB transaction. Errors return clear JSON (400/404/409), never stack traces.

## Frontend (one page, simple, clean)
- Dashboard: totals (customers, orders, ledger rows, points outstanding), validation pass/fail, anomaly counts.
- Account lookup: balance / available / pending / tier, lots table, ledger table.
- Actions: redeem a reward, return an order line, run an expiry batch -> show the API result or the rejection reason.
- Anomalies view: ground truth vs detector, metrics per issue type.

## Determinism rules
- One `random.Random(seed)` created in the CLI and passed to every generator function. No global random, no `datetime.now()`, no uuid4, no set-order-dependent logic.
- Default seed 20261022. Dataset covers calendar year 2026 (fixed dates).
- Money as `Decimal`, 2 decimals, ROUND_HALF_UP. Points are integers.
- `--scale starter` (~5%) and `--scale full` use the same code paths.

## ID formats (exact)
CUST-000001 (customer) · LOY-<customer-id> (e.g. LOY-CUST-000001) · ORD-2026-000001 · line id ORD-2026-000001-01 · PTS-2026-00000001 · RDM-2026-000001 · EXP-2026-000001 · RET-2026-000001 (return) · TCH-2026-000001 (tier change) · TIER-BRONZE etc · RULE-0001 · RWD-0001.
Zero-padded, immutable, identical across all files.

## Business rules (base currency INR)
### Tiers (Tier table, configurable)
| Tier | Qualifying spend in 2026 (cumulative) | Earn rate |
|---|---|---|
| Bronze | ₹0 | 1 point per ₹100 eligible net spend |
| Silver | ₹25,000 | 1.25 per ₹100 |
| Gold | ₹75,000 | 1.5 per ₹100 |
| Platinum | ₹2,00,000 | 2 per ₹100 |

### Rules (Rule table, with effective_from / effective_to)
- Earn rate per tier (above).
- Category exclusions: gift cards are not eligible; shipping and tax are never eligible.
- Point lot expiry: 180 days after the accrual date.
- Return window: 30 days (a return on day 30 after the order is allowed). Points stay PENDING through day 30 and become AVAILABLE on day 31 (earned + 31 days), so no redemption can consume points a return may still reverse.

### Eligible net spend and accrual
- Per order line: eligible net = (unit_price × qty − discount), only for eligible categories.
- Accrual is per order line: points = floor(eligible_net × rate / 100), using the tier at the order date.
- Each accrual is a "lot" with earned_at, available_from (+31 days), expires_at (+180 days).

### Returns and reversals
- Full refund or partial return (some qty of a line), only within 30 days.
- Reversal points = original_points − floor(remaining_eligible_net × rate / 100), using the ORIGINAL rate.
- Every REVERSAL entry references the original ACCRUAL `pts_id` (`reverses_pts_id`).
- Because points are pending until the day after the return window closes, returns never conflict with redemptions.

### Redemptions
- Reward catalogue (Reward table) with points_cost.
- Available balance = AVAILABLE lots' remaining points (not pending, not expired).
- Approved only if points_cost ≤ available balance. Otherwise REJECTED with reason INSUFFICIENT_POINTS (exception path, no ledger entry).
- Redemptions consume lots FIFO (oldest expiry first). Store which lots were consumed (bridge table redemption_lot).
- Concurrent attempts: some accounts get 2 requests with the same timestamp whose sum exceeds the balance. Process in order (timestamp, rdm_id): the first is approved, the second rejected.

### Expiry
- Monthly ExpiryBatch runs (EXP-2026-000001 … 000012) on the 1st of each month.
- Each batch expires every lot with expires_at ≤ run date and remaining > 0, writing one EXPIRY entry per lot (references lot `pts_id`).
- Idempotent: a lot can be expired at most once. Re-running a batch adds nothing.

### Tier progression
- Qualifying spend = eligible net spend minus returned amounts (NOT gross spend).
- When cumulative qualifying spend crosses a threshold, write a TierChange event (account, old tier, new tier, effective date, qualifying spend at the time). Upgrades apply to orders after that date. No downgrades within 2026.

### Ledger and balances
- PointsTransaction types: ACCRUAL (+), REVERSAL (−), REDEMPTION (−), EXPIRY (−). Signed integer points.
- History is never overwritten; every state change is a new record.
- Account balance = sum of ledger points. LoyaltyAccount stores a `current_balance` snapshot computed from the ledger (this is what the anomaly corrupts).

## Entities (PK / FK)
customer, tier, rule, loyalty_account (FK customer, tier), order (FK account), order_line (FK order), points_transaction (FK account; optional FK order_line, reverses_pts_id, redemption, expiry_batch), reward, redemption (FK account, reward), redemption_lot (bridge: redemption ↔ accrual lot), expiry_batch, tier_change (FK account, tiers).

## Volumes (full scale, approximate, tune lines per order to land near 300k ledger rows)
20,000 customers, 100,000 orders, ~300,000 points transactions. Include refunds, partial returns, expiries, tier changes and concurrent redemption attempts.

## Anomalies (inject AFTER a fully valid baseline; label each in ground truth)
1. BALANCE_MISMATCH: account current_balance ≠ ledger sum.
2. REDEMPTION_EXCEEDS_BALANCE: an APPROVED redemption whose points exceed the available balance at that time.
3. RETURN_KEEPS_POINTS: returned line with no reversal entry.
4. DOUBLE_EXPIRY: the same lot expired in two batches.
5. TIER_USES_GROSS_SPEND: a tier change that only happens if gross (not qualifying) spend is used.
Mutate only the chosen records. Ground truth (record_id, entity, issue_type, description, rule_violated) lives in separate files, never as input columns.

## Validation (writes validation_summary.json/.md, and is also exposed via GET /validation, run as SQL against the DB)
PK uniqueness, FK integrity, allowed enums, date order, arithmetic reconciliation, lifecycle transitions, duplicates. Plus the four acceptance checks:
- Balance equals ledger sum.
- Every reversal references an existing original accrual.
- Expiry is idempotent (no lot expired twice; re-running a batch adds nothing).
- Tier status follows the configured thresholds using qualifying spend.

## Build plan (one step at a time)
- Step 0: repo layout (frontend/ backend/ db/ generator/ core/), .gitignore (brief/, .venv, __pycache__, output/full, .env), docker-compose.yml with Postgres running, .env.example, config (seed, scale, tiers, rules), ids.py + tests.
- Step 1: db/schema.sql (all tables, PK/FK/CHECK, indexes) + docs/erd.md (Mermaid, cardinality, lifecycle states) + docs/business_rules.md (short).
- Step 2: core/ rule functions + unit tests (points, reversal, FIFO, expiry, tier).
- Step 3: generator: master data, orders + lines, accrual, tier changes, returns/reversals, redemptions (incl. concurrent + rejected), monthly expiry. Starter scale only.
- Step 4: anomaly injection + ground truth; exports CSV/JSON/.sql; load into Postgres (COPY); manifest with row counts + SHA-256; reproducibility check.
- Step 5: backend API (read endpoints first, then writes: redemption with FOR UPDATE, return, expiry run) + API tests.
- Step 6: validation SQL checks + GET /validation; detector + metrics + GET /anomalies.
- Step 7: frontend page.
- Step 8: full-scale run, docs/data_dictionary.md, README (architecture diagram, one command to run, seed, design decisions, assumptions, limitations, real metrics, AI usage).
CHECKPOINT: by ~17:00 the DB is loaded and at least the read API works. If behind, cut the detector first, then frontend polish.

## Current status
- Step 0 done: layout, .gitignore, .env.example, docker-compose (db only, host port 5433 because a local Postgres uses 5432), core/config.py, generator/config.py, core/ids.py + tests. Local venv uses the installed python3 (3.14); backend image will be python:3.12-slim.
- Step 1 done: db/schema.sql (13 tables + eval.ground_truth, structural constraints only; business rules left to validation), lot_status(as_of date DEFAULT '2026-12-31') SQL function, db/tests/smoke_test.sql (rolled-back, passing), docs/erd.md, docs/business_rules.md. Added RET/TCH IDs. Compose mounts only db/schema.sql into initdb (recreate volume with `docker compose down -v` after schema changes, or re-run schema.sql, which is re-runnable).
- Step 2 done: core/rules.py (eligible_net, points_for, lot_dates, reversal_points with cumulative partial returns and proportional discount split, fifo_consume + InsufficientPoints, lots_to_expire, tier_for), tests/test_rules.py. 72 tests passing. Floats rejected for money/rates.
- Step 3 done: generator (master.py, plan.py, simulate.py, generate.py, `python -m generator --scale starter`). One date-ordered pass: expiry batch -> tier upgrades due -> events by (ts, order<return<redemption, key) -> end-of-day tier check. Lots available from earned + 31 days (day after the return window). RDM ids issued at processing time; 80% of requests with nothing affordable are dropped; concurrent pairs from the top 20% most active accounts. Upgrade earned on 31 Dec is effective 2027-01-01. tests/test_generator.py replays the ledger to prove no lot ever goes negative. Starter: 15,107 ledger rows (~302k projected at full), 0.24s. 104 tests passing.
- Step 4 done: generator/anomalies.py (5 types x anomalies_per_type: starter 10, full 50; one account per anomaly; candidates restricted so nothing else changes; balances recomputed before BALANCE_MISMATCH; new rows take next ids, deleted reversals leave gaps), export.py (csv/json/sql + ground_truth.csv/json + manifest.json without timestamps), load.py (schema reset + COPY, one transaction, counts checked; psycopg[binary]==3.3.9 added), `python -m generator --scale starter --load --check-reproducible` (2 subprocesses, different PYTHONHASHSEED: all 30 files identical). Starter export is 17.69 MB, so only output/starter/csv, ground_truth.* and manifest.json are committed (json/ and sql/ ignored). DB load test runs only with RUN_DB_TESTS=1 (it resets the DB). 125 tests passing.
- Next: Step 5 (backend API).
