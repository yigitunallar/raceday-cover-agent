"""Invented demo data: one race day at an invented Irish course. See docs/MOCK_DATA.md.

Every horse, owner, course, sale and number here is made up. The shapes copy
real racing feeds, including their traps: numbers as strings, a display-only
off_time, and race_status stages.

    python -m raceday.seed
"""
from datetime import datetime, timedelta, timezone

from . import db as dbm
from . import rules

COURSE = "Kilbarrow"


def _race(race_id, name, rtype, pattern, hours, status="declared", now=None):
    off = (now + timedelta(hours=hours)).replace(second=0, microsecond=0)
    h12 = off.hour % 12 or 12
    return {
        "race_id": race_id, "date": off.date().isoformat(), "off_dt": off,
        "off_time": f"{h12}:{off.minute:02d}",  # display only: 12-hour, no am/pm
        "course": COURSE, "region": "IRE", "race_name": f"{COURSE} {name}", "type": rtype,
        "race_class": "", "pattern": pattern, "field_size": "0", "is_abandoned": False,
        "race_status": status, "runners": [], "updatedAt": now,
    }


# horse_id, name, race_id, sex, age, rating, owner, share, stated value, add-ons, story
RUNNERS = [
    ("hrs_d01", "Copper Lantern", "rac_k2", "Gelding", 5, 82, "own_1", 1.0, None, [], "clean"),
    ("hrs_d02", "Tin Whistle", "rac_k2", "Mare", 4, 84, "own_2", 1.0, None, [], "busy"),
    ("hrs_d03", "Northern Ledger", "rac_k1", "Colt", 3, 104, "own_3", 1.0, 450_000, ["vet_fees"], "overvalued"),
    ("hrs_d04", "Velvet Anchor", "rac_k4", "Gelding", 8, 128, "own_4", 1.0, None, [], "contradiction"),
    ("hrs_d05", "Salt Meadow", "rac_k5", "Gelding", 6, None, "own_1", 0.5, None, ["life_saving_surgery"], "pre_existing"),
    ("hrs_d06", "Paper Comet", "rac_k3", "Filly", 3, None, "own_2", 1.0, None, [], "no_answer"),
    ("hrs_d07", "Second Draft", "rac_k6", "Gelding", 7, 118, "own_3", 1.0, None, [], "entry_stage"),
    ("hrs_d08", "Harbour Clerk", "rac_k2", "Gelding", 6, 76, "own_4", 1.0, None, [], "duplicate_name"),
    ("hrs_d09", "Harbour Clerk", "rac_k5", "Gelding", 5, None, "own_1", 1.0, None, [], "duplicate_name"),
    ("hrs_d10", "Winter Almanac", "rac_k3", "Colt", 3, None, "own_3", 0.5, None, [], "clean"),
    ("hrs_d11", "Plain Sailing", "rac_k4", "Gelding", 9, 131, "own_2", 1.0, None, ["vet_fees"], "clean"),
    ("hrs_d12", "Maple Verdict", "rac_k1", "Filly", 3, 98, "own_4", 1.0, None, [], "clean"),
    ("hrs_d13", "Quiet Harbour", "rac_k4", "Mare", 7, 122, "own_1", 1.0, None, [], "long_absence"),
]

OWNERS = {"own_1": "IE", "own_2": "IE", "own_3": "GB", "own_4": "IE"}

# How each invented owner likes to be reached. The agent reads this before deciding
# whether to call, remind in the app, or wait. Quiet hours are also enforced in code.
OWNER_CONTACT = {
    "own_1": {"prefers": "phone", "notes": "Wants a phone call as soon as cover is priced; rarely opens the app."},
    "own_2": {"prefers": "app", "notes": "Answers app reminders within minutes. Do not call unless the horse is about to leave."},
    "own_3": {"prefers": "phone", "quiet_until": "16:00", "notes": "Trainer is at the gallops: no calls before 4 PM Toronto time."},
    "own_4": {"prefers": "phone", "notes": "Happy with calls, but already confirmed by phone for two horses this morning."},
}


def _profile(i, horse_id, name, rating, story, now):
    day = lambda n: (now - timedelta(days=n)).date().isoformat()
    p = {
        "horse_id": horse_id, "horse": name,
        "official_rating": str(rating) if rating is not None else "",  # strings, sometimes empty
        "breeding": {"sire": f"Sire {chr(65 + i)}", "dam": f"Dam {chr(80 - i)}", "risk_band": "AAB"[i % 3]},
        "runs": [{"date": day(35 + i * 3), "race_id": f"rac_hist_{i}a", "finished": True, "position": str(2 + i % 5)},
                 {"date": day(70 + i * 4), "race_id": f"rac_hist_{i}b", "finished": True, "position": str(1 + i % 6)}],
        "career_earnings": str(8_000 + i * 3_500),
        "sales": [{"date": day(500 + i * 40), "venue": "Invented Bloodstock Sale", "price": 15_000 + i * 4_000}],
        "health_records": [{"date": day(120 + i), "kind": "vet exam", "result": "cleared"}],
        "declarations": [{"date": day(35 + i * 3), "race_id": f"rac_hist_{i}a", "fit": True, "given_by": "owner"}],
        "updatedAt": now,
    }
    if story == "busy":  # same race as Copper Lantern: higher price, with reasons
        p["runs"] = [{"date": day(n), "race_id": f"rac_hist_b{n}", "finished": n != 4, "position": "PU" if n == 4 else "6"}
                     for n in (27, 20, 13, 8, 4)]
    if story == "overvalued":  # owner says 450k; evidence says far less
        p["sales"] = [{"date": day(400), "venue": "Invented Bloodstock Sale", "price": 62_000}]
        p["career_earnings"] = "41000"
    if story == "contradiction":  # declared unfit two weeks ago
        p["declarations"].append({"date": day(14), "race_id": "rac_hist_x", "fit": False, "given_by": "owner",
                                  "note": "lame after work"})
    if story == "pre_existing":  # known condition: excluded, not priced
        p["health_records"].append({"date": day(400), "kind": "tendon scan", "result": "issue", "pre_existing": True,
                                    "condition": "left fore tendon"})
    if story == "long_absence":
        p["runs"] = [{"date": day(260), "race_id": "rac_hist_q", "finished": True, "position": "3"}]
        p["health_records"].append({"date": day(40), "kind": "x-ray", "result": "issue"})
    return p


def run() -> dict:
    dbm.reset_all()
    d = dbm.db()
    now = datetime.now(timezone.utc)
    d[dbm.INSURER_RULES].insert_one({**rules.DEFAULT_RULES})

    races = {r["race_id"]: r for r in [
        _race("rac_k1", "Stakes", "Flat", "Group 3", 6, now=now),
        _race("rac_k2", "Handicap", "Flat", "", 5, now=now),
        _race("rac_k3", "Maiden", "Flat", "", 4, now=now),
        _race("rac_k4", "Handicap Chase", "Chase", "", 7, now=now),
        _race("rac_k5", "Maiden Hurdle", "Hurdle", "", 7.5, now=now),
        _race("rac_k6", "Novice Chase", "Chase", "", 26, status="entry", now=now),
    ]}

    instructions: dict[str, dict] = {}
    tasks = []
    for i, (hid, name, rid, sex, age, rating, owner, share, stated, add_ons, story) in enumerate(RUNNERS):
        race = races[rid]
        race["runners"].append({"horse_id": hid, "horse": name, "age": str(age), "sex": sex,
                                "ofr": str(rating) if rating is not None else "", "owner_id": owner,
                                "trainer_id": f"trn_{i % 3 + 1}", "number": str(len(race["runners"]) + 1)})
        d[dbm.HORSE_PROFILES].insert_one(_profile(i, hid, name, rating, story, now))
        # When the horse leaves the yard. "No answer" horse's has already passed.
        leaves = now - timedelta(minutes=5) if story == "no_answer" else race["off_dt"] - timedelta(hours=3)
        entry = {"horse_id": hid, "ownership_share": share, "stated_value": stated, "add_ons": add_ons, "leaves_yard_at": leaves}
        instructions.setdefault(owner, {"owner_id": owner, "country": OWNERS[owner], "cover_declared_runners": True,
                                        "entries": []})["entries"].append(entry)
        tasks.append({
            "_id": f"task_{rid}_{hid}", "horse_id": hid, "horse": name, "story": story,
            "race": {k: race[k] for k in ("race_id", "race_name", "type", "pattern", "race_status", "off_dt", "off_time")},
            "runner": {"ofr": str(rating) if rating is not None else ""},
            "owner_id": owner, "owner_contact": OWNER_CONTACT[owner], "outreach": [], "ownership_share": share, "stated_value": stated, "add_ons": add_ons,
            "deadline": leaves,  # internal name "loading deadline": when the horse leaves the stable
            "status": "new", "lane": None, "confirmation": None, "quote": None,
            "created_at": now, "updated_at": now,
        })
    for r in races.values():
        r["field_size"] = str(len(r["runners"]))
    d[dbm.RACES].insert_many(list(races.values()))
    d[dbm.INSTRUCTIONS].insert_many(list(instructions.values()))
    d[dbm.TASKS].insert_many(tasks)
    return {"races": len(races), "runners": len(tasks), "owners": len(instructions)}


if __name__ == "__main__":
    print(run())
