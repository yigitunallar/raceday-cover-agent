"""The insurer's rules, applied by code. The model never sets a price.

Every number here is invented for the demo. See docs/MOCK_DATA.md.
"""
from datetime import datetime, timedelta, timezone
from typing import Any

JUMPS = {"Hurdle", "Chase", "NH Flat"}

DEFAULT_RULES: dict[str, Any] = {
    "version": 2,
    "currency": "EUR",
    "rate": {"flat": 0.006, "jumps": 0.018},
    # Default value when the owner gives none, by code, race kind and rating band.
    "default_value": {
        "flat": [
            {"kind": "pattern", "value": 90_000},
            {"kind": "handicap", "min_rating": 90, "value": 75_000},
            {"kind": "handicap", "min_rating": 70, "value": 60_000},
            {"kind": "handicap", "min_rating": 55, "value": 40_000},
            {"kind": "maiden_open", "value": 40_000},
            {"kind": "maiden_auction", "value": 20_000},
            {"kind": "handicap", "min_rating": 0, "value": 8_000},
        ],
        "jumps": [
            {"kind": "pattern", "value": 70_000},
            {"kind": "handicap", "min_rating": 135, "value": 55_000},
            {"kind": "handicap", "min_rating": 115, "value": 40_000},
            {"kind": "novice", "value": 20_000},
            {"kind": "handicap", "min_rating": 0, "value": 8_000},
        ],
    },
    "minimum_premium": 90.0,
    "tax_rate": 0.05,
    "add_ons": {
        "life_saving_surgery": {"flat": 30.0, "jumps": 60.0},
        "vet_fees": {"flat": 50.0, "jumps": 90.0},
    },
    "authority_limit": 200_000,
    "cover_hours_max": 48,
    "risk": {
        "runs_last_30_days_over": 3, "workload_loading": 0.20,
        "failed_to_finish_last_run_loading": 0.25,
        "days_since_last_run_over": 200, "long_absence_loading": 0.15,
        "recent_health_issue_days": 60, "recent_health_issue_loading": 0.30,
        "breeding_band_loading": {"A": 0.0, "B": 0.05, "C": 0.15},
        "valuation_gap_ratio": 2.0,  # owner's figure this many times the evidence: flag it
    },
}


def code(race: dict[str, Any]) -> str:
    return "jumps" if race.get("type") in JUMPS else "flat"


def race_kind(race: dict[str, Any]) -> str:
    """Feeds do not say this directly: it comes from the pattern field and the race name."""
    if (race.get("pattern") or "").strip():
        return "pattern"
    name = (race.get("race_name") or "").lower()
    if "handicap" in name:
        return "handicap"
    if "auction" in name or "median" in name:
        return "maiden_auction"
    if code(race) == "jumps" and any(w in name for w in ("maiden", "novice", "bumper")) or race.get("type") == "NH Flat":
        return "novice"
    if "maiden" in name:
        return "maiden_open"
    return "handicap"


def default_value(race: dict[str, Any], rating: float | None, rules: dict[str, Any]) -> int | None:
    kind = race_kind(race)
    for row in rules["default_value"][code(race)]:
        if row["kind"] != kind:
            continue
        if "min_rating" in row and (rating if rating is not None else 0) < row["min_rating"]:
            continue
        return row["value"]
    return None


def estimate_value(profile: dict[str, Any], fallback: int | None) -> dict[str, Any]:
    """The agent's own evidence of what the horse is worth. It checks the owner's figure; it never sets it."""
    evidence = []
    sales = sorted(profile.get("sales", []), key=lambda s: s["date"])
    if sales:
        evidence.append(f"last sold for {sales[-1]['price']:,} on {sales[-1]['date']}")
    earnings = _num(profile.get("career_earnings")) or 0
    if earnings:
        evidence.append(f"career earnings {int(earnings):,}")
    candidates = [s["price"] for s in sales[-1:]] + ([earnings * 2] if earnings else [])
    estimate = max(candidates) if candidates else fallback
    if not candidates and fallback:
        evidence.append("no sales or earnings; using the table default")
    return {"estimate": estimate, "evidence": evidence}


def risk_factors(profile: dict[str, Any], rules: dict[str, Any], today: datetime | None = None) -> list[dict[str, Any]]:
    """What the rules find in a horse's profile. Each factor carries its evidence."""
    today = today or datetime.now(timezone.utc)
    r = rules["risk"]
    out: list[dict[str, Any]] = []
    runs = sorted(profile.get("runs", []), key=lambda x: x["date"])
    recent = [x for x in runs if _dt(x["date"]) >= today - timedelta(days=30)]
    if len(recent) > r["runs_last_30_days_over"]:
        out.append({"factor": "workload", "evidence": f"{len(recent)} runs in 30 days", "effect": r["workload_loading"]})
    if runs:
        last = runs[-1]
        if not last.get("finished", True):
            out.append({"factor": "recent form", "evidence": "failed to finish last time", "effect": r["failed_to_finish_last_run_loading"]})
        gap = (today - _dt(last["date"])).days
        if gap > r["days_since_last_run_over"]:
            out.append({"factor": "long absence", "evidence": f"{gap} days since last run", "effect": r["long_absence_loading"]})
    issues = [h for h in profile.get("health_records", [])
              if h.get("result") == "issue" and not h.get("pre_existing")
              and _dt(h["date"]) >= today - timedelta(days=r["recent_health_issue_days"])]
    if issues:
        out.append({"factor": "health", "evidence": f"{issues[-1]['kind']} issue {_days_ago(issues[-1]['date'], today)} days ago",
                    "effect": r["recent_health_issue_loading"]})
    band = (profile.get("breeding") or {}).get("risk_band", "A")
    if r["breeding_band_loading"].get(band, 0):
        out.append({"factor": "breeding", "evidence": f"breeding risk band {band}", "effect": r["breeding_band_loading"][band]})
    return out


def exclusions(profile: dict[str, Any]) -> list[str]:
    """Pre-existing conditions are never covered. They are listed on the policy, not priced."""
    return [h["condition"] for h in profile.get("health_records", []) if h.get("pre_existing")]


def quote(race: dict[str, Any], runner: dict[str, Any], share: float, profile: dict[str, Any],
          stated_value: float | None = None, add_ons: list[str] | None = None,
          rules: dict[str, Any] = DEFAULT_RULES, today: datetime | None = None) -> dict[str, Any]:
    """Price one horse in one race. Returns the numbers and the reasons behind them."""
    c = code(race)
    rating = _num(runner.get("ofr"))
    table_value = default_value(race, rating, rules)
    if stated_value is None and table_value is None:
        return {"offerable": False, "reason": "no default value for this race kind"}
    full_value = float(stated_value) if stated_value is not None else float(table_value)
    sum_insured = round(full_value * share, 2)
    factors = risk_factors(profile, rules, today)
    adjustment = 1 + sum(f["effect"] for f in factors)
    base = rules["rate"][c] * sum_insured
    premium = round(max(base * adjustment, rules["minimum_premium"]), 2)
    extras = {a: rules["add_ons"][a][c] for a in (add_ons or []) if a in rules["add_ons"]}
    subtotal = round(premium + sum(extras.values()), 2)
    tax = round(subtotal * rules["tax_rate"], 2)
    valuation = estimate_value(profile, table_value)
    gap = (stated_value is not None and valuation["estimate"]
           and stated_value >= rules["risk"]["valuation_gap_ratio"] * valuation["estimate"])
    return {
        "offerable": True,
        "code": c,
        "race_kind": race_kind(race),
        "value_basis": "owner's figure" if stated_value is not None else "table default",
        "full_value": full_value,
        "sum_insured": sum_insured,
        "rate": rules["rate"][c],
        "base_premium": round(base, 2),
        "risk_adjustment": round(adjustment, 2),
        "risk_factors": factors,
        "premium": premium,
        "add_ons": extras,
        "tax": tax,
        "total": round(subtotal + tax, 2),
        "currency": rules["currency"],
        "exclusions": exclusions(profile),
        "valuation": {**valuation, "gap_flagged": bool(gap)},
        "rules_version": rules["version"],
        "over_limit": sum_insured > rules["authority_limit"],
    }


def declaration_contradicts_history(answer_fit: bool, profile: dict[str, Any], today: datetime | None = None) -> bool:
    """A clean answer today that follows a recent adverse one. History only adds caution."""
    today = today or datetime.now(timezone.utc)
    recent_adverse = [d for d in profile.get("declarations", [])
                      if not d.get("fit", True) and _dt(d["date"]) >= today - timedelta(days=30)]
    return answer_fit and bool(recent_adverse)


def _days_ago(value, today: datetime) -> int:
    return (today - _dt(value)).days


def _dt(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value)
    if "T" in s:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _num(value) -> float | None:
    """Feeds send numbers as strings, and sometimes send nothing."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
