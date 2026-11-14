"""The insurer's rules, applied by code. The model never sets a price.

Every number here is invented for the demo.
"""
from datetime import datetime, timedelta, timezone
from typing import Any

DEFAULT_RULES: dict[str, Any] = {
    "version": 1,
    "currency": "GBP",
    # Maximum sum insured by race type and class.
    "sum_insured": {
        "Flat": {"1": 400_000, "2": 200_000, "3": 100_000, "4": 60_000, "5": 30_000, "6": 20_000},
        "Hurdle": {"1": 300_000, "2": 150_000, "3": 80_000, "4": 50_000, "5": 25_000, "6": 15_000},
        "Chase": {"1": 300_000, "2": 150_000, "3": 80_000, "4": 50_000, "5": 25_000, "6": 15_000},
    },
    "base_rate": 0.004,
    "minimum_premium": 25.0,
    "tax_rate": {"GB": 0.12, "IE": 0.05, "default": 0.10},
    "authority_limit": 200_000,  # invented; above this, a human underwriter decides
    # Each risk factor's effect on the price.
    "risk": {
        "runs_last_30_days_over": 3, "workload_loading": 0.20,
        "failed_to_finish_last_run_loading": 0.25,
        "days_since_last_run_over": 200, "long_absence_loading": 0.15,
        "health_issue_last_180_days_loading": 0.30,
        "breeding_band_loading": {"A": 0.0, "B": 0.05, "C": 0.15},
        "low_rating_below": 60, "low_rating_loading": 0.05,
    },
}


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
              if h.get("result") == "issue" and _dt(h["date"]) >= today - timedelta(days=180)]
    if issues:
        out.append({"factor": "health", "evidence": f"{len(issues)} health issue(s) in 180 days", "effect": r["health_issue_last_180_days_loading"]})
    band = (profile.get("breeding") or {}).get("risk_band", "A")
    if r["breeding_band_loading"].get(band, 0):
        out.append({"factor": "breeding", "evidence": f"risk band {band}", "effect": r["breeding_band_loading"][band]})
    rating = _num(profile.get("official_rating"))
    if rating is not None and rating < r["low_rating_below"]:
        out.append({"factor": "official rating", "evidence": f"rated {int(rating)}", "effect": r["low_rating_loading"]})
    return out


def quote(race: dict[str, Any], share: float, owner_country: str, profile: dict[str, Any],
          rules: dict[str, Any] = DEFAULT_RULES, today: datetime | None = None) -> dict[str, Any]:
    """Price one horse in one race. Returns the numbers and the reasons behind them."""
    table = rules["sum_insured"].get(race.get("type", ""), {})
    max_si = table.get(str(race.get("race_class", "")).replace("Class ", ""))
    if max_si is None:
        return {"offerable": False, "reason": "no rate for this race type and class"}
    sum_insured = max_si * share
    factors = risk_factors(profile, rules, today)
    adjustment = 1 + sum(f["effect"] for f in factors)
    base = max(rules["base_rate"] * sum_insured, rules["minimum_premium"])
    premium = round(base * adjustment, 2)
    tax_rate = rules["tax_rate"].get(owner_country, rules["tax_rate"]["default"])
    tax = round(premium * tax_rate, 2)
    return {
        "offerable": True,
        "sum_insured": sum_insured,
        "base_premium": round(base, 2),
        "risk_adjustment": round(adjustment, 2),
        "risk_factors": factors,
        "premium": premium,
        "tax": tax,
        "total": round(premium + tax, 2),
        "currency": rules["currency"],
        "rules_version": rules["version"],
        "over_limit": sum_insured > rules["authority_limit"],
    }


def declaration_contradicts_history(answer_fit: bool, profile: dict[str, Any], today: datetime | None = None) -> bool:
    """A clean answer today that follows a recent adverse one. History only adds caution."""
    today = today or datetime.now(timezone.utc)
    recent_adverse = [d for d in profile.get("declarations", [])
                      if not d.get("fit", True) and _dt(d["date"]) >= today - timedelta(days=30)]
    return answer_fit and bool(recent_adverse)


def _dt(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc) \
        if "T" in str(value) else datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc)


def _num(value) -> float | None:
    """Feeds send numbers as strings, and sometimes send nothing."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
