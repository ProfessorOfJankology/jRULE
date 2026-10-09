"""jAPI-only JSON GET sources and JSON POST actions."""
import asyncio
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
import httpx
from . import db, state
from .rules import render_template, resolve_template_value

NAME=re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')
ENV_NAME=re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

def validate_name(name):
    if not NAME.fullmatch(name): raise ValueError('Name must start with a letter and contain only letters, digits, - or _ (max 64)')
    return name

def validate_endpoint(endpoint):
    """Allow jAPI-relative /v1 routes with an optional query string."""
    from urllib.parse import unquote
    if not isinstance(endpoint,str) or len(endpoint)>1024 or any(ch.isspace() for ch in endpoint) or '\\' in endpoint:
        raise ValueError('Endpoint must be a relative jAPI route')
    parts=urlsplit(endpoint)
    if parts.scheme or parts.netloc or parts.fragment or not parts.path.startswith('/v1/'):
        raise ValueError('Endpoint must be a relative jAPI route starting /v1/')
    decoded=unquote(parts.path)
    if '//' in parts.path or any(part in ('.','..') for part in decoded.split('/')):
        raise ValueError('Endpoint traversal is not allowed')
    if not re.fullmatch(r'/v1/[A-Za-z0-9_./-]+', parts.path):
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

_MISSING=object()

def _mapping_value(payload,spec):
    """Resolve one derived property mapping.

    Backward compatible string specs are plain JSON paths.
    Object specs support: path, op (value|exists|contains|count|first|equals),
    value (for contains/equals), and default.
    """
    if isinstance(spec,str):
        return json_path(payload,spec)
    if not isinstance(spec,dict):
        raise ValueError('Property mapping must be a JSON path string or mapping object')
    path=spec.get('path','')
    op=spec.get('op','value')
    default=spec.get('default',None)
    try:
        raw=json_path(payload,path)
        found=True
    except (KeyError,IndexError,TypeError):
        raw=_MISSING
        found=False
    if op=='exists':
        return found
    if not found:
        return default
    if op=='value':
        return raw
    if op=='first':
        return raw[0] if isinstance(raw,list) and raw else default
    if op=='count':
        return len(raw) if isinstance(raw,(list,dict,str)) else default
    if op=='contains':
        try:return spec.get('value') in raw
        except TypeError:return False
    if op=='equals':
        return raw==spec.get('value')
    raise ValueError(f'Unsupported mapping operation: {op}')

def validate_mapping(mapping):
    if not isinstance(mapping,dict):
        raise ValueError('Property mapping must be a JSON object')
    for alias,spec in mapping.items():
        validate_name(alias)
        if isinstance(spec,str):
            if len(spec)>255:raise ValueError('JSON property path is too long')
            continue
        if not isinstance(spec,dict):
            raise ValueError(f'Invalid mapping for {alias}')
        if set(spec)-{'path','op','value','default'}:
            raise ValueError(f'Unsupported mapping option for {alias}')
        path=spec.get('path','')
        if not isinstance(path,str) or len(path)>255:
            raise ValueError(f'Invalid JSON path for {alias}')
        if spec.get('op','value') not in {'value','exists','contains','count','first','equals'}:
            raise ValueError(f'Invalid mapping operation for {alias}')
    return mapping

def select_properties(payload,mapping):
    if not isinstance(payload,dict):
        raise ValueError('Source JSON must be an object')
    # Keep usable top-level fields available even when derived properties exist.
    props={}
    for k,v in payload.items():
        try:props[validate_name(k)]=v
        except ValueError:pass
    if mapping:
        validate_mapping(mapping)
        for alias,spec in mapping.items():
            props[validate_name(alias)]=_mapping_value(payload,spec)
    return props

def discover_fields(payload,limit=500):
    """Flatten the current JSON response into dotted paths."""
    fields=[]
    def walk(value,path):
        if len(fields)>=limit:return
        if path:
            fields.append({'path':path,'value':value,'type':type(value).__name__})
        if isinstance(value,dict):
            for key,item in value.items():
                child=f'{path}.{key}' if path else str(key)
                walk(item,child)
        # Lists are atomic collection values for discovery. Numeric list
        # positions are not stable field identities, so do not persist .0/.1/etc.
    walk(payload,'')
    return fields

def merge_discovered_fields(previous,current,seen_at,limit=1000):
    """Keep a cumulative catalogue while marking what exists in this poll."""
    merged={}
    for field in previous or []:
        path=field.get('path')
        if not path or any(part.isdigit() for part in path.split('.')):continue
        item=dict(field)
        item['present']=False
        item.setdefault('first_seen',item.get('last_seen'))
        merged[path]=item
    for field in current or []:
        path=field.get('path')
        if not path:continue
        old=merged.get(path,{})
        merged[path]={
            'path':path,
            'value':field.get('value'),
            'type':field.get('type'),
            'present':True,
            'first_seen':old.get('first_seen') or old.get('last_seen') or seen_at,
            'last_seen':seen_at,
        }
    values=list(merged.values())
    values.sort(key=lambda x:(not x.get('present',False),x.get('path','').lower()))
    return values[:limit]

async def poll_source(name):
    async with state.connection() as conn:
        row=await (await conn.execute('SELECT s.* FROM http_sources s JOIN objects o ON o.name=s.name WHERE s.name=? AND s.enabled=1 AND o.enabled=1',(name,))).fetchone()
    if not row:raise ValueError(f'Unknown or disabled source: {name}')
    now=db.utc_now()
    started=asyncio.get_running_loop().time()
    status_code=None
    try:
        # No redirects, so a misconfigured service cannot redirect to an unexpected host.
        async with httpx.AsyncClient(timeout=10,follow_redirects=False,trust_env=False) as client:
            resp=await client.get(japi_url(row['url']),headers=headers_for())
            status_code=resp.status_code
            resp.raise_for_status()
            if len(resp.content)>1048576:raise ValueError('Source response exceeds 1 MiB')
            payload=resp.json()
            props=select_properties(payload,json.loads(row['mapping_json']))
            if len(props)>250:raise ValueError('Source exposes more than 250 properties')
            observed=discover_fields(payload)
            previous_discovered=json.loads(row['discovered_json'] or '[]')
            discovered=merge_discovered_fields(previous_discovered,observed,now)
        sequence=await state.apply_source_poll(name,props,json.dumps(discovered,default=str),now)
        elapsed_ms=round((asyncio.get_running_loop().time()-started)*1000)
        await db.log_event(
            level='info',event_type='source_poll',
            message=f"{name}: poll succeeded (HTTP {status_code}, {elapsed_ms} ms, sequence {sequence})",
            details={'source':name,'endpoint':row['url'],'started_at':now,
                     'duration_ms':elapsed_ms,'http_status':status_code,
                     'poll_sequence':sequence,'response':payload}
        )
        return {'name':name,'updated':list(props),'poll_sequence':sequence}
    except Exception as exc:
        async with state.connection() as conn:
            await conn.execute('UPDATE http_sources SET last_attempt=?,last_error=? WHERE name=?',(now,str(exc)[:500],name))
            await conn.commit()
        elapsed_ms=round((asyncio.get_running_loop().time()-started)*1000)
        await db.log_event(
            level='error',event_type='source_poll_error',message=f'{name}: {exc}',
            details={'source':name,'endpoint':row['url'],'started_at':now,
                     'duration_ms':elapsed_ms,'http_status':status_code,'error':str(exc)}
        )
        raise

_EXACT_TEMPLATE=re.compile(r'^\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}$')

def render_body(value,context):
    if isinstance(value,str):
        match=_EXACT_TEMPLATE.fullmatch(value)
        if match:
            # A whole-value template preserves the underlying JSON type.
            # Missing paths intentionally become JSON null so disappearance
            # events can clear downstream state.
            try:
                return resolve_template_value(match.group(1),context)
            except ValueError as exc:
                if "was not found" in str(exc):
                    return None
                raise
        return render_template(value,context)
    if isinstance(value,list):return [render_body(v,context) for v in value]
    if isinstance(value,dict):return {k:render_body(v,context) for k,v in value.items()}
    return value

async def invoke_action(name,arguments,context):
    async with state.connection() as conn:
        row=await (await conn.execute('SELECT * FROM http_actions WHERE name=? AND enabled=1',(name,))).fetchone()
    if not row:raise ValueError(f'Unknown or disabled HTTP action: {name}')
    # Resolve rule-supplied argument templates first, then expose them as
    # {{ args.some_field }} inside the configured jAPI body template.
    rendered_arguments=render_body(arguments,context)
    values={**context,'args':rendered_arguments}
    payload=render_body(json.loads(row['body_json']),values)
    from .modules import unwrap_literal
    payload=unwrap_literal(payload)
    async with httpx.AsyncClient(timeout=row['timeout_seconds'],follow_redirects=False,trust_env=False) as client:
        resp=await client.post(japi_url(row['url']),headers=headers_for(),json=payload)
        if resp.is_error:
            try:
                response_detail=resp.json()
            except Exception:
                response_detail=resp.text[:2000]
            raise RuntimeError(
                f"Action {name} failed: HTTP {resp.status_code}; response={json.dumps(response_detail,default=str)}; "
                f"payload={json.dumps(payload,default=str)}"
            )
    await db.log_event(
        level='info',
        event_type='http_action_sent',
        message=f'Action {name} completed: HTTP {resp.status_code}',
        details={
            'action':name,
            'endpoint':row['url'],
            'status_code':resp.status_code,
            'payload':payload,
        },
    )

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
