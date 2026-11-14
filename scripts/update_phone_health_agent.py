"""Prepare or apply a health-follow-up update to an existing ElevenLabs agent.

Set ELEVENLABS_API_KEY and ELEVENLABS_AGENT_ID. Run without --apply to inspect
only. After the backend is deployed, --apply creates a separate webhook tool
and attaches it to the existing agent; it does not edit the shared old tool.
"""
import copy
import os
import sys

import httpx

from setup_phone_agent import PROMPT, FIRST

BASE = 'https://api.elevenlabs.io/v1/convai'
KEY = os.environ['ELEVENLABS_API_KEY']
AGENT = os.environ['ELEVENLABS_AGENT_ID']
HEADERS = {'xi-api-key': KEY}


def main():
    agent_response = httpx.get(f'{BASE}/agents/{AGENT}', headers=HEADERS, timeout=30)
    agent_response.raise_for_status()
    agent = agent_response.json()['conversation_config']['agent']
    prompt = agent['prompt']
    tool_ids = prompt.get('tool_ids') or []
    answer_tools = []
    for tool_id in tool_ids:
        response = httpx.get(f'{BASE}/tools/{tool_id}', headers=HEADERS, timeout=30)
        response.raise_for_status()
        config = response.json()['tool_config']
        if config.get('name') == 'record_answer':
            answer_tools.append((tool_id, config))
    if len(answer_tools) != 1:
        raise RuntimeError('Expected exactly one existing record_answer tool; nothing changed')
    old_id, old = answer_tools[0]
    schema = old.get('api_schema') or {}
    headers = schema.get('request_headers') or {}
    if not headers.get('X-Webhook-Secret'):
        raise RuntimeError('Existing webhook secret is not readable; nothing changed')
    body = copy.deepcopy(schema['request_body_schema'])
    body['properties']['health_notes'] = {'type': 'string', 'description': 'The owner-provided health information, required when requested.'}
    body['properties']['health_resolved'] = {'type': 'boolean', 'description': 'True only when the requested health details are clearly resolved; false if unknown.'}
    new_tool = {'type': 'webhook', 'name': 'record_health_and_answer',
                'description': 'Record the clear fit-to-run answer and any requested health follow-up.',
                'execution_mode': 'immediate', 'response_timeout_secs': 20,
                'api_schema': {'url': schema['url'], 'method': schema['method'],
                               'content_type': schema.get('content_type') or 'application/json',
                               'request_headers': headers, 'request_body_schema': body}}
    variables = copy.deepcopy(agent.get('dynamic_variables') or {})
    defaults = variables.setdefault('dynamic_variable_placeholders', {})
    defaults.update(missing_information='No investigation summary available.', health_followup_required='no', health_question='No health follow-up is needed.')
    updated_prompt = PROMPT.replace('record_answer', 'record_health_and_answer')
    print(f'Agent {AGENT}: replace one answer tool, preserve {len(tool_ids)-1} other tool(s), add three variables.')
    if '--apply' not in sys.argv:
        print('Dry run only. No configuration changed.')
        return
    created = httpx.post(f'{BASE}/tools', headers=HEADERS, json={'tool_config': new_tool}, timeout=30)
    created.raise_for_status()
    new_id = created.json()['id']
    updated_ids = [new_id if value == old_id else value for value in tool_ids]
    patch = {'conversation_config': {'agent': {'first_message': FIRST,
              'prompt': {'prompt': updated_prompt, 'tool_ids': updated_ids},
              'dynamic_variables': variables}}}
    response = httpx.patch(f'{BASE}/agents/{AGENT}', headers=HEADERS, json=patch, timeout=30)
    if not response.is_success:
        raise RuntimeError(f'Agent update failed ({response.status_code}); new tool {new_id} was created but not attached.')
    check = httpx.get(f'{BASE}/agents/{AGENT}', headers=HEADERS, timeout=30)
    check.raise_for_status()
    saved = check.json()['conversation_config']['agent']
    saved_ids = saved['prompt'].get('tool_ids') or []
    if new_id not in saved_ids or old_id in saved_ids or saved['prompt'].get('prompt') != updated_prompt:
        raise RuntimeError('Update response did not match expected agent configuration; inspect before calling')
    untouched = [key for key in agent if key not in ('first_message', 'prompt', 'dynamic_variables')]
    prompt_keys = [key for key in prompt if key not in ('prompt', 'tool_ids')]
    old_vars = (agent.get('dynamic_variables') or {}).get('dynamic_variable_placeholders') or {}
    new_vars = (saved.get('dynamic_variables') or {}).get('dynamic_variable_placeholders') or {}
    if any(saved.get(key) != agent.get(key) for key in untouched) or any(saved['prompt'].get(key) != prompt.get(key) for key in prompt_keys) or any(new_vars.get(k) != v for k, v in old_vars.items()):
        raise RuntimeError('Some existing agent settings changed; inspect before calling')
    print(f'Updated agent with tool {new_id}; existing agent settings verified. Test a synthetic call before relying on it.')


if __name__ == '__main__':
    main()
