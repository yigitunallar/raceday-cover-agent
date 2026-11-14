"""The model's writing: the owner's price explanation and the underwriter's referral summary.

The core rule holds here too: the model writes, code decides whether it is allowed.
Code builds the facts (every number already computed by rules.py), the model turns
them into words, and code checks the words before they are stored:

- every number the model writes must appear in the facts it was given;
- the text must be short and non-empty.

Anything else (no key, an outage, a timeout, a failed check) falls back to a
template built from the same facts. A model outage never blocks a horse from
being covered or referred: the call has a hard timeout, and after a failure the
model is skipped for a short cool-down so a dead API costs one timeout, not one
per horse.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timezone
from typing import Any, Callable

from .. import config

TIMEOUT_S = 15.0
COOL_DOWN_S = 60.0
EXPLANATION_MAX_WORDS = 40
SUMMARY_MAX_WORDS = 150

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="writer")
_down_until = 0.0  # circuit breaker: skip the model until this monotonic time

# A number as people write it: 1,234.50  450k  20%  -> captured digits plus an optional k/m suffix.
_NUM = re.compile(r"(\d[\d,]*(?:\.\d+)?)([kKmM]\b)?")


# ---------------------------------------------------------------- validation

def numbers_in(text: str) -> set[float]:
    """Every number written in the text, normalised: commas dropped, k/m expanded."""
    out: set[float] = set()
    for m in _NUM.finditer(text or ""):
        raw, suffix = m.group(1).replace(",", ""), m.group(2)
        try:
            n = float(raw)
        except ValueError:
            continue
        if suffix:
            n *= 1_000 if suffix.lower() == "k" else 1_000_000
        out.add(round(n, 4))
    return out


def allowed_numbers(facts: Any) -> set[float]:
    """Every number that appears anywhere in the facts, as values or inside strings."""
    out: set[float] = set()

    def walk(v: Any) -> None:
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            out.add(round(float(v), 4))
        elif isinstance(v, str):
            out.update(numbers_in(v))
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(k)
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
        else:
            out.update(numbers_in(str(v)))

    walk(facts)
    return out


def check(text: str, facts: Any, max_words: int) -> str | None:
    """Why the text cannot be used, or None if it passes."""
    if not text or not text.strip():
        return "empty"
    if len(text.split()) > max_words:
        return f"too long ({len(text.split())} words)"
    stray = numbers_in(text) - allowed_numbers(facts)
    if stray:
        return "numbers not in the facts: " + ", ".join(f"{n:g}" for n in sorted(stray))
    return None


# ---------------------------------------------------------------- facts

def _money(v: float | int | None) -> str:
    if v is None:
        return "none"
    return f"{v:,.2f}" if float(v) != int(v) else f"{int(v):,}"


def _pct(x: float) -> str:
    return f"{round(x * 100, 2):g}%"


def price_facts(q: dict[str, Any]) -> dict[str, Any]:
    """The only facts the model sees when explaining a price. All numbers come from the quote."""
    base_adj = round(q["base_premium"] * q["risk_adjustment"], 2)
    return {
        "currency": q["currency"],
        "total_to_pay": _money(q["total"]),
        "premium": _money(q["premium"]),
        "tax": _money(q["tax"]),
        "code": "jumps" if q["code"] == "jumps" else "Flat",
        "code_rate": _pct(q["rate"]),
        "value_basis": q["value_basis"],
        "full_value": _money(q["full_value"]),
        "sum_insured": _money(q["sum_insured"]),
        "risk_factors": [{"factor": f["factor"], "evidence": f["evidence"], "loading": "+" + _pct(f["effect"])}
                         for f in q.get("risk_factors", [])],
        "minimum_premium_applied": q["premium"] > base_adj + 0.01,
        "exclusions": list(q.get("exclusions", [])),
        "add_ons": {k.replace("_", " "): _money(v) for k, v in (q.get("add_ons") or {}).items()},
    }


def _time_left(deadline: datetime, now: datetime) -> str:
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    mins = int((deadline - now).total_seconds() // 60)
    if mins <= 0:
        return "the horse has already left the stable"
    return f"{mins // 60} hours {mins % 60} minutes"


def _days_ago(date: str, now: datetime) -> int:
    d = datetime.fromisoformat(str(date)[:10]).replace(tzinfo=timezone.utc)
    return (now - d).days


def referral_facts(task: dict[str, Any], profile: dict[str, Any], reason: str,
                   authority_limit: float | None, now: datetime | None = None) -> dict[str, Any]:
    """The only facts the model sees when writing the underwriter's summary."""
    now = now or datetime.now(timezone.utc)
    q = task.get("quote") or {}
    val = q.get("valuation") or {}
    runs = sorted(profile.get("runs", []), key=lambda r: r["date"], reverse=True)[:4]
    return {
        "why_referred": reason,
        "horse": task.get("horse"),
        "race": (task.get("race") or {}).get("race_name"),
        "code": "jumps" if q.get("code") == "jumps" else "Flat",
        "time_left_before_leaving_stable": _time_left(task["deadline"], now),
        "currency": q.get("currency"),
        "owner_stated_value": _money(task.get("stated_value")) if task.get("stated_value") is not None else "none given",
        "value_basis": q.get("value_basis"),
        "sum_insured": _money(q.get("sum_insured")),
        "authority_limit": _money(authority_limit) if authority_limit is not None else "unknown",
        "agent_valuation_estimate": _money(val.get("estimate")) if val.get("estimate") is not None else "none",
        "agent_valuation_evidence": list(val.get("evidence", [])),
        "valuation_gap_flagged": bool(val.get("gap_flagged")),
        "official_rating": profile.get("official_rating") or "none",
        "breeding_risk_band": (profile.get("breeding") or {}).get("risk_band"),
        "recent_runs": [{"days_ago": _days_ago(r["date"], now), "finished": r.get("finished", True),
                         "position": r.get("position")} for r in runs],
        "health_records": [{"days_ago": _days_ago(h["date"], now), "kind": h.get("kind"), "result": h.get("result"),
                            **({"pre_existing_condition": h["condition"]} if h.get("pre_existing") else {})}
                           for h in profile.get("health_records", [])],
        "declarations": [{"days_ago": _days_ago(dcl["date"], now), "fit": dcl.get("fit", True),
                          **({"note": dcl["note"]} if dcl.get("note") else {})}
                         for dcl in profile.get("declarations", [])],
        "risk_factors": [{"factor": f["factor"], "evidence": f["evidence"]} for f in q.get("risk_factors", [])],
        "exclusions": list(q.get("exclusions", [])),
    }


# ---------------------------------------------------------------- templates

def template_explanation(f: dict[str, Any]) -> str:
    c = f["currency"]
    basis = "your figure" if f["value_basis"] == "owner's figure" else "the table default value"
    s = f"{c} {f['total_to_pay']} to cover {c} {f['sum_insured']} on {basis}, at the {f['code']} rate"
    if f["risk_factors"]:
        s += "; priced higher for " + ", ".join(r["evidence"] for r in f["risk_factors"])
    elif f["minimum_premium_applied"]:
        s += "; minimum premium applies"
    if f["exclusions"]:
        s += "; excludes " + ", ".join(f["exclusions"])
    if f["add_ons"]:
        s += "; includes " + ", ".join(f["add_ons"])
    return s + "."


def template_summary(f: dict[str, Any]) -> str:
    c = f["currency"]
    lines = [f"Referred: {f['why_referred']}.",
             f"{f['horse']}, {f['race']} ({f['code']}). Leaves the stable in {f['time_left_before_leaving_stable']}."]
    if f["owner_stated_value"] != "none given":
        lines.append(f"Owner's figure {c} {f['owner_stated_value']}; agent estimate {c} {f['agent_valuation_estimate']}"
                     + (f" ({'; '.join(f['agent_valuation_evidence'])})" if f["agent_valuation_evidence"] else "") + ".")
    lines.append(f"Sum insured {c} {f['sum_insured']}; authority limit {c} {f['authority_limit']}.")
    adverse = [d for d in f["declarations"] if not d["fit"]]
    lines.append("Declarations: " + ("; ".join(f"unfit {d['days_ago']} days ago" + (f" ({d['note']})" if d.get("note") else "")
                                                  for d in adverse) or "all fit") + ".")
    issues = [h for h in f["health_records"] if h["result"] == "issue"]
    lines.append("Health: " + ("; ".join(f"{h['kind']} issue {h['days_ago']} days ago" for h in issues) or "no issues on record") + ".")
    form = f["recent_runs"][0] if f["recent_runs"] else None
    lines.append(f"Rating {f['official_rating']}"
                 + (f"; last ran {form['days_ago']} days ago, " + ("finished " + str(form["position"]) if form["finished"] else "did not finish")
                    if form else "; no runs on record") + ".")
    return "\n".join(lines)


# ---------------------------------------------------------------- model

EXPLAIN_PROMPT = (
    "You explain a horse's raceday insurance price to its owner in ONE plain sentence of at most 30 words (shorter is better). "
    "Use only the facts given. Mention the value basis, the code (Flat or jumps) and each risk factor's evidence; "
    "mention exclusions and add-ons if any. Write numbers exactly as they appear in the facts (no rounding, no 'k'), "
    "and do not compute new numbers. Never suggest a different price. Output the sentence only."
)

SUMMARY_PROMPT = (
    "You write a short summary for an underwriter reviewing a referred raceday insurance case. "
    "Plain text, 5 to 8 short lines, under 110 words, no markdown, no bullets symbols. Cover: why it was referred; "
    "the owner's figure versus the agent's valuation evidence when an owner figure is given; health and declaration "
    "history highlights; official rating and recent form; time left before the horse leaves the stable. "
    "Use only the facts given. Write numbers exactly as they appear in the facts (no rounding, no 'k'), and do not "
    "compute new numbers. Do not recommend a price or a decision. Output the summary only."
)


def _call_model(system: str, facts: dict[str, Any]) -> str:
    import anthropic  # imported lazily: tests need no network or key

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=TIMEOUT_S, max_retries=0)
    # Short, factual writing: no thinking between tools, low effort. Keeps latency near 2 s.
    msg = client.messages.create(
        model=config.MODEL, max_tokens=1000, system=system,
        thinking={"type": "between_tools"}, output_config={"effort": "low"},
        messages=[{"role": "user", "content": "Facts (JSON):\n" + json.dumps(facts, indent=1, default=str)}])
    if msg.stop_reason != "end_turn":
        raise RuntimeError(f"stop_reason {msg.stop_reason}")
    return "".join(b.text for b in msg.content if b.type == "text").strip()


def write(system: str, facts: dict[str, Any], template: Callable[[dict[str, Any]], str], max_words: int,
          call: Callable[[str, dict[str, Any]], str] | None = None) -> dict[str, Any]:
    """Ask the model; check its words; fall back to the template. Never raises."""
    global _down_until
    started = time.monotonic()
    result = {"text": None, "source": "template", "model": config.MODEL, "ms": None, "fallback_reason": None,
              "at": datetime.now(timezone.utc)}
    call = call or _call_model
    if call is _call_model and not config.ANTHROPIC_API_KEY:
        result["fallback_reason"] = "no API key"
    elif call is _call_model and time.monotonic() < _down_until:
        result["fallback_reason"] = "model skipped after a recent failure"
    else:
        try:
            text = _pool.submit(call, system, facts).result(timeout=TIMEOUT_S)
            text = text.strip().strip('"').strip()
            problem = check(text, facts, max_words)
            if problem:
                result["fallback_reason"] = f"failed validation: {problem}"
                result["rejected_text"] = text
            else:
                result.update(text=text, source="model")
        except FutureTimeout:
            result["fallback_reason"] = f"timed out after {TIMEOUT_S:g}s"
            _down_until = time.monotonic() + COOL_DOWN_S
        except Exception as e:  # an outage must never block the horse
            result["fallback_reason"] = f"model error: {type(e).__name__}"
            _down_until = time.monotonic() + COOL_DOWN_S
        result["ms"] = int((time.monotonic() - started) * 1000)
    if result["text"] is None:
        try:
            result["text"] = template(facts)
        except Exception as e:  # the template is ours, but still never block the horse
            result["text"] = "See the quote's figures and reasons."
            result["fallback_reason"] = f"{result['fallback_reason']}; template error: {type(e).__name__}"
    return result


def explain_price(q: dict[str, Any], call=None) -> dict[str, Any]:
    f = price_facts(q)
    return {**write(EXPLAIN_PROMPT, f, template_explanation, EXPLANATION_MAX_WORDS, call), "facts": f}


def summarize_referral(task: dict[str, Any], profile: dict[str, Any], reason: str,
                       authority_limit: float | None, call=None, now: datetime | None = None) -> dict[str, Any]:
    f = referral_facts(task, profile, reason, authority_limit, now)
    return {**write(SUMMARY_PROMPT, f, template_summary, SUMMARY_MAX_WORDS, call), "facts": f}
