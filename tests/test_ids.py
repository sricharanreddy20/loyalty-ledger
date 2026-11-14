import pytest

from core import ids


def test_exact_formats():
    assert ids.customer_id(1) == "CUST-000001"
    assert ids.loyalty_id("CUST-000001") == "LOY-CUST-000001"
    assert ids.order_id(1) == "ORD-2026-000001"
    assert ids.line_id("ORD-2026-000001", 1) == "ORD-2026-000001-01"
    assert ids.pts_id(1) == "PTS-2026-00000001"
    assert ids.rdm_id(1) == "RDM-2026-000001"
    assert ids.exp_id(12) == "EXP-2026-000012"
    assert ids.ret_id(1) == "RET-2026-000001"
    assert ids.tch_id(1) == "TCH-2026-000001"
    assert ids.tier_id("BRONZE") == "TIER-BRONZE"
    assert ids.rule_id(1) == "RULE-0001"
    assert ids.reward_id(1) == "RWD-0001"


def test_upper_boundaries():
    assert ids.customer_id(999_999) == "CUST-999999"
    assert ids.pts_id(99_999_999) == "PTS-2026-99999999"
    assert ids.line_id("ORD-2026-000001", 99) == "ORD-2026-000001-99"


@pytest.mark.parametrize("fn, bad", [
    (ids.customer_id, 0),
    (ids.customer_id, -1),
    (ids.customer_id, 1_000_000),     # would overflow the padding
    (ids.pts_id, 100_000_000),
    (ids.rule_id, 10_000),
    (ids.order_id, "1"),              # not an int
    (ids.order_id, True),             # bool is not a counter
])
def test_bad_counters_rejected(fn, bad):
    with pytest.raises(ValueError):
        fn(bad)


@pytest.mark.parametrize("fn, bad", [
    (ids.loyalty_id, "CUST-1"),
    (ids.line_id, "ORD-2025-000001"),
    (ids.tier_id, "DIAMOND"),
])
def test_bad_parents_rejected(fn, bad):
    with pytest.raises(ValueError):
        fn(bad, 1) if fn is ids.line_id else fn(bad)


def test_patterns_match_formatter_output():
    samples = {
        "customer": ids.customer_id(42),
        "loyalty": ids.loyalty_id(ids.customer_id(42)),
        "order": ids.order_id(42),
        "line": ids.line_id(ids.order_id(42), 3),
        "pts": ids.pts_id(42),
        "rdm": ids.rdm_id(42),
        "exp": ids.exp_id(1),
        "ret": ids.ret_id(7),
        "tch": ids.tch_id(7),
        "tier": ids.tier_id("PLATINUM"),
        "rule": ids.rule_id(42),
        "reward": ids.reward_id(42),
    }
    assert samples.keys() == ids.PATTERNS.keys()
    for kind, value in samples.items():
        assert ids.PATTERNS[kind].fullmatch(value), (kind, value)


def test_patterns_reject_near_misses():
    assert not ids.PATTERNS["order"].fullmatch("ORD-2026-00001")    # too short
    assert not ids.PATTERNS["pts"].fullmatch("PTS-2026-000000001")  # too long
    assert not ids.PATTERNS["line"].fullmatch("ORD-2026-000001-1")
