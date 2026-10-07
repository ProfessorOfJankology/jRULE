"""Runtime action registry for configured jAPI actions and built-in variables."""
import os
from . import state
from .http_services import invoke_action, render_body, validate_name

async def catalog():
    async with state.connection() as conn:
        rows=await (await conn.execute('SELECT name,url,enabled,body_json FROM http_actions ORDER BY name')).fetchall()
    result={r['name']:{'enabled':bool(r['enabled']),'kind':'japi.post','url':r['url'],'body_json':r['body_json']} for r in rows}
    result['variables.set']={
        'enabled':True,
        'kind':'builtin',
        'description':'Store/update a custom variable in the global pool',
        'arguments':{'name':'variable name','value':'JSON value or template'}
    }
    return result

async def invoke(name,target,arguments,context=None):
    if os.getenv('JRULE_ENABLE_ACTIONS','0')!='1':
        raise PermissionError('Actions disabled (JRULE_ENABLE_ACTIONS=0)')
    context=context or {}
    if name=='variables.set':
        if not isinstance(arguments,dict) or 'name' not in arguments or 'value' not in arguments:
            raise ValueError('variables.set requires name and value')
        var_name=validate_name(str(arguments['name']))
        rendered=render_body(arguments['value'],context)
        await state.update_properties('variables',{var_name:rendered})
        return
    await invoke_action(name,arguments,context)
