"""One horse in one race, start to finish, as a LangGraph graph.

Task ID = LangGraph thread ID. MongoDB's checkpointer saves the graph's own
state; the ledger (ledger.py) proves what took effect in the outside world.

This first version walks the steps in a fixed order. Build order item 6 swaps
in the model for the hard parts: unknown outcomes, changed systems, triage.
"""
from datetime import datetime, timezone
from typing import Any, TypedDict

from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.graph import END, START, StateGraph

from .. import config, rules
from .. import db as dbm
from ..ledger import Outcome, guarded_write
from . import decider, investigator, writer
from .tools import look_up, send


class State(TypedDict, total=False):
    task_id: str
    status: str


def _task(state: State) -> dict[str, Any]:
    return dbm.db()[dbm.TASKS].find_one({"_id": state["task_id"]})


def _set(task_id: str, **fields) -> None:
    fields["updated_at"] = datetime.now(timezone.utc)
    dbm.db()[dbm.TASKS].update_one({"_id": task_id}, {"$set": fields})


def _left_stable(task: dict[str, Any]) -> bool:
    deadline = task["deadline"]
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= deadline


def _refer(task: dict[str, Any], reason: str) -> State:
    d = dbm.db()
    profile = d[dbm.HORSE_PROFILES].find_one({"horse_id": task["horse_id"]}, {"_id": 0}) or {}
    pack = {"why": reason, "horse": task.get("horse"), "quote": task.get("quote"),
            "owner_value": task.get("stated_value"),
            "agent_valuation": (task.get("quote") or {}).get("valuation"),
            "rating": profile.get("official_rating"), "breeding": profile.get("breeding"),
            "runs": profile.get("runs", []), "health_records": profile.get("health_records", []),
            "declarations": profile.get("declarations", []), "deadline": task["deadline"]}
    try:
        pack.update(_summary(d, task, profile, reason))
    except Exception as e:  # words are never worth blocking a referral for
        pack.update(summary=None, summary_source="none", summary_meta={"error": repr(e)})
    d[dbm.REFERRALS].update_one({"_id": task["_id"]}, {"$set": {
        "task_id": task["_id"], "reason": reason, "decision": None, "pack": pack,
        "created_at": datetime.now(timezone.utc)}}, upsert=True)
    _set(task["_id"], status="referred", lane="referral", referral_reason=reason)
    return {"status": "referred"}


def _summary(d, task: dict[str, Any], profile: dict[str, Any], reason: str) -> dict[str, Any]:
    """The model's one-page summary for the underwriter. Written once: a resume reuses it."""
    old = (d[dbm.REFERRALS].find_one({"_id": task["_id"]}, {"reason": 1, "pack": 1}) or {})
    old_pack = old.get("pack") or {}
    if old.get("reason") == reason and old_pack.get("summary"):
        return {k: old_pack[k] for k in ("summary", "summary_source", "summary_meta") if k in old_pack}
    rule_doc = d[dbm.INSURER_RULES].find_one(sort=[("version", -1)]) or rules.DEFAULT_RULES
    w = writer.summarize_referral(task, profile, reason, rule_doc.get("authority_limit"))
    return {"summary": w["text"], "summary_source": w["source"],
            "summary_meta": {k: w.get(k) for k in ("model", "ms", "at", "fallback_reason", "rejected_text")}}


def _explain(q: dict[str, Any], old: dict[str, Any] | None) -> dict[str, Any]:
    """The model's one-sentence explanation of a price code already computed.

    Written once per set of facts: a resume, or a re-run of this node while the
    owner has not answered yet, reuses the stored sentence instead of calling again.
    """
    facts = writer.price_facts(q)
    old = old or {}
    if old.get("explanation") and old.get("explanation_facts") == facts:
        return {**q, **{k: old[k] for k in ("explanation", "explanation_source", "explanation_meta",
                                             "explanation_facts") if k in old}}
    w = writer.explain_price(q)
    return {**q, "explanation": w["text"], "explanation_source": w["source"], "explanation_facts": facts,
            "explanation_meta": {k: w.get(k) for k in ("model", "ms", "at", "fallback_reason", "rejected_text")}}


def price(state: State) -> State:
    """Build the risk profile's findings and price the horse from the insurer's rules."""
    task = _task(state)
    d = dbm.db()
    if task["race"].get("race_status") != "declared":
        _set(task["_id"], status="not_eligible", reason=f"race is at '{task['race'].get('race_status')}', not declared")
        return {"status": "not_eligible"}
    profile = d[dbm.HORSE_PROFILES].find_one({"horse_id": task["horse_id"]}) or {}
    if not task.get("investigation"):  # once per horse: a resume or a re-run reuses it
        found = investigator.investigate(task, profile)
        _set(task["_id"], investigation=found)
        investigator.log_trail(task["_id"], found)
    rule_doc = d[dbm.INSURER_RULES].find_one(sort=[("version", -1)]) or rules.DEFAULT_RULES
    q = rules.quote(task["race"], task.get("runner", {}), task["ownership_share"], profile,
                    stated_value=task.get("stated_value"), add_ons=task.get("add_ons"), rules=rule_doc)
    if q["offerable"]:
        try:
            q = _explain(q, task.get("quote"))
        except Exception as e:  # words are never worth blocking a horse for
            q = {**q, "explanation": None, "explanation_source": "none", "explanation_meta": {"error": repr(e)}}
    _set(task["_id"], quote=q)
    if not q["offerable"]:
        _set(task["_id"], status="not_eligible", reason=q["reason"])
        return {"status": "not_eligible"}
    if q["over_limit"]:
        return _refer({**task, "quote": q}, "value above the insurer's limit")
    return {"status": "priced"}


def confirm(state: State) -> State:
    """The owner answers once per race. The agent never answers for them."""
    task = _task(state)
    answer = task.get("confirmation")
    if answer is None:
        if _left_stable(task):
            _set(task["_id"], status="not_confirmed")
            return {"status": "not_confirmed"}
        _set(task["_id"], status="awaiting_owner")
        return {"status": "awaiting_owner"}
    profile = dbm.db()[dbm.HORSE_PROFILES].find_one({"horse_id": task["horse_id"]}) or {}
    if not answer["fit"]:
        return _refer(task, "adverse answer from the owner")
    if rules.declaration_contradicts_history(True, profile):
        return _refer(task, "today's answer contradicts a recent declaration")
    _set(task["_id"], lane="clean")
    return {"status": "confirmed"}


def _write(state: State, step: str, system: str, intended: dict[str, Any], next_status: str) -> State:
    task = _task(state)

    def guard() -> str | None:
        if _left_stable(task):
            return "the horse has left the stable"
        if not (task.get("confirmation") or {}).get("fit"):
            return "no fit-to-run confirmation"
        if task["quote"]["over_limit"]:
            return "outside the insurer's limit"
        return None

    def attempt(max_attempts: int = 3) -> dict[str, Any]:
        return guarded_write(task_id=task["_id"], step=step, system=system, intended=intended,
                             guard=guard, call=send(system), read_back=look_up(system), max_attempts=max_attempts)

    record = attempt()
    decisions = 0
    while record["state"] != Outcome.VERIFIED and record["state"] != Outcome.BLOCKED:
        # Outcome not proven. The model decides the next move; code decides if it's allowed.
        fresh = _task(state)
        charge_rec = dbm.db()[dbm.ACTIONS].find_one({"operation_id": f"{task['_id']}:charge"}) or {}
        facts = {
            "horse": task.get("horse"), "step": step, "system": system,
            "ledger_state": str(record.get("state")), "attempts_so_far": record.get("attempts"),
            "evidence": str(record.get("evidence"))[:300],
            "charge_verified": charge_rec.get("state") == Outcome.VERIFIED and step != "charge",
            "horse_has_left": _left_stable(fresh),
            "minutes_until_horse_leaves": int(((fresh["deadline"] if fresh["deadline"].tzinfo else
                                                fresh["deadline"].replace(tzinfo=timezone.utc))
                                               - datetime.now(timezone.utc)).total_seconds() // 60),
            "decisions_so_far": decisions,
        }
        facts["allowed_moves"] = decider.allowed_moves(facts)
        choice = decider.decide(facts)
        decisions += 1
        decider.log(task["_id"], "agent_decision", {"system": system, "step": step, "move": choice["move"],
                                                   "reason": choice["reason"], "source": choice["source"],
                                                   "allowed": facts["allowed_moves"],
                                                   "ledger_state": facts["ledger_state"]}, choice["ms"])
        if choice["move"] == "read_back_again":
            record = attempt(max_attempts=0)
        elif choice["move"] == "resend_same_id":
            record = attempt(max_attempts=1)
        elif choice["move"] == "refund_and_escalate":
            refund = guarded_write(task_id=task["_id"], step="refund", system="refunds",
                                   intended={"task_id": task["_id"], "refund_of": f"{task['_id']}:charge",
                                             "amount": task["quote"]["total"], "currency": task["quote"]["currency"]},
                                   guard=lambda: None, call=send("refunds"), read_back=look_up("refunds"))
            decider.log(task["_id"], "refund", {"system": "refunds", "state": str(refund["state"])})
            return _refer(_task(state), f"agent refunded and escalated: {step} at {system} could not be proven "
                                        f"({choice['reason']})")
        else:  # escalate
            return _refer(_task(state), f"agent escalated: {step} at {system} could not be proven ({choice['reason']})")
    if record["state"] != Outcome.VERIFIED:
        _set(task["_id"], status="blocked", blocked_at=step, blocked_reason=str(record.get("reason") or record.get("state")))
        return {"status": "blocked"}
    _set(task["_id"], status=next_status)
    return {"status": next_status}


def charge(state: State) -> State:
    t = _task(state)
    return _write(state, "charge", "payments",
                  {"task_id": t["_id"], "amount": t["quote"]["total"], "currency": t["quote"]["currency"]}, "charged")


def issue(state: State) -> State:
    t = _task(state)
    return _write(state, "issue", "platform",
                  {"task_id": t["_id"], "horse_id": t["horse_id"], "race_id": t["race"]["race_id"],
                   "sum_insured": t["quote"]["sum_insured"], "premium": t["quote"]["premium"]}, "issued")


def report(state: State) -> State:
    t = _task(state)
    return _write(state, "report", "insurer",
                  {"task_id": t["_id"], "horse_id": t["horse_id"], "race_id": t["race"]["race_id"],
                   "sum_insured": t["quote"]["sum_insured"]}, "reported")


def notify(state: State) -> State:
    t = _task(state)
    return _write(state, "notify", "notify", {"task_id": t["_id"], "owner_id": t["owner_id"], "message": "covered"}, "covered")


def _go(on: str, then: str):
    return lambda state: then if state.get("status") == on else END


def build():
    g = StateGraph(State)
    for name, fn in [("price", price), ("confirm", confirm), ("charge", charge),
                     ("issue", issue), ("report", report), ("notify", notify)]:
        g.add_node(name, fn)
    g.add_edge(START, "price")
    g.add_conditional_edges("price", _go("priced", "confirm"), ["confirm", END])
    g.add_conditional_edges("confirm", _go("confirmed", "charge"), ["charge", END])
    g.add_conditional_edges("charge", _go("charged", "issue"), ["issue", END])
    g.add_conditional_edges("issue", _go("issued", "report"), ["report", END])
    g.add_conditional_edges("report", _go("reported", "notify"), ["notify", END])
    g.add_edge("notify", END)
    return g.compile(checkpointer=MongoDBSaver(dbm.client(), db_name=config.DB_NAME))


def run_task(graph, task_id: str) -> State:
    """Run or resume one task. Safe to call again after a crash."""
    cfg = {"configurable": {"thread_id": task_id}}
    snapshot = graph.get_state(cfg)
    if snapshot.next:  # killed mid-run: carry on from the checkpoint
        return graph.invoke(None, cfg)
    return graph.invoke({"task_id": task_id}, cfg)
