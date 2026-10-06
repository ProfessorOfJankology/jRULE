"""Runtime action registry populated from configured jAPI actions."""
import os
from . import state
from .http_services import invoke_action

async def catalog():
    async with state.connection() as conn:
        rows=await (await conn.execute('SELECT name,url,enabled,body_json FROM http_actions ORDER BY name')).fetchall()
    return {r['name']:{'enabled':bool(r['enabled']),'url':r['url'],'body_json':r['body_json']} for r in rows}

async def invoke(name,target,arguments,context=None):
    if os.getenv('JRULE_ENABLE_ACTIONS','0')!='1':raise PermissionError('Actions disabled (JRULE_ENABLE_ACTIONS=0)')
    await invoke_action(name,arguments,context or {})