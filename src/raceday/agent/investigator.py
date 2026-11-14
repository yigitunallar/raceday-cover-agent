"""Before pricing, the model investigates each horse the way an underwriter would.

It starts with only the basics and a set of READ-ONLY tools. It chooses which to
call, reads each result, and decides what to check next, saying why each time.
When it judges it has enough, it calls ready_to_price. Code then checks that the
evidence the rules require was actually looked at; if not, it refuses and says
what is missing. The tools cannot change data, move money or set a price, and
the price itself is still computed by rules.quote() from the same profile.
"""
import json
from datetime import datetime, timezone
from typing import Any, Callable

from .. import config
from .. import db as dbm
from .. import rules

MAX_TURNS = 8
TIMEOUT_S = 20


def _days_ago(date: str, now: datetime) -> int:
    return (now - rules._dt(date)).days


def _tools_impl(task: dict[str, Any], profile: dict[str, Any]) -> dict[str, Callable[[], Any]]:
    now = datetime.now(timezone.utc)
    return {
        "get_race_history": lambda: {
            "runs": [{"days_ago": _days_ago(r["date"], now), "finished": r.get("finished", True),
                      "position": r.get("position")} for r in sorted(profile.get("runs", []), key=lambda r: r["date"],
                                                                         reverse=True)],
            "official_rating": profile.get("official_rating") or "none"},
        "get_health_records": lambda: {"owner_provided_history": task.get("owner_medical_history"), "records": [
            {"days_ago": _days_ago(h["date"], now), "kind": h.get("kind"), "result": h.get("result"),
             **({"pre_existing_condition": h["condition"]} if h.get("pre_existing") else {})}
            for h in profile.get("health_records", [])]},
        "get_past_declarations": lambda: [
            {"days_ago": _days_ago(d["date"], now), "fit_to_run": d.get("fit", True), "note": d.get("note")}
            for d in profile.get("declarations", [])],
        "get_sales_and_earnings": lambda: {
            "sales": [{"days_ago": _days_ago(s["date"], now), "price": s["price"]} for s in profile.get("sales", [])],
            "career_earnings": profile.get("career_earnings")},
        "get_breeding": lambda: profile.get("breeding") or {},
    }


TOOL_SPECS = [
    {"name": "get_race_history", "description": "Recent runs (days ago, finished or not, position) and official rating."},
    {"name": "get_health_records", "description": "Vet exams, x-rays, scans and known conditions from the health data provider."},
    {"name": "get_past_declarations", "description": "The owner's past fit-to-run answers, with any notes."},
    {"name": "get_sales_and_earnings", "description": "Past sale prices and career earnings: evidence of what the horse is worth."},
    {"name": "get_breeding", "description": "Sire, dam and breeding risk band."},
]
TOOLS = [{**t, "input_schema": {"type": "object", "properties": {
    "why": {"type": "string", "description": "One short sentence: why you are checking this now."}},
    "required": ["why"]}} for t in TOOL_SPECS] + [{
    "name": "ready_to_price",
    "description": "Call when you have enough evidence to price this horse. Code will check nothing required is missing.",
    "input_schema": {"type": "object", "properties": {
        "summary": {"type": "string", "description": "One or two sentences: what you found that matters for pricing."}},
        "required": ["summary"]}}]

SYSTEM = (
    "You are an underwriting assistant investigating one racehorse before it is priced for raceday mortality cover. "
    "Time is short before the horse leaves the stable, so be economical: check ONE thing at a time, and only what the "
    "basics or an earlier finding give you a specific reason to check. Start from the most telling evidence, then "
    "follow up anything unusual: a long gap or a failed finish leads to health records; a health issue leads to past "
    "declarations; an owner's value above what the rating suggests leads to sales and earnings. Skip checks that cannot "
    "change anything for this horse. Every tool call needs a short 'why' that refers to what you have seen. When you "
    "have enough, call ready_to_price. You never set a price."
)


def required_checks(task: dict[str, Any]) -> set[str]:
    """What the insurer's rules need to have been looked at. Enforced by code, not trusted to the model."""
    need = {"get_race_history", "get_past_declarations"}  # always: recent form and the owner's history
    if task.get("owner_medical_history"):
        need.add("get_health_records")
    if task.get("stated_value") is not None:
        need.add("get_sales_and_earnings")  # an owner's own figure must be checked against evidence
    return need


def _short(result: Any) -> str:
    s = json.dumps(result, default=str)
    return s if len(s) <= 160 else s[:157] + "..."


def investigate(task: dict[str, Any], profile: dict[str, Any], client_factory=None) -> dict[str, Any]:
    """Run the loop. Returns {trail, summary, source}. Never raises; falls back to a fixed checklist."""
    impl = _tools_impl(task, profile)
    need = required_checks(task)
    trail: list[dict[str, Any]] = []
    basics = {"horse": task.get("horse"), "race": task["race"].get("race_name"), "race_type": task["race"].get("type"),
              "pattern": task["race"].get("pattern") or "none", "official_rating": task.get("runner", {}).get("ofr") or "none",
              "owner_share": task.get("ownership_share"),
              "owner_stated_value": task.get("stated_value") if task.get("stated_value") is not None else "none (table default applies)"}
    basics["insurer_requires_before_pricing"] = sorted(need)
    started = datetime.now(timezone.utc)
    try:
        if not config.ANTHROPIC_API_KEY and client_factory is None:
            raise RuntimeError("no API key")
        import anthropic
        client = (client_factory or (lambda: anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY,
                                                                timeout=TIMEOUT_S, max_retries=0)))()
        messages: list[dict[str, Any]] = [{"role": "user", "content": "Basics (JSON):\n" + json.dumps(basics)}]
        for _ in range(MAX_TURNS):
            msg = client.messages.create(model=config.MODEL, max_tokens=1200, system=SYSTEM, tools=TOOLS,
                                         thinking={"type": "between_tools"}, output_config={"effort": "low"},
                                         tool_choice={"type": "auto", "disable_parallel_tool_use": True},
                                         messages=messages)
            messages.append({"role": "assistant", "content": msg.content})
            uses = [b for b in msg.content if b.type == "tool_use"]
            if not uses:
                messages.append({"role": "user", "content": "Use a tool, or call ready_to_price."})
                continue
            results, done = [], None
            for u in uses:
                if u.name == "ready_to_price":
                    missing = sorted(need - {t["tool"] for t in trail})
                    if missing:  # code refuses: the rules need these looked at
                        trail.append({"tool": "ready_to_price", "why": "", "finding": f"refused by code: still need {missing}"})
                        results.append({"type": "tool_result", "tool_use_id": u.id, "is_error": True,
                                        "content": f"Not yet: the insurer's rules require you to check {missing}."})
                    else:
                        done = str(u.input.get("summary", ""))[:400]
                        results.append({"type": "tool_result", "tool_use_id": u.id, "content": "Accepted."})
                elif u.name in impl:
                    out = impl[u.name]()
                    trail.append({"tool": u.name, "why": str(u.input.get("why", ""))[:200], "finding": _short(out)})
                    _log_step(task, trail[-1])  # saved the moment it happens, so the page shows it live
                    results.append({"type": "tool_result", "tool_use_id": u.id, "content": json.dumps(out, default=str)})
                else:
                    results.append({"type": "tool_result", "tool_use_id": u.id, "is_error": True, "content": "Unknown tool."})
            messages.append({"role": "user", "content": results})
            if done is not None:
                return {"trail": trail, "summary": done, "source": "model",
                        "ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000)}
        raise RuntimeError("no decision within the turn limit")
    except Exception as e:  # noqa: BLE001 - investigation must never strand a horse
        for name in sorted(need - {t["tool"] for t in trail}):
            trail.append({"tool": name, "why": "required check (fallback)", "finding": _short(impl[name]())})
        return {"trail": trail, "summary": "Required checks completed by code.", "source": "template",
                "fallback_reason": repr(e)[:200], "ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000)}


def _log_step(task: dict[str, Any], step: dict[str, Any]) -> None:
    try:
        dbm.db()[dbm.TIMELINE].insert_one({"task_id": task["_id"], "at": datetime.now(timezone.utc), "kind": "investigate",
                                           "detail": {"system": "investigation", **step}, "duration_ms": None, "live": True})
    except Exception:  # noqa: BLE001 - logging must never break the investigation
        pass


def log_trail(task_id: str, result: dict[str, Any]) -> None:
    if result.get("source") == "model":
        return  # each step was already saved live by _log_step
    rows = [{"task_id": task_id, "at": datetime.now(timezone.utc), "kind": "investigate",
             "detail": {"system": "investigation", "tool": t["tool"], "why": t["why"], "finding": t["finding"]},
             "duration_ms": None} for t in result["trail"]]
    if rows:
        dbm.db()[dbm.TIMELINE].insert_many(rows)
