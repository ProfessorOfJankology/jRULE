"""jAPI-only JSON GET sources and JSON POST actions."""
import asyncio
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
import httpx
from . import db, state
from .rules import render_template

NAME=re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
ENV_NAME=re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

def validate_name(name):
    if not NAME.fullmatch(name): raise ValueError('Name must start with a letter and contain only letters, digits, - or _ (max 64)')
    return name

def validate_endpoint(endpoint):
    """Only jAPI-relative endpoints. Reject URLs, fragments, query tricks and traversal."""
    from urllib.parse import unquote
    if (not isinstance(endpoint,str) or not endpoint.startswith('/v1/') or
            len(endpoint)>512 or '?' in endpoint or '#' in endpoint or
            any(ch.isspace() for ch in endpoint) or '\\' in endpoint or
            '//' in endpoint or any(part in ('.','..') for part in unquote(endpoint).split('/')) or
            '%' in endpoint):
        raise ValueError('Endpoint must be a plain jAPI route starting /v1/ (no URL, query or traversal)')
    if not re.fullmatch(r'/v1/[A-Za-z0-9_./-]+', endpoint):
        raise ValueError('Invalid jAPI endpoint path')
    return endpoint


def japi_url(endpoint):
    validate_endpoint(endpoint)
    base=os.getenv('JRULE_JAPI_BASE_URL','http://127.0.0.1:8088').rstrip('/')
    parts=urlsplit(base)
    if (parts.scheme not in ('http','https') or not parts.hostname or parts.username or
            parts.password or parts.path or parts.query or parts.fragment):
        raise RuntimeError('Invalid server-side JRULE_JAPI_BASE_URL')
    return base+endpoint


def headers_for(_row=None):
    # The systemd pre-start hook reads ONLY API_KEY from jAPI's env into a
    # private runtime file, refreshed whenever jRULE restarts. No SQL secrets
    # or API key appear in SQLite or the browser.
    key_file=os.getenv('JRULE_JAPI_KEY_FILE','/run/jrule/japi-api-key')
    try:
        value=open(key_file,encoding='utf-8').read().strip()
    except OSError as exc:
        raise RuntimeError('jAPI key unavailable; check jrule.service pre-start hook') from exc
    if not value:
        raise RuntimeError('jAPI API_KEY is empty')
    return {'X-API-Key':value}

def json_path(data,path):
    """Dotted mapping path, with numeric list indices (e.g. sessions.0.user)."""
    if path in ('','$'):return data
    current=data
    for part in path.split('.'):
        if isinstance(current,dict): current=current[part]
        elif isinstance(current,list) and part.isdigit():current=current[int(part)]
        else:raise KeyError(path)
    return current

def select_properties(payload,mapping):
    if mapping:
        return {validate_name(alias):json_path(payload,path) for alias,path in mapping.items()}
    if not isinstance(payload,dict):raise ValueError('Source JSON must be an object, or supply a property mapping')
    return {validate_name(k):v for k,v in payload.items()}

async def poll_source(name):
    async with state.connection() as conn:
        row=await (await conn.execute('SELECT s.* FROM http_sources s JOIN objects o ON o.name=s.name WHERE s.name=? AND s.enabled=1 AND o.enabled=1',(name,))).fetchone()
    if not row:raise ValueError(f'Unknown or disabled source: {name}')
    now=db.utc_now()
    try:
        # No redirects, so a misconfigured service cannot redirect to an unexpected host.
        async with httpx.AsyncClient(timeout=10,follow_redirects=False,trust_env=False) as client:
            resp=await client.get(japi_url(row['url']),headers=headers_for())
            resp.raise_for_status()
            if len(resp.content)>1048576:raise ValueError('Source response exceeds 1 MiB')
            props=select_properties(resp.json(),json.loads(row['mapping_json']))
            if len(props)>250:raise ValueError('Source exposes more than 250 properties')
        await state.update_properties(name,props)
        async with state.connection() as conn:
            await conn.execute('UPDATE http_sources SET last_attempt=?,last_success=?,last_error=NULL WHERE name=?',(now,now,name))
            await conn.commit()
        return {'name':name,'updated':list(props)}
    except Exception as exc:
        async with state.connection() as conn:
            await conn.execute('UPDATE http_sources SET last_attempt=?,last_error=? WHERE name=?',(now,str(exc)[:500],name))
            await conn.commit()
        await db.log_event(level='error',event_type='source_poll_error',message=f'{name}: {exc}')
        raise

def render_body(value,context):
    if isinstance(value,str):return render_template(value,context)
    if isinstance(value,list):return [render_body(v,context) for v in value]
    if isinstance(value,dict):return {k:render_body(v,context) for k,v in value.items()}
    return value

async def invoke_action(name,arguments,context):
    async with state.connection() as conn:
        row=await (await conn.execute('SELECT * FROM http_actions WHERE name=? AND enabled=1',(name,))).fetchone()
    if not row:raise ValueError(f'Unknown or disabled HTTP action: {name}')
    # Arguments are accessible as {{ args.some_field }} in a configured POST body.
    values={**context,'args':arguments}
    payload=render_body(json.loads(row['body_json']),values)
    async with httpx.AsyncClient(timeout=row['timeout_seconds'],follow_redirects=False,trust_env=False) as client:
        resp=await client.post(japi_url(row['url']),headers=headers_for(),json=payload)
        resp.raise_for_status()
    await db.log_event(level='info',event_type='http_action_sent',message=f'Action {name} completed: HTTP {resp.status_code}')

async def loop(stop):
    due={}
    while not stop.is_set():
        async with state.connection() as conn:
            rows=await (await conn.execute('SELECT s.name,s.interval_seconds FROM http_sources s JOIN objects o ON s.name=o.name WHERE s.enabled=1 AND o.enabled=1')).fetchall()
        now=asyncio.get_running_loop().time()
        active={r['name'] for r in rows}
        for name in list(due):
            if name not in active:due.pop(name,None)
        for row in rows:
            name=row['name']
            if now>=due.get(name,0):
                due[name]=now+max(5,int(row['interval_seconds']))
                try:await poll_source(name)
                except Exception:pass
        try:await asyncio.wait_for(stop.wait(),timeout=1)
        except asyncio.TimeoutError:pass