"""The investigation claim: one thread wins, a second is refused, a stale (crashed) claim is taken over."""
from datetime import datetime, timedelta, timezone

from raceday import db as dbm
from raceday.agent import graph


class _Tasks:
    def __init__(self, doc):
        self.doc = doc

    def find_one_and_update(self, flt, update):
        d = self.doc
        if d["_id"] != flt["_id"] or d.get("investigation") is not None:
            return None
        since = d.get("investigating_since")
        ok = False
        for alt in flt["$or"]:
            cond = alt["investigating_since"]
            if cond == {"$exists": False} and "investigating_since" not in d:
                ok = True
            elif cond is None and since is None and "investigating_since" in d:
                ok = True
            elif isinstance(cond, dict) and "$lt" in cond and since is not None and since < cond["$lt"]:
                ok = True
        if not ok:
            return None
        before = dict(d)
        d.update(update["$set"])
        return before


def test_claim_is_exclusive_and_stale_claims_expire(monkeypatch):
    tasks = _Tasks({"_id": "t1", "investigation": None})
    monkeypatch.setattr(dbm, "db", lambda: {dbm.TASKS: tasks})
    assert graph.claim_investigation("t1") is True
    assert graph.claim_investigation("t1") is False  # second thread skips
    tasks.doc["investigating_since"] = datetime.now(timezone.utc) - timedelta(seconds=graph.CLAIM_SECONDS + 5)
    assert graph.claim_investigation("t1") is True  # killed agent's claim expired: resume works
    tasks.doc["investigation"] = {"done": True}
    tasks.doc.pop("investigating_since")
    assert graph.claim_investigation("t1") is False  # already investigated
