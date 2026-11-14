from unittest.mock import MagicMock
from raceday.agent import outreach


def test_scoped_outreach_skips_other_tasks(monkeypatch):
    d = MagicMock()
    d[outreach.SETTINGS].find_one.return_value = {'auto_call': True, 'only_task_id': 'wanted'}
    monkeypatch.setattr(outreach.dbm, 'db', lambda: d)
    decide = MagicMock()
    monkeypatch.setattr(outreach, 'decide', decide)
    assert outreach.maybe_reach_owner({'_id': 'other', 'confirmation': None}) is None
    decide.assert_not_called()
