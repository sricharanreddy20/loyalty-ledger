"""Pydantic request and response models. Money is Decimal (serialised as a string), points are int.
Timestamps are local and must not carry a timezone (the dataset has no time zones)."""
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, NaiveDatetime

from core.config import CATEGORIES

Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
Price = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
Category = Literal[CATEGORIES]


# ---------------------------------------------------------------- requests

class OrderLineIn(BaseModel):
    sku: str = Field(min_length=1, max_length=40)
    category: Category
    unit_price: Price
    qty: int = Field(gt=0, le=1000)
    discount: Money = Decimal("0")


class OrderIn(BaseModel):
    loy_id: str
    order_ts: NaiveDatetime
    channel: Literal["STORE", "ONLINE"]
    shipping_amount: Money = Decimal("0")
    tax_amount: Money = Decimal("0")
    lines: list[OrderLineIn] = Field(min_length=1, max_length=99)


class ReturnIn(BaseModel):
    line_no: int = Field(ge=1, le=99)
    qty: int = Field(gt=0)
    return_ts: NaiveDatetime


class RedemptionIn(BaseModel):
    loy_id: str
    reward_id: str
    requested_at: NaiveDatetime


# ---------------------------------------------------------------- responses

class AccountSummary(BaseModel):
    loy_id: str
    customer_id: str
    name: str
    email: str
    city: str
    tier_id: str
    current_balance: int


class AccountDetail(AccountSummary):
    opened_at: date
    as_of: date
    ledger_balance: int          # sum of the ledger: the source of truth
    balance_matches: bool        # stored current_balance == ledger_balance
    available: int
    pending: int
    expired_awaiting_batch: int  # past expires_at, not yet processed by a batch
    qualifying_spend: Decimal


class LedgerEntry(BaseModel):
    pts_id: str
    txn_type: str
    points: int
    txn_ts: datetime
    line_id: str | None
    eligible_net: Decimal | None
    earn_rate: Decimal | None
    available_from: date | None
    expires_at: date | None
    return_id: str | None
    reverses_pts_id: str | None
    rdm_id: str | None
    exp_id: str | None
    expires_pts_id: str | None


class LotOut(BaseModel):
    lot_pts_id: str
    earned_at: datetime
    available_from: date
    expires_at: date
    original: int
    reversed: int
    redeemed: int
    expired: int
    remaining: int
    status: str


class TierOut(BaseModel):
    tier_id: str
    code: str
    rank: int
    min_qualifying_spend: Decimal
    earn_rate: Decimal


class RuleOut(BaseModel):
    rule_id: str
    rule_type: str
    tier_id: str | None
    category: str | None
    value: Decimal | None
    unit: str | None
    description: str
    effective_from: date
    effective_to: date | None


class RewardOut(BaseModel):
    reward_id: str
    name: str
    points_cost: int
    active: bool


class TierChangeOut(BaseModel):
    tier_change_id: str
    old_tier_id: str
    new_tier_id: str
    effective_date: date
    qualifying_spend_at_change: Decimal


class OrderLineResult(BaseModel):
    line_id: str
    eligible_net: Decimal
    points: int
    pts_id: str | None


class OrderResult(BaseModel):
    ord_id: str
    loy_id: str
    tier_id_used: str
    earn_rate: Decimal
    lines: list[OrderLineResult]
    points_total: int
    qualifying_spend: Decimal
    tier_change: TierChangeOut | None


class ReturnResult(BaseModel):
    return_id: str
    line_id: str
    qty_returned: int
    refund_amount: Decimal
    points_reversed: int
    reversal_pts_id: str | None
    reverses_pts_id: str | None
    qualifying_spend: Decimal


class LotUsed(BaseModel):
    lot_pts_id: str
    points_used: int


class RedemptionResult(BaseModel):
    rdm_id: str
    status: Literal["APPROVED", "REJECTED"]
    reject_reason: str | None
    points_cost: int
    available_before: int
    pts_id: str | None
    lots_used: list[LotUsed]


class ExpiryResult(BaseModel):
    exp_id: str
    run_date: date
    already_run: bool
    lots_expired: int
    points_expired: int


class Health(BaseModel):
    status: str
    database: str


# ---------------------------------------------------------------- evaluation

class ValidationTotals(BaseModel):
    checks: int
    PASS: int
    EXPECTED_FAIL: int
    FAIL: int


class ValidationCheck(BaseModel):
    id: str
    category: str
    description: str
    status: Literal["PASS", "EXPECTED_FAIL", "FAIL"]
    violations: int
    explained: int
    unexplained: int
    explained_by: list[str]
    ground_truth_count: int | None
    sample: list[str]


class ValidationSummary(BaseModel):
    totals: ValidationTotals
    checks: list[ValidationCheck]


class Metrics(BaseModel):
    ground_truth: int
    flagged: int
    tp: int
    fp: int
    fn: int
    precision: float | None
    recall: float | None
    f1: float | None


class TypeMetrics(Metrics):
    issue_type: str


class Flagged(BaseModel):
    issue_type: str
    record_id: str
    loy_id: str
    reason: str


class GroundTruth(BaseModel):
    record_id: str
    entity: str
    issue_type: str
    description: str
    rule_violated: str
    loy_id: str | None           # the account the labelled record belongs to


class AnomalyReport(BaseModel):
    detector: str
    metrics: list[TypeMetrics]
    overall: Metrics
    false_positives: list[Flagged]
    false_negatives: list[GroundTruth]
    flagged: list[Flagged]
    ground_truth: list[GroundTruth]


# ---------------------------------------------------------------- dashboard

class TierCount(BaseModel):
    tier_id: str
    code: str
    accounts: int
    share: float                 # fraction of all accounts, 0..1


class Stats(BaseModel):
    customers: int
    accounts: int
    orders: int
    order_lines: int
    ledger_rows: int
    points_outstanding: int      # sum of the whole ledger (the source of truth)
    redemptions_approved: int
    redemptions_rejected: int
    tier_mix: list[TierCount]
