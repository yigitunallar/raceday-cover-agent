"""Demo quote intake using an existing declared racehorse."""
import re
from datetime import datetime, timezone
from uuid import uuid4
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from .. import db as dbm

router = APIRouter()


class IntakeRequest(BaseModel):
    horse_name: str = Field(min_length=1, max_length=100)
    horse_id: str = Field(default='', max_length=100)
    medical_history: str = Field(min_length=1, max_length=4000)
    fit_to_run: Literal['yes', 'no', 'unknown'] = 'unknown'
    health_kind: str = Field(default='', max_length=100)
    health_date: str = Field(default='', max_length=10, pattern=r'^$|^\d{4}-\d{2}-\d{2}$')
    health_result: str = Field(default='', max_length=500)

    @field_validator('horse_name', 'horse_id', 'medical_history')
    @classmethod
    def strip(cls, value):
        return value.strip()


@router.post('/intake/requests', status_code=201)
def create_intake(body: IntakeRequest):
    if not body.horse_name or not body.medical_history:
        raise HTTPException(422, 'Horse name and medical information are required')
    d = dbm.db()
    query = {'horse': {'$regex': '^' + re.escape(body.horse_name) + '$', '$options': 'i'},
             'race.race_status': 'declared', 'quote_only': {'$ne': True}}
    if body.horse_id:
        query['horse_id'] = body.horse_id
    matches = list(d[dbm.TASKS].find(query).limit(2))
    if len(matches) != 1:
        raise HTTPException(409, 'Horse must match exactly one declared demo task; include its horse ID if ambiguous')
    source = matches[0]
    deadline = source.get('deadline')
    now = datetime.now(timezone.utc)
    if not isinstance(deadline, datetime):
        raise HTTPException(409, 'Horse departure deadline is missing')
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    if deadline <= now:
        raise HTTPException(409, 'Horse departure deadline has passed')
    if not d[dbm.HORSE_PROFILES].find_one({'horse_id': source['horse_id']}):
        raise HTTPException(409, 'Horse profile is unavailable')
    task = {key: source.get(key) for key in ('horse_id', 'horse', 'race', 'runner', 'owner_id', 'ownership_share', 'stated_value', 'add_ons', 'deadline')}
    declaration = None if body.fit_to_run == 'unknown' else {'fit': body.fit_to_run == 'yes', 'given_by': 'owner (demo form)', 'at': now}
    health_record = {'kind': body.health_kind, 'date': body.health_date, 'result': body.health_result}
    task.update(_id='quote_' + uuid4().hex, owner_medical_history=body.medical_history,
                owner_declaration=declaration, owner_health_record=health_record,
                quote_only=True, status='new', confirmation=None, quote=None, created_at=now, updated_at=now)
    d[dbm.TASKS].insert_one(task)
    return {'task_id': task['_id'], 'status': 'new'}
