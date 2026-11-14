from datetime import datetime, timedelta, timezone

from raceday import rules

NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)
FLAT_HCAP = {"type": "Flat", "race_name": "Kilbarrow Handicap", "pattern": ""}
CHASE_HCAP = {"type": "Chase", "race_name": "Kilbarrow Handicap Chase", "pattern": ""}
GROUP = {"type": "Flat", "race_name": "Kilbarrow Stakes", "pattern": "Group 3"}


def day(n):
    return (NOW - timedelta(days=n)).date().isoformat()


CALM = {"runs": [{"date": day(40), "finished": True}], "breeding": {"risk_band": "A"}}


def test_default_value_comes_from_race_kind_and_rating_band():
    assert rules.quote(FLAT_HCAP, {"ofr": "82"}, 1.0, CALM, today=NOW)["full_value"] == 60_000
    assert rules.quote(FLAT_HCAP, {"ofr": "40"}, 1.0, CALM, today=NOW)["full_value"] == 8_000
    assert rules.quote(GROUP, {"ofr": ""}, 1.0, CALM, today=NOW)["value_basis"] == "table default"


def test_owner_figure_wins_over_the_default():
    q = rules.quote(FLAT_HCAP, {"ofr": "82"}, 0.5, CALM, stated_value=100_000, today=NOW)
    assert q["value_basis"] == "owner's figure" and q["sum_insured"] == 50_000


def test_jumps_cost_more_than_flat_for_the_same_value():
    flat = rules.quote(FLAT_HCAP, {}, 1.0, CALM, stated_value=50_000, today=NOW)
    chase = rules.quote(CHASE_HCAP, {}, 1.0, CALM, stated_value=50_000, today=NOW)
    assert chase["premium"] == 3 * flat["premium"]


def test_riskier_horse_costs_more_and_says_why():
    busy = {"runs": [{"date": day(n), "finished": n != 4} for n in (26, 19, 12, 8, 4)]}
    a = rules.quote(FLAT_HCAP, {"ofr": "82"}, 1.0, busy, today=NOW)
    b = rules.quote(FLAT_HCAP, {"ofr": "82"}, 1.0, CALM, today=NOW)
    assert a["premium"] > b["premium"]
    assert {f["factor"] for f in a["risk_factors"]} == {"workload", "recent form"}


def test_minimum_premium_and_add_ons_and_tax():
    q = rules.quote(FLAT_HCAP, {"ofr": "40"}, 1.0, CALM, add_ons=["vet_fees"], today=NOW)
    assert q["premium"] == 90.0 and q["add_ons"] == {"vet_fees": 50.0}
    assert q["total"] == round((90 + 50) * 1.05, 2)


def test_pre_existing_condition_is_excluded_not_priced():
    p = {**CALM, "health_records": [{"date": day(400), "kind": "scan", "result": "issue", "pre_existing": True,
                                     "condition": "left fore tendon"}]}
    q = rules.quote(FLAT_HCAP, {"ofr": "82"}, 1.0, p, today=NOW)
    assert q["exclusions"] == ["left fore tendon"] and q["risk_adjustment"] == 1.0


def test_over_the_limit_and_valuation_gap():
    p = {**CALM, "sales": [{"date": day(400), "price": 62_000}], "career_earnings": "41000"}
    q = rules.quote(GROUP, {"ofr": "104"}, 1.0, p, stated_value=450_000, today=NOW)
    assert q["over_limit"] and q["valuation"]["gap_flagged"] and q["valuation"]["estimate"] == 82_000


def test_clean_answer_after_recent_adverse_one_contradicts():
    assert rules.declaration_contradicts_history(True, {"declarations": [{"date": day(14), "fit": False}]}, NOW)
    assert not rules.declaration_contradicts_history(True, {"declarations": []}, NOW)
