from raceday.api.app import _demo_settled


def test_demo_is_ready_only_when_no_horse_is_left_unpriced():
    assert _demo_settled({"awaiting_owner": 10, "referred": 1, "not_confirmed": 1, "not_eligible": 1})
    assert not _demo_settled({"awaiting_owner": 10, "new": 1})
    assert not _demo_settled({"awaiting_owner": 10, "priced": 1})
    assert not _demo_settled({})  # nothing seeded yet is not ready
