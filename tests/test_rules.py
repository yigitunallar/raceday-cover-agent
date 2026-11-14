from datetime import datetime, timedelta, timezone

from raceday import rules

NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)
RACE = {"type": "Flat", "race_class": "4"}


def day(n):
    return (NOW - timedelta(days=n)).date().isoformat()


def test_clean_horse_gets_base_price():
    q = rules.quote(RACE, 1.0, "GB", {"runs": [{"date": day(40), "finished": True}], "official_rating": "80"}, today=NOW)
    assert q["offerable"] and q["risk_adjustment"] == 1.0 and q["risk_factors"] == []
    assert q["sum_insured"] == 60_000 and not q["over_limit"]


def test_riskier_horse_costs_more_and_says_why():
    busy = {"runs": [{"date": day(n), "finished": n != 4} for n in (26, 19, 12, 8, 4)], "official_rating": "80"}
    calm = {"runs": [{"date": day(40), "finished": True}], "official_rating": "80"}
    a, b = rules.quote(RACE, 1.0, "GB", busy, today=NOW), rules.quote(RACE, 1.0, "GB", calm, today=NOW)
    assert a["premium"] > b["premium"]
    assert {f["factor"] for f in a["risk_factors"]} == {"workload", "recent form"}


def test_value_over_the_limit_is_flagged():
    assert rules.quote({"type": "Flat", "race_class": "1"}, 1.0, "GB", {}, today=NOW)["over_limit"]
    assert not rules.quote({"type": "Flat", "race_class": "1"}, 0.5, "GB", {}, today=NOW)["over_limit"]


def test_clean_answer_after_recent_adverse_one_contradicts():
    profile = {"declarations": [{"date": day(14), "fit": False}]}
    assert rules.declaration_contradicts_history(True, profile, NOW)
    assert not rules.declaration_contradicts_history(True, {"declarations": []}, NOW)
