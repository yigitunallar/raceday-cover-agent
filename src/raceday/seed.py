"""Invented demo data: one race meeting, twelve declared runners.

Every horse, owner, race and number here is made up.

    python -m raceday.seed
"""
from datetime import datetime, timedelta, timezone

from . import db as dbm
from . import rules

NAMES = ["Copper Lantern", "Quiet Harbour", "Maple Verdict", "Tin Whistle", "Northern Ledger", "Salt Meadow",
         "Paper Comet", "Velvet Anchor", "Second Draft", "Winter Almanac", "Harbour Clerk", "Plain Sailing"]


def run() -> dict:
    dbm.reset_all()
    d = dbm.db()
    now = datetime.now(timezone.utc)
    d[dbm.INSURER_RULES].insert_one({**rules.DEFAULT_RULES})

    races = [
        {"race_id": "rac_demo_1", "course": "Elmfield", "region": "GB", "type": "Flat", "race_class": "4",
         "race_name": "Elmfield Handicap", "off_dt": now + timedelta(hours=5), "race_status": "declared"},
        {"race_id": "rac_demo_2", "course": "Elmfield", "region": "GB", "type": "Chase", "race_class": "2",
         "race_name": "Elmfield Chase", "off_dt": now + timedelta(hours=6), "race_status": "declared"},
        {"race_id": "rac_demo_3", "course": "Elmfield", "region": "GB", "type": "Flat", "race_class": "1",
         "race_name": "Elmfield Stakes", "off_dt": now + timedelta(hours=7), "race_status": "declared"},
    ]
    d[dbm.RACES].insert_many([dict(r) for r in races])

    tasks = []
    for i, name in enumerate(NAMES):
        horse_id = f"hrs_demo_{i + 1:02d}"
        race = races[2] if i == 11 else races[i % 2]
        profile = {
            "horse_id": horse_id, "horse": name, "age": str(3 + i % 6),
            "official_rating": str(55 + i * 4),  # feeds send numbers as strings
            "breeding": {"sire": f"Sire {i + 1}", "dam": f"Dam {i + 1}", "risk_band": "ABC"[i % 3]},
            "runs": [{"date": (now - timedelta(days=40 + i)).date().isoformat(), "race_id": "rac_old", "finished": True, "position": 3}],
            "health_records": [], "declarations": [], "updatedAt": now,
        }
        if i == 1:  # heavy workload and a failed run: same race as horse 3, higher price
            profile["runs"] = [{"date": (now - timedelta(days=n)).date().isoformat(), "race_id": f"rac_old_{n}",
                                "finished": n != 4, "position": 6} for n in (26, 19, 12, 8, 4)]
        if i == 4:  # a recent health issue
            profile["health_records"] = [{"date": (now - timedelta(days=30)).date().isoformat(), "kind": "vet exam", "result": "issue"}]
        if i == 7:  # declared unfit two weeks ago: a clean answer today contradicts it
            profile["declarations"] = [{"date": (now - timedelta(days=14)).date().isoformat(), "race_id": "rac_old", "fit": False, "given_by": "owner"}]
        d[dbm.HORSE_PROFILES].insert_one(profile)
        tasks.append({
            "_id": f"task_{race['race_id']}_{horse_id}", "horse_id": horse_id, "horse": name,
            "race": {k: race[k] for k in ("race_id", "race_name", "type", "race_class", "region", "off_dt")},
            "owner_id": f"own_demo_{i % 4 + 1}", "owner_country": "IE" if i % 4 == 3 else "GB",
            "ownership_share": 1.0 if i != 5 else 0.5,
            # "Loading deadline": when the horse leaves the stable.
            "deadline": race["off_dt"] - timedelta(hours=3),
            "status": "new", "lane": None, "confirmation": None, "quote": None,
            "created_at": now, "updated_at": now,
        })
    d[dbm.TASKS].insert_many(tasks)
    return {"races": len(races), "runners": len(tasks)}


if __name__ == "__main__":
    print(run())
