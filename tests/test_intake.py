from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from raceday.agent import investigator
from raceday.api import intake
from raceday import db as dbm


def test_intake_creates_quote_task_and_preserves_submitted_notes(monkeypatch):
    database = MagicMock()
    monkeypatch.setattr(dbm, 'db', lambda: database)
    source = {'_id': 'task_original', 'horse': 'Demo Horse', 'horse_id': 'h1',
              'race': {'race_id': 'r1', 'race_status': 'declared'}, 'runner': {},
              'ownership_share': 1, 'deadline': datetime.now(timezone.utc) + timedelta(hours=1)}
    database[dbm.TASKS].find.return_value.limit.return_value = [source]
    database[dbm.HORSE_PROFILES].find_one.return_value = {'horse_id': 'h1'}
    result = intake.create_intake(intake.IntakeRequest(horse_name='Demo Horse', medical_history='Recent veterinary exam'))
    created = database[dbm.TASKS].insert_one.call_args.args[0]
    assert result['task_id'] == created['_id']
    assert created['quote_only'] and created['owner_medical_history'] == 'Recent veterinary exam'
    assert created['confirmation'] is None and created['status'] == 'new'
    assert 'get_health_records' in investigator.required_checks(created)
    assert investigator._tools_impl(created, {})['get_health_records']()['owner_provided_history'] == 'Recent veterinary exam'


def test_quote_only_stops_after_verified_quote(monkeypatch):
    from raceday.agent import graph
    task = {'_id': 'quote_demo', 'horse_id': 'h1', 'race': {'race_status': 'declared'},
            'runner': {}, 'ownership_share': 1, 'investigation': {'trail': []}, 'quote_only': True}
    q = {'offerable': True, 'over_limit': False, 'total': 30}
    database = MagicMock()
    monkeypatch.setattr(dbm, 'db', lambda: database)
    monkeypatch.setattr(graph, '_task', lambda state: task)
    updates = []
    monkeypatch.setattr(graph, '_set', lambda tid, **fields: updates.append(fields))
    monkeypatch.setattr(graph.rules, 'quote', lambda *args, **kwargs: q)
    monkeypatch.setattr(graph, '_explain', lambda quote, old: quote)
    database[dbm.TASKS].find_one.return_value = {'quote': q}
    assert graph.price({'task_id': task['_id']}) == {'status': 'quoted'}
    assert updates[-1] == {'status': 'quoted'}
    database[dbm.TASKS].find_one.return_value = {'quote': {'total': 99}}
    assert graph.price({'task_id': task['_id']}) == {'status': 'quote_unverified'}
