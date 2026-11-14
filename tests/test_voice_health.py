from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest
from fastapi import HTTPException
from raceday.agent import voice
from raceday.api import app as api


def test_missing_health_is_passed_to_phone_agent():
    task = {'_id': 't1', 'horse_id': 'h1', 'horse': 'Demo', 'investigation': {'summary': 'Health history unknown.'}}
    variables = voice.phone_variables(task, {'health_records': []})
    assert variables['health_followup_required'] == 'yes'
    assert 'health' in variables['health_question'].lower()
    assert voice.phone_variables(task, {'health_records': [{'result': 'clear'}]})['health_followup_required'] == 'no'


def test_required_health_must_be_recorded_before_yes(monkeypatch):
    monkeypatch.setattr(api.config, 'VOICE_WEBHOOK_SECRET', 'secret')
    db = MagicMock()
    collections = {name: MagicMock() for name in (api.dbm.TASKS, api.dbm.TIMELINE)}
    db.__getitem__.side_effect = collections.__getitem__
    db[api.dbm.TASKS].find_one.return_value = {'_id': 't1', 'voice_health_required': True}
    monkeypatch.setattr(api.dbm, 'db', lambda: db)
    body = api.VoiceAnswer(task_id='t1', answer='yes', conversation_id='conv1')
    with pytest.raises(HTTPException) as exc:
        api.voice_answer(body, 'secret')
    assert exc.value.status_code == 409
    db[api.dbm.TASKS].update_one.assert_not_called()


def test_health_answer_is_saved_and_investigation_invalidated(monkeypatch):
    monkeypatch.setattr(api.config, 'VOICE_WEBHOOK_SECRET', 'secret')
    db = MagicMock()
    collections = {name: MagicMock() for name in (api.dbm.TASKS, api.dbm.TIMELINE)}
    db.__getitem__.side_effect = collections.__getitem__
    db[api.dbm.TASKS].find_one.return_value = {'_id': 't1', 'voice_health_required': True}
    db[api.dbm.TASKS].update_one.return_value.matched_count = 1
    db[api.dbm.TIMELINE].find_one.return_value = {'task_id':'t1'}
    monkeypatch.setattr(api.dbm, 'db', lambda: db)
    result = api.voice_answer(api.VoiceAnswer(task_id='t1', answer='yes', conversation_id='conv1', health_notes='Exam is scheduled', health_resolved=True), 'secret')
    args = db[api.dbm.TASKS].update_one.call_args.args
    assert result['ok']
    assert args[1]['$set']['owner_health_followup']['notes'] == 'Exam is scheduled'
    assert args[1]['$set']['investigation'] is None


def test_unknown_health_is_saved_but_does_not_confirm(monkeypatch):
    monkeypatch.setattr(api.config, 'VOICE_WEBHOOK_SECRET', 'secret')
    db = MagicMock()
    collections = {name: MagicMock() for name in (api.dbm.TASKS, api.dbm.TIMELINE)}
    db.__getitem__.side_effect = collections.__getitem__
    db[api.dbm.TASKS].find_one.return_value = {'_id': 't1', 'voice_health_required': True}
    db[api.dbm.TASKS].update_one.return_value.matched_count = 1
    db[api.dbm.TIMELINE].find_one.return_value = {'task_id':'t1'}
    monkeypatch.setattr(api.dbm, 'db', lambda: db)
    result = api.voice_answer(api.VoiceAnswer(task_id='t1', answer='yes', conversation_id='conv1', health_notes='Owner does not know', health_resolved=False), 'secret')
    fields = db[api.dbm.TASKS].update_one.call_args.args[1]['$set']
    assert fields['status'] == 'health_unresolved'
    assert 'confirmation' not in fields
    assert not result['ok']
