"""Runtime action registry for configured jAPI actions and built-in variables."""
import json
import os
import re
from . import state
from .http_services import invoke_action, render_body, validate_name
from .rules import apply_transform, resolve_template_value

_ARG_RE=re.compile(r'\{\{\s*args\.([A-Za-z][A-Za-z0-9_-]*)\s*\}\}')
def _action_arguments(body_json):
    try:text=json.dumps(json.loads(body_json),separators=(',',':'))
    except Exception:text=str(body_json)
    return sorted(set(_ARG_RE.findall(text)))

async def catalog():
    async with state.connection() as conn:
        rows=await (await conn.execute('SELECT name,url,enabled,body_json FROM http_actions ORDER BY name')).fetchall()
    result={r['name']:{'enabled':bool(r['enabled']),'kind':'japi.post','url':r['url'],'body_json':r['body_json'],'arguments':_action_arguments(r['body_json'])} for r in rows}
    result['variables.set']={
        'enabled':True,
        'kind':'builtin',
        'description':'Store/update a custom variable in the global pool',
        'arguments':{'name':'variable name','value':'JSON value or template'}
    }
    return result

def resolve_action_argument(value,context):
    """Resolve structured rule parameters while preserving literal JSON values."""
    if isinstance(value,dict) and value.get("kind")=="parameter" and "expression" in value:
        expr=value["expression"]
        if isinstance(expr,str):
            return resolve_template_value(expr,context)
        if not isinstance(expr,dict) or not expr.get("source"):
            raise ValueError("Malformed action parameter expression")
        resolved=resolve_template_value(str(expr["source"]),context)
        for transform in expr.get("transforms") or []:
            resolved=apply_transform(resolved,transform)
        return resolved
    if isinstance(value,list):
        return [resolve_action_argument(v,context) for v in value]
    if isinstance(value,dict):
        return {k:resolve_action_argument(v,context) for k,v in value.items()}
    # Backward compatibility for rules saved before structured action parameters.
    if isinstance(value,str) and re.fullmatch(r'\{\{\s*[A-Za-z0-9_.-]+\s*\}\}',value):
        return render_body(value,context)
    return value


async def invoke(name,target,arguments,context=None):
    if os.getenv('JRULE_ENABLE_ACTIONS','0')!='1':
        raise PermissionError('Actions disabled (JRULE_ENABLE_ACTIONS=0)')
    context=context or {}
    resolved_arguments=resolve_action_argument(arguments,context)
    if name=='variables.set':
        if not isinstance(resolved_arguments,dict) or 'name' not in resolved_arguments or 'value' not in resolved_arguments:
            raise ValueError('variables.set requires name and value')
        var_name=validate_name(str(resolved_arguments['name']))
        await state.update_properties('variables',{var_name:resolved_arguments['value']})
        return
    await invoke_action(name,resolved_arguments,context)
