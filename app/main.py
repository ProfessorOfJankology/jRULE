"""jRULE: global per-property pool, independently scheduled jAPI sources/actions."""
from __future__ import annotations
import asyncio
import hmac
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import db, state, engine, modules, http_services as http
from . import APP_NAME, APP_VERSION, APP_BUILD

DIR=Path(__file__).parent
stop=asyncio.Event()
workers=[]
@asynccontextmanager
async def lifespan(app):
    await db.init_db()
    stop.clear()
    workers.extend([asyncio.create_task(engine.loop(stop)),asyncio.create_task(http.loop(stop))])
    yield
    stop.set()
    await asyncio.gather(*workers,return_exceptions=True)
    workers.clear()
app=FastAPI(title=APP_NAME,version=APP_VERSION,lifespan=lifespan)
app.mount('/static',StaticFiles(directory=DIR/'static'),name='static')

@app.middleware('http')
async def admin_guard(request:Request,call_next):
    # All mutations, including manual polling and rule evaluation, require a token.
    if request.method not in ('GET','HEAD','OPTIONS') and request.url.path.startswith('/api/'):
        expected=os.getenv('JRULE_ADMIN_TOKEN','')
        received=request.headers.get('x-jrule-admin-token','')
        if not expected:return JSONResponse({'detail':'Set JRULE_ADMIN_TOKEN in /etc/jrule/jrule.env to enable configuration writes'},status_code=503)
        if not received or not hmac.compare_digest(received,expected):return JSONResponse({'detail':'Invalid administrator token'},status_code=401)
    return await call_next(request)

@app.get('/')
async def home():return FileResponse(DIR/'static/jrule.html')
@app.get('/api/version')
async def version():return {'name':APP_NAME,'version':APP_VERSION,'build':APP_BUILD}
@app.get('/api/objects')
async def objects():return await state.pool()
@app.get('/api/global-variables')
async def variables():
    data=await state.pool()
    return sorted([f'{t}.{name}.{prop}' for name,obj in data.items() for prop in obj['properties'] for t in ('current','previous')]+[f'meta.{name}.{prop}.{t}' for name,obj in data.items() for prop in obj['properties'] for t in ('last_polled','last_changed')])
@app.get('/api/modules/sources')
async def source_catalog():return {'japi.get':{'kind':'poll','object_types':['japi'],'supports_mapping':True,'supports_query':True,'supports_derived_fields':True}}
@app.get('/api/modules/actions')
async def action_catalog():return await modules.catalog()
@app.get('/api/settings')
async def settings():return await db.get_settings()
@app.put('/api/settings')
async def put_settings(payload:dict[str,Any]):
    if set(payload)-{'rule_interval_seconds'}:raise HTTPException(400,'Unsupported setting')
    value=int(payload['rule_interval_seconds'])
    if not 1<=value<=86400:raise HTTPException(400,'rule_interval_seconds must be 1..86400')
    await db.set_settings({'rule_interval_seconds':value})
    return await db.get_settings()

class SourceIn(BaseModel):
    name:str
    url:str
    interval_seconds:int=60
    mapping:dict[str,Any]=Field(default_factory=dict)
    enabled:bool=True

def validate_source(payload:SourceIn):
    try:
        http.validate_name(payload.name)
        http.validate_endpoint(payload.url)
        http.validate_mapping(payload.mapping)
        if not 5<=payload.interval_seconds<=86400:raise ValueError('Polling interval must be 5..86400 seconds')
    except ValueError as e:raise HTTPException(400,str(e)) from e

@app.get('/api/sources')
async def list_sources():
    async with state.connection() as conn:
        rows=await (await conn.execute('SELECT * FROM http_sources ORDER BY name')).fetchall()
    return [{**dict(r),'mapping':json.loads(r['mapping_json'])} for r in rows]
@app.post('/api/sources')
async def create_source(payload:SourceIn):
    validate_source(payload)
    async with state.connection() as conn:
        existing=await (await conn.execute('SELECT 1 FROM objects WHERE name=?',(payload.name,))).fetchone()
        if existing:raise HTTPException(409,'Object name already exists')
    await state.register_object(payload.name,'japi.get','japi')
    async with state.connection() as conn:
        await conn.execute('INSERT INTO http_sources(name,url,interval_seconds,mapping_json,auth_env,auth_header,auth_prefix,enabled) VALUES(?,?,?,?,?,?,?,?)',(payload.name,payload.url,payload.interval_seconds,json.dumps(payload.mapping),'','X-API-Key','',int(payload.enabled)))
        await conn.commit()
    return {'ok':True,'name':payload.name}
@app.put('/api/sources/{name}')
async def update_source(name:str,payload:SourceIn):
    if name!=payload.name:raise HTTPException(400,'Cannot rename source')
    validate_source(payload)
    async with state.connection() as conn:
        cur=await conn.execute('UPDATE http_sources SET url=?,interval_seconds=?,mapping_json=?,auth_env=?,auth_header=?,auth_prefix=?,enabled=? WHERE name=?',(payload.url,payload.interval_seconds,json.dumps(payload.mapping),'','X-API-Key','',int(payload.enabled),name))
        if cur.rowcount==0:raise HTTPException(404,'Unknown source')
        await conn.commit()
    return {'ok':True}
@app.delete('/api/sources/{name}')
async def delete_source(name:str):
    async with state.connection() as conn:
        cur=await conn.execute("DELETE FROM objects WHERE name=? AND module='japi.get'",(name,))
        if cur.rowcount==0:raise HTTPException(404,'Unknown source')
        await conn.commit()
    return {'ok':True}
@app.post('/api/sources/{name}/poll')
async def poll_now(name:str):
    try:return await http.poll_source(name)
    except ValueError as e:raise HTTPException(400,str(e)) from e
    except Exception as e:raise HTTPException(502,str(e)) from e

class DerivedFieldIn(BaseModel):
    name:str
    path:str
    op:str='value'
    value:Any=None
    default:Any=None

@app.get('/api/sources/{name}/fields')
async def source_fields(name:str):
    async with state.connection() as conn:
        row=await (await conn.execute('SELECT discovered_json,mapping_json FROM http_sources WHERE name=?',(name,))).fetchone()
    if not row:raise HTTPException(404,'Unknown source')
    return {'fields':json.loads(row['discovered_json'] or '[]'),'mapping':json.loads(row['mapping_json'] or '{}')}

@app.post('/api/sources/{name}/derived-fields')
async def add_derived_field(name:str,payload:DerivedFieldIn):
    try:
        http.validate_name(payload.name)
        spec={'path':payload.path,'op':payload.op,'default':payload.default}
        if payload.op in ('contains','equals'):spec['value']=payload.value
        http.validate_mapping({payload.name:spec})
    except ValueError as e:raise HTTPException(400,str(e)) from e
    async with state.connection() as conn:
        row=await (await conn.execute('SELECT mapping_json FROM http_sources WHERE name=?',(name,))).fetchone()
        if not row:raise HTTPException(404,'Unknown source')
        mapping=json.loads(row['mapping_json'] or '{}')
        mapping[payload.name]=spec
        await conn.execute('UPDATE http_sources SET mapping_json=? WHERE name=?',(json.dumps(mapping),name))
        await conn.commit()
    try:
        polled=await http.poll_source(name)
    except Exception as e:
        return {'ok':True,'name':payload.name,'poll_error':str(e)}
    return {'ok':True,'name':payload.name,'updated':polled.get('updated',[])}

class VariableIn(BaseModel):
    name:str
    value:Any=None

@app.get('/api/custom-variables')
async def list_custom_variables():
    snapshot=await state.pool()
    props=snapshot.get('variables',{}).get('properties',{})
    return [{'name':name,**value} for name,value in sorted(props.items())]

@app.post('/api/custom-variables')
async def create_custom_variable(payload:VariableIn):
    try:http.validate_name(payload.name)
    except ValueError as e:raise HTTPException(400,str(e)) from e
    async with state.connection() as conn:
        exists=await (await conn.execute("SELECT 1 FROM object_properties WHERE object_name='variables' AND property_name=?",(payload.name,))).fetchone()
    if exists:raise HTTPException(409,'Variable already exists')
    await state.update_properties('variables',{payload.name:payload.value})
    return {'ok':True,'name':payload.name}

@app.put('/api/custom-variables/{name}')
async def update_custom_variable(name:str,payload:VariableIn):
    if name!=payload.name:raise HTTPException(400,'Cannot rename variable')
    try:http.validate_name(name)
    except ValueError as e:raise HTTPException(400,str(e)) from e
    async with state.connection() as conn:
        exists=await (await conn.execute("SELECT 1 FROM object_properties WHERE object_name='variables' AND property_name=?",(name,))).fetchone()
    if not exists:raise HTTPException(404,'Unknown variable')
    await state.update_properties('variables',{name:payload.value})
    return {'ok':True}

@app.delete('/api/custom-variables/{name}')
async def delete_custom_variable(name:str):
    async with state.connection() as conn:
        cur=await conn.execute("DELETE FROM object_properties WHERE object_name='variables' AND property_name=?",(name,))
        if cur.rowcount==0:raise HTTPException(404,'Unknown variable')
        await conn.commit()
    return {'ok':True}

class ActionIn(BaseModel):
    name:str
    url:str
    body:Any=Field(default_factory=dict)
    enabled:bool=False
    timeout_seconds:float=10

def validate_action(payload:ActionIn):
    try:
        http.validate_name(payload.name)
        http.validate_endpoint(payload.url)
        if not .5<=payload.timeout_seconds<=60:raise ValueError('Timeout must be 0.5..60 seconds')
        if len(json.dumps(payload.body))>65536:raise ValueError('POST body exceeds 64 KiB')
    except ValueError as e:raise HTTPException(400,str(e)) from e
@app.get('/api/actions')
async def list_actions():
    async with state.connection() as conn:
        rows=await (await conn.execute('SELECT * FROM http_actions ORDER BY name')).fetchall()
    return [{**dict(r),'body':json.loads(r['body_json'])} for r in rows]
@app.post('/api/actions')
async def create_action(payload:ActionIn):
    validate_action(payload)
    async with state.connection() as conn:
        try:
            await conn.execute('INSERT INTO http_actions(name,url,body_json,enabled,auth_env,auth_header,auth_prefix,timeout_seconds) VALUES(?,?,?,?,?,?,?,?)',(payload.name,payload.url,json.dumps(payload.body),int(payload.enabled),'','X-API-Key','',payload.timeout_seconds))
            await conn.commit()
        except Exception as e:
            await conn.rollback()
            if 'UNIQUE constraint' in str(e):raise HTTPException(409,'Action exists') from e
            raise
    return {'ok':True}
@app.put('/api/actions/{name}')
async def update_action(name:str,payload:ActionIn):
    if name!=payload.name:raise HTTPException(400,'Cannot rename action')
    validate_action(payload)
    async with state.connection() as conn:
        cur=await conn.execute('UPDATE http_actions SET url=?,body_json=?,enabled=?,auth_env=?,auth_header=?,auth_prefix=?,timeout_seconds=? WHERE name=?',(payload.url,json.dumps(payload.body),int(payload.enabled),'','X-API-Key','',payload.timeout_seconds,name))
        if cur.rowcount==0:raise HTTPException(404,'Unknown action')
        await conn.commit()
    return {'ok':True}
@app.delete('/api/actions/{name}')
async def delete_action(name:str):
    async with state.connection() as conn:
        cur=await conn.execute('DELETE FROM http_actions WHERE name=?',(name,))
        if cur.rowcount==0:raise HTTPException(404,'Unknown action')
        await conn.commit()
    return {'ok':True}

class RuleIn(BaseModel):
    name:str
    enabled:bool=True
    priority:int=100
    condition:dict[str,Any]
    actions:list[dict[str,Any]]=Field(default_factory=list)
    cooldown_seconds:int=0
    stop_processing:bool=False
async def verify_rule(payload:RuleIn):
    if not payload.name.strip() or not 0<=payload.cooldown_seconds<=31536000:raise HTTPException(400,'Invalid rule name/cooldown')
    catalog=await modules.catalog()
    for action in payload.actions:
        if action.get('method') not in catalog or not isinstance(action.get('arguments',{}),dict):raise HTTPException(400,'Unknown action or malformed arguments')
@app.get('/api/global-rules')
async def list_rules():
    async with state.connection() as conn:
        rows=await (await conn.execute('SELECT * FROM global_rules ORDER BY priority,id')).fetchall()
    return [{**dict(r),'condition':json.loads(r['condition_json']),'actions':json.loads(r['actions_json'])} for r in rows]
@app.post('/api/global-rules')
async def create_rule(payload:RuleIn):
    await verify_rule(payload)
    async with state.connection() as conn:
        cur=await conn.execute('INSERT INTO global_rules(name,enabled,priority,condition_json,actions_json,cooldown_seconds,stop_processing) VALUES(?,?,?,?,?,?,?)',(payload.name,int(payload.enabled),payload.priority,json.dumps(payload.condition),json.dumps(payload.actions),payload.cooldown_seconds,int(payload.stop_processing)))
        await conn.commit()
    return {'id':cur.lastrowid}
@app.put('/api/global-rules/{id}')
async def update_rule(id:int,payload:RuleIn):
    await verify_rule(payload)
    async with state.connection() as conn:
        cur=await conn.execute('UPDATE global_rules SET name=?,enabled=?,priority=?,condition_json=?,actions_json=?,cooldown_seconds=?,stop_processing=? WHERE id=?',(payload.name,int(payload.enabled),payload.priority,json.dumps(payload.condition),json.dumps(payload.actions),payload.cooldown_seconds,int(payload.stop_processing),id))
        if cur.rowcount==0:raise HTTPException(404,'Unknown rule')
        await conn.commit()
    return {'ok':True}
@app.delete('/api/global-rules/{id}')
async def delete_rule(id:int):
    async with state.connection() as conn:
        cur=await conn.execute('DELETE FROM global_rules WHERE id=?',(id,))
        if cur.rowcount==0:raise HTTPException(404,'Unknown rule')
        await conn.commit()
    return {'ok':True}
@app.post('/api/global-rules/evaluate')
async def evaluate_now():return await engine.evaluate_once()
@app.get('/api/events')
async def events(limit:int=100):
    async with state.connection() as conn:
        rows=await (await conn.execute('SELECT * FROM event_log ORDER BY id DESC LIMIT ?', (max(1,min(limit,500)),))).fetchall()
    return [dict(r) for r in rows]