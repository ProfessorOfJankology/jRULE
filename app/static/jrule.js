"use strict";
const $=s=>document.querySelector(s);
let sources=[],actions=[],rules=[],variables=[],actionCatalog={},ruleParameters=[],edit={source:null,action:null,rule:null,variable:null};

function el(t,text,cls){const e=document.createElement(t);if(text!==undefined&&text!==null)e.textContent=String(text);if(cls)e.className=cls;return e;}
function notice(s){$('#notice').textContent=s;$('#notice').classList.add('visible');}
async function api(url,method='GET',body){
  const headers={};
  if(method!=='GET'){headers['Content-Type']='application/json';headers['X-JRULE-Admin-Token']=$('#admin-token').value;}
  const r=await fetch(url,{method,headers,body:body===undefined?undefined:JSON.stringify(body)});
  const v=await r.json().catch(()=>null);
  if(!r.ok)throw Error(v?.detail||r.statusText);
  return v;
}
const j=v=>JSON.stringify(v);

function shortValue(v){
  const s=j(v);
  return s===undefined?'':(s.length>140?s.slice(0,137)+'…':s);
}
function suggestedProperty(path){
  const parts=path.split('.').slice(-2);
  let value=parts.join('_').replace(/[^A-Za-z0-9_-]/g,'_');
  if(!/^[A-Za-z]/.test(value))value='field_'+value;
  return value.slice(0,64);
}
function openDerived(source,field){
  const form=$('#derived-form');
  form.reset();
  form.elements.source.value=source;
  form.elements.source_display.value=source;
  form.elements.name.value=suggestedProperty(field.path);
  form.elements.path.value=field.path;
  form.elements.op.value='value';
  form.elements.default.value='null';
  $('#derived-edit').hidden=false;
  $('#derived-edit').scrollIntoView({behavior:'smooth'});
}
async function objCard(name,info){
  const c=el('div',null,'panel source-state');
  const title=el('div',null,'bar');
  const left=el('div');
  left.append(el('h3',name),el('p',`${info.module} / ${info.type}`,'muted'));
  title.append(left);
  if(info.source_meta){
    const sm=info.source_meta;
    const meta=el('p',`Poll #${sm.poll_sequence} · checks since poll: ${sm.checks_since_poll} · last poll: ${sm.last_poll||'never'}`,'muted');
    left.append(meta);
  }
  c.append(title);

  const table=el('table',null,'parameter-table'),head=el('tr');
  for(const heading of ['Rule parameter','Current','Previous','Last changed'])head.append(el('th',heading));
  table.append(head);
  for(const [prop,p] of Object.entries(info.properties)){
    const tr=el('tr');
    const param=el('code',`current.${name}.${prop}`);
    const td=el('td');td.append(param);tr.append(td);
    for(const v of [shortValue(p.current),shortValue(p.last),p.last_changed||''])tr.append(el('td',v));
    table.append(tr);
  }
  if(!Object.keys(info.properties).length){
    const tr=el('tr'),td=el('td','No rule parameters yet. Poll the source first.','hint');td.colSpan=4;tr.append(td);table.append(tr);
  }
  c.append(table);

  if(info.module==='japi.get'){
    try{
      const discovered=await api('/api/sources/'+encodeURIComponent(name)+'/fields');
      const details=document.createElement('details');
      const summary=document.createElement('summary');
      summary.textContent=`Available source fields (${discovered.fields.length})`;
      details.append(summary);
      const ftable=el('table',null,'field-table'),fh=el('tr');
      for(const heading of ['Observed path','Last observed value','Type','Status','Last seen',''])fh.append(el('th',heading));
      ftable.append(fh);
      for(const field of discovered.fields){
        const tr=el('tr'),pathTd=el('td'),code=el('code',field.path);
        pathTd.append(code);tr.append(pathTd,el('td',shortValue(field.value)),el('td',field.type));
        const status=el('span',field.present?'Present now':'Not in latest poll',field.present?'tag field-present':'tag field-absent');
        const statusTd=el('td');statusTd.append(status);tr.append(statusTd,el('td',field.last_seen||''));
        const actionTd=el('td'),button=el('button','Add property');
        button.type='button';button.onclick=()=>openDerived(name,field);actionTd.append(button);
        if(!field.present){
          const forget=el('button','Forget');forget.type='button';forget.className='danger';
          forget.onclick=async()=>{
            if(!confirm('Forget historical field '+field.path+'?'))return;
            try{
              await api('/api/sources/'+encodeURIComponent(name)+'/fields?path='+encodeURIComponent(field.path),'DELETE');
              notice('Forgot '+field.path);await pool();await refreshRules();
            }catch(e){notice(e.message)}
          };
          actionTd.append(forget);
        }
        tr.append(actionTd);
        ftable.append(tr);
      }
      if(!discovered.fields.length){
        const tr=el('tr'),td=el('td','No observed fields yet. Poll this source first.','hint');td.colSpan=6;tr.append(td);ftable.append(tr);
      }
      details.append(ftable);c.append(details);
    }catch(e){
      c.append(el('p','Could not load discovered fields: '+e.message,'hint'));
    }
  }
  return c;
}
async function pool(){
  const root=$('#objects');root.replaceChildren();
  const data=await api('/api/objects');
  for(const [name,info] of Object.entries(data))root.append(await objCard(name,info));
  if(!root.children.length)root.append(el('p','No properties yet. Configure a jAPI source and poll it.','hint'));
}

function actionButtons(row,onEdit,onDelete,extra){
  const bar=el('div',null,'buttons');
  for(const [name,fn] of [['Edit',onEdit],...(extra?[['Poll',extra]]:[]),['Delete',onDelete]]){
    const b=el('button',name);b.onclick=fn;bar.append(b);
  }
  row.append(bar);
}
function renderList(id,items,onEdit,onDelete,onExtra){
  const root=$(id);root.replaceChildren();
  for(const obj of items){
    const row=el('div',null,'panel rule'),left=el('div');
    left.append(el('strong',obj.name),el('p',obj.url||`Priority ${obj.priority} · ${obj.enabled?'Enabled':'Disabled'}`,'muted'));
    row.append(left);
    actionButtons(row,()=>onEdit(obj),()=>onDelete(obj),onExtra?()=>onExtra(obj):null);
    root.append(row);
  }
  if(!items.length)root.append(el('p','Nothing configured yet.','hint'));
}

async function refreshSources(){
  sources=await api('/api/sources');
  renderList('#source-list',sources,o=>openForm('source',o),o=>remove('sources',o.name,refreshSources),async o=>{
    try{
      const v=await api('/api/sources/'+encodeURIComponent(o.name)+'/poll','POST',{});
      notice('Updated '+v.updated.join(', '));await pool();await refreshSources();
    }catch(e){notice(e.message);await refreshSources();}
  });
}
async function refreshActions(){
  actions=await api('/api/actions');
  renderList('#action-list',actions,o=>openForm('action',o),o=>remove('actions',o.name,refreshActions));
  actionCatalog=await api('/api/modules/actions');
  $('#action-hint').textContent='Available actions: '+Object.keys(actionCatalog).join(', ');
}
async function duplicateRule(rule){
  const copy={
    name:'Copy of '+rule.name,
    enabled:false,
    priority:rule.priority,
    cooldown_seconds:rule.cooldown_seconds,
    stop_processing:!!rule.stop_processing,
    condition:rule.condition,
    actions:rule.actions
  };
  try{
    await api('/api/global-rules','POST',copy);
    await refreshRules();
    notice('Duplicated rule as '+copy.name+' (disabled)');
  }catch(err){notice(err?.message||String(err))}
}
async function forceRunRule(rule){
  if(!confirm('Run saved actions for '+rule.name+' regardless of conditions?'))return;
  try{
    const result=await api('/api/global-rules/'+rule.id+'/run','POST');
    notice('Ran '+result.actions_executed+' actions');
    await refreshLogs();
  }catch(err){notice(err.message);}
}
async function refreshRules(){
  [rules,ruleParameters]=await Promise.all([api('/api/global-rules'),api('/api/global-variables')]);
  const root=$('#rules-list');root.replaceChildren();
  for(const rule of rules){
    const row=el('div',null,'panel rule'),left=el('div');
    left.append(el('strong',rule.name),el('p',`Priority ${rule.priority} · ${rule.enabled?'Enabled':'Disabled'}`,'muted'));
    row.append(left);
    const buttons=el('div',null,'buttons');
    for(const [label,fn] of [
      ['Run',()=>forceRunRule(rule)],
       ['Edit',()=>openRule(rule)],
      ['Duplicate',()=>duplicateRule(rule)],
      ['Delete',()=>remove('global-rules',rule.id,refreshRules)]
    ]){
      const b=el('button',label);b.type='button';b.onclick=fn;buttons.append(b);
    }
    row.append(buttons);root.append(row);
  }
  if(!rules.length)root.append(el('p','Nothing configured yet.','hint'));
}
async function refreshVariables(){
  variables=await api('/api/custom-variables');
  const root=$('#variable-list');root.replaceChildren();
  for(const v of variables){
    const row=el('div',null,'panel rule'),left=el('div');
    left.append(el('strong',v.name),el('p','Current: '+j(v.current)+' · Last: '+j(v.last),'muted'));
    row.append(left);
    actionButtons(row,()=>openVariable(v),()=>remove('custom-variables',v.name,refreshVariables));
    root.append(row);
  }
  if(!variables.length)root.append(el('p','No custom variables yet.','hint'));
}
async function remove(type,id,refresh){
  if(!confirm('Delete '+id+'?'))return;
  try{
    await api('/api/'+type+'/'+encodeURIComponent(id),'DELETE');
    await refresh();
    if(type==='sources'||type==='custom-variables')await pool();
  }catch(e){notice(e.message)}
}

function parseLooseJson(text,defaultValue=null){
  const s=String(text??'').trim();
  if(!s)return defaultValue;
  try{return JSON.parse(s)}catch{return s}
}
function mappingSpecFromRow(row){
  const name=row.querySelector('[data-map="name"]').value.trim();
  if(!name)return null;
  const path=row.querySelector('[data-map="path"]').value.trim();
  const op=row.querySelector('[data-map="op"]').value;
  const spec={path,op,default:parseLooseJson(row.querySelector('[data-map="default"]').value,null)};
  if(op==='contains'||op==='equals')spec.value=parseLooseJson(row.querySelector('[data-map="value"]').value,null);
  return [name,spec];
}
function syncMappingText(){
  const mapping={};
  for(const row of $('#mapping-builder').querySelectorAll('.mapping-row')){
    const item=mappingSpecFromRow(row);if(item)mapping[item[0]]=item[1];
  }
  $('#source-form').elements.mapping.value=JSON.stringify(mapping,null,2);
}
function addMappingRow(name='',spec={path:'',op:'value',default:null}){
  if(typeof spec==='string')spec={path:spec,op:'value',default:null};
  const row=el('div',null,'grid mapping-row');
  const mk=(label,role,value='')=>{const l=el('label',label),i=document.createElement('input');i.dataset.map=role;i.value=value??'';l.append(i);return l;};
  row.append(mk('Property name','name',name),mk('JSON path','path',spec.path||''));
  const opLabel=el('label','Derive as'),sel=document.createElement('select');sel.dataset.map='op';
  for(const [value,label] of [['value','Value'],['first','First item'],['count','Count'],['exists','Exists'],['contains','Contains'],['equals','Equals']]){
    const o=document.createElement('option');o.value=value;o.textContent=label;sel.append(o);
  }
  sel.value=spec.op||'value';opLabel.append(sel);row.append(opLabel);
  row.append(mk('Compare/value','value',spec.value===undefined?'':j(spec.value)));
  row.append(mk('Default if missing','default',spec.default===undefined?'null':j(spec.default)));
  const remove=el('button','Remove');remove.type='button';remove.onclick=()=>{row.remove();syncMappingText();};row.append(remove);
  for(const input of row.querySelectorAll('input,select'))input.addEventListener('input',syncMappingText);
  $('#mapping-builder').append(row);
}
function renderMapping(mapping){
  $('#mapping-builder').replaceChildren();
  for(const [name,spec] of Object.entries(mapping||{}))addMappingRow(name,spec);
  $('#source-form').elements.mapping.value=JSON.stringify(mapping||{},null,2);
}

function openForm(type,obj){
  edit[type]=obj?.name||null;
  const form=$('#'+type+'-form');form.reset();
  for(const input of form.elements){
    if(!input.name)continue;
    if(input.name==='mapping'||input.name==='body')input.value=JSON.stringify(obj?.[input.name]||{},null,2);
    else if(input.type==='checkbox')input.checked=obj?!!obj[input.name]:(type==='source');
    else if(obj && Object.hasOwn(obj,input.name))input.value=obj[input.name];
  }
  form.elements.name.readOnly=!!obj;
  $('#'+type+'-edit').hidden=false;
  $('#'+type+'-edit').scrollIntoView({behavior:'smooth'});
}
async function saveForm(type,e){
  e.preventDefault();
  try{
    const data={};
    for(const field of e.target.elements){
      if(!field.name)continue;
      if(field.name==='mapping'||field.name==='body'){
        const raw=field.value.trim();
        try{data[field.name]=raw?JSON.parse(raw):{};}
        catch(parseError){
          const label=field.name==='body'?'POST body':'Property mapping';
          throw new Error(label+' must be valid JSON: '+parseError.message);
        }
      }else if(field.type==='checkbox')data[field.name]=field.checked;
      else if(field.type==='number')data[field.name]=Number(field.value);
      else data[field.name]=field.value;
    }
    await api('/api/'+type+'s'+(edit[type]?'/'+encodeURIComponent(edit[type]):''),edit[type]?'PUT':'POST',data);
    $('#'+type+'-edit').hidden=true;
    await(type==='source'?refreshSources():refreshActions());
    notice('Saved '+type);
  }catch(err){
    notice(err?.message||String(err));
  }
}

function openVariable(v){
  edit.variable=v?.name||null;
  const form=$('#variable-form');form.reset();
  form.elements.name.value=v?.name||'';
  form.elements.name.readOnly=!!v;
  form.elements.value.value=j(v?.current??null);
  $('#variable-edit').hidden=false;$('#variable-edit').scrollIntoView({behavior:'smooth'});
}
async function saveVariable(e){
  e.preventDefault();
  const data={name:e.target.elements.name.value,value:parseLooseJson(e.target.elements.value.value,null)};
  try{
    await api('/api/custom-variables'+(edit.variable?'/'+encodeURIComponent(edit.variable):''),edit.variable?'PUT':'POST',data);
    $('#variable-edit').hidden=true;await refreshVariables();await pool();notice('Variable saved');
  }catch(err){notice(err.message)}
}

async function saveDerived(e){
  e.preventDefault();
  const form=e.target;
  const source=form.elements.source.value;
  const payload={
    name:form.elements.name.value.trim(),
    path:form.elements.path.value,
    op:form.elements.op.value,
    value:parseLooseJson(form.elements.value.value,null),
    default:parseLooseJson(form.elements.default.value,null)
  };
  try{
    const result=await api('/api/sources/'+encodeURIComponent(source)+'/derived-fields','POST',payload);
    $('#derived-edit').hidden=true;
    await Promise.all([pool(),refreshSources()]);
    notice(result.poll_error?'Property saved; poll failed: '+result.poll_error:'Derived property added');
  }catch(err){notice(err.message)}
}


const OPERATOR_GROUPS=[
  ['Compare',[['eq','is'],['ne','is not'],['gt','is greater than'],['gte','is at least'],['lt','is less than'],['lte','is at most']]],
  ['Collection / text',[['contains','contains'],['not_contains','does not contain'],['in','is in'],['not_in','is not in']]],
  ['State',[['exists','exists'],['not_exists','does not exist'],['changed','has changed'],['changed_to','changed to'],['changed_from','changed from']]]
];
const NO_RIGHT=new Set(['exists','not_exists','changed']);
const RANGE_RIGHT=new Set();
const TRANSFORMS=[
  ['count','Count',false],['first','First item',false],['last','Last item',false],
  ['key','Dictionary key',true],['index','List index',true],
  ['as_number','As number',false],['as_string','As text',false],['as_boolean','As true/false',false],
  ['as_date','As date',false],['as_time','As time',false],
  ['lowercase','Lowercase',false],['uppercase','Uppercase',false]
];

function prettyParameter(p){
  const parts=p.split('.');
  if(parts[0]==='meta' && parts.length===3){
    const labels={checks_since_poll:'checks since poll',poll_sequence:'poll sequence',last_poll:'last poll'};
    return parts[1]+' · '+(labels[parts[2]]||parts[2]);
  }
  if(parts[0]==='current' && parts.length>=3)
    return parts[1]+' · '+parts.slice(2).join('.');
  if(parts[0]==='meta' && parts.length>=4)
    return parts[1]+' · '+parts.slice(2,-1).join('.')+' · '+parts.at(-1);
  return p;
}
function canonicalParameter(p){
  return p.startsWith('previous.')?'current.'+p.slice(9):p;
}
function parameterSelect(value=''){
  const s=document.createElement('select');
  const canonicalValue=canonicalParameter(value);
  const values=[...new Set(ruleParameters.map(canonicalParameter))];
  const groups=new Map();
  for(const p of values){
    const parts=p.split('.');
    const group=parts.length>1?parts[1]:'Other';
    if(!groups.has(group))groups.set(group,[]);
    groups.get(group).push(p);
  }
  for(const [group,items] of groups){
    const og=document.createElement('optgroup');og.label=group;
    for(const p of items){const o=document.createElement('option');o.value=p;o.textContent=prettyParameter(p);if(p===canonicalValue)o.selected=true;og.append(o);}
    s.append(og);
  }
  const custom=document.createElement('option');custom.value='__custom__';custom.textContent='Custom path…';s.append(custom);
  if(canonicalValue && !values.includes(canonicalValue)){
    custom.selected=true;
    s.dataset.customValue=canonicalValue;
  }
  return s;
}
function parameterReferenceEditor(value=''){
  const box=el('div',null,'parameter-reference');
  const source=parameterSelect(value);source.className='parameter-source';
  const custom=document.createElement('input');custom.className='parameter-custom';
  custom.placeholder='Presence.workstation_users.ESMC-DAN';
  let initial=value||'';
  if(initial.startsWith('current.'))initial=initial.slice(8);
  else if(initial.startsWith('previous.'))initial=initial.slice(9);
  else if(initial.startsWith('meta.'))initial=value;
  custom.value=source.value==='__custom__'?(source.dataset.customValue?canonicalParameter(source.dataset.customValue).replace(/^current\./,''):initial):'';
  const state=document.createElement('select');state.className='parameter-state';
  for(const pair of [['current','Current'],['previous','Previous']]){
    const o=document.createElement('option');o.value=pair[0];o.textContent=pair[1];state.append(o);
  }
  state.value=value.startsWith('previous.')?'previous':'current';
  const update=()=>{
    const isCustom=source.value==='__custom__';
    custom.hidden=!isCustom;
    const raw=isCustom?custom.value.trim():source.value;
    state.hidden=raw.startsWith('meta.');
  };
  source.onchange=update;custom.oninput=update;update();box.append(source,custom,state);return box;
}
function parameterReferenceValue(box){
  const source=box.querySelector('.parameter-source');
  const custom=box.querySelector('.parameter-custom');
  const state=box.querySelector('.parameter-state');
  if(source.value!=='__custom__'){
    let value=source.value;
    if(value.startsWith('current.') && state && state.value==='previous')value='previous.'+value.slice(8);
    return value;
  }
  let raw=custom.value.trim();
  if(!raw)return '';
  if(raw.startsWith('current.')||raw.startsWith('previous.')||raw.startsWith('meta.'))return raw;
  return (state&&state.value==='previous'?'previous.':'current.')+raw;
}
function normalizeExpression(expr){
  if(typeof expr==='string')return {source:expr,transforms:[]};
  if(expr&&typeof expr==='object'&&expr.source)return {source:expr.source,transforms:expr.transforms||[]};
  return {source:ruleParameters.find(v=>v.startsWith('current.'))||'',transforms:[]};
}
function addTransformChip(holder,transform={op:'count'}){
  const chip=el('div',null,'transform-chip');
  const select=document.createElement('select');select.className='transform-op';
  for(const entry of TRANSFORMS){
    const o=document.createElement('option');o.value=entry[0];o.textContent=entry[1];if(entry[0]===transform.op)o.selected=true;select.append(o);
  }
  const arg=document.createElement('input');arg.className='transform-arg';arg.placeholder='Key / index';arg.value=transform.arg??'';
  const remove=el('button','×');remove.type='button';remove.className='transform-remove';remove.onclick=()=>chip.remove();
  const update=()=>{
    const spec=TRANSFORMS.find(x=>x[0]===select.value);
    arg.hidden=!(spec&&spec[2]);
  };
  select.onchange=update;update();chip.append(el('span','→','muted'),select,arg,remove);holder.append(chip);
}
function expressionEditor(expr){
  const normalized=normalizeExpression(expr);
  const box=el('div',null,'expression-editor');
  const source=parameterReferenceEditor(normalized.source);source.classList.add('expression-source');box.append(source);
  const transforms=el('div',null,'transform-list');box.append(transforms);
  for(const t of normalized.transforms)addTransformChip(transforms,t);
  const add=el('button','Transform');add.type='button';add.className='transform-add';add.onclick=()=>addTransformChip(transforms,{op:'count'});
  box.append(add);return box;
}
function expressionFromEditor(box){
  const source=parameterReferenceValue(box.querySelector('.expression-source'));
  const transforms=[...box.querySelectorAll('.transform-chip')].map(chip=>{
    const op=chip.querySelector('.transform-op').value;
    const result={op:op};
    const arg=chip.querySelector('.transform-arg');
    if(!arg.hidden)result.arg=parseLooseJson(arg.value,arg.value);
    return result;
  });
  return transforms.length?{source:source,transforms:transforms}:source;
}

function operatorSelect(value='eq'){
  const s=document.createElement('select');
  for(const [group,ops] of OPERATOR_GROUPS){
    const og=document.createElement('optgroup');og.label=group;
    for(const [v,label] of ops){const o=document.createElement('option');o.value=v;o.textContent=label;if(v===value)o.selected=true;og.append(o);}
    s.append(og);
  }
  return s;
}
function literalInput(value,cls='condition-value'){
  const i=document.createElement('input');i.className=cls;
  i.value=value===undefined||value===null?(value===null?'null':''):(typeof value==='string'?value:j(value));
  return i;
}
function renderConditionRight(row,node={}){
  const slot=row.querySelector('.condition-right');slot.replaceChildren();
  const op=row.querySelector('.condition-operator').value;
  if(NO_RIGHT.has(op)){slot.append(el('span','', 'muted'));return;}
  if(RANGE_RIGHT.has(op)){
    const values=Array.isArray(node.right)?node.right:['',''];
    const a=literalInput(values[0],'range-a'),b=literalInput(values[1],'range-b');
    a.placeholder='From';b.placeholder='To';slot.append(a,el('span','and','muted'),b);return;
  }
  const mode=document.createElement('select');mode.className='right-mode';
  for(const pair of [['literal','value'],['variable','parameter']]){const o=document.createElement('option');o.value=pair[0];o.textContent=pair[1];mode.append(o);}
  mode.value=node.right_type==='variable'?'variable':'literal';
  const holder=el('span',null,'right-value-holder');
  const draw=()=>{
    holder.replaceChildren();
    if(mode.value==='variable'){
      const ps=parameterReferenceEditor(node.right_type==='variable'?String(node.right||''):'');ps.classList.add('right-variable');holder.append(ps);
    }else holder.append(literalInput(node.right_type==='variable'?'':node.right,'condition-value'));
  };
  mode.onchange=draw;draw();slot.append(mode,holder);
}
function addCondition(group,node={}){
  const row=el('div',null,'condition-row');row.dataset.kind='condition';
  const left=expressionEditor(node.left||ruleParameters.find(v=>v.startsWith('current.'))||'');left.classList.add('condition-left-expression');
  const op=operatorSelect(node.operator||'eq');op.className='condition-operator';
  const right=el('div',null,'condition-right');
  const remove=el('button','Remove');remove.type='button';remove.className='danger';remove.onclick=()=>row.remove();
  row.append(left,op,right,remove);
  group.querySelector(':scope > .group-items').append(row);
  op.onchange=()=>renderConditionRight(row,{});
  renderConditionRight(row,node);
  return row;
}
function addConditionGroup(parent,node={kind:'group',logic:'all',items:[]},root=false){
  const g=el('div',null,'condition-group');g.dataset.kind='group';
  const head=el('div',null,'group-head');
  const logic=document.createElement('select');logic.className='group-logic';
  for(const pair of [['all','All of these are true'],['any','Any of these are true']]){const o=document.createElement('option');o.value=pair[0];o.textContent=pair[1];logic.append(o);}
  logic.value=node.logic||'all';
  const add=el('button','Add condition');add.type='button';
  const addGroup=el('button','Add group');addGroup.type='button';
  head.append(logic,add,addGroup);
  if(!root){const remove=el('button','Remove group');remove.type='button';remove.className='danger';remove.onclick=()=>g.remove();head.append(remove);}
  const items=el('div',null,'group-items');g.append(head,items);
  (parent||$('#rule-condition-builder')).append(g);
  add.onclick=()=>addCondition(g,{});
  addGroup.onclick=()=>addConditionGroup(items,{kind:'group',logic:'any',items:[{kind:'condition'}]},false);
  const children=node.items||[];
  for(const child of children){if(child.kind==='group')addConditionGroup(items,child,false);else addCondition(g,child);}
  if(!children.length)addCondition(g,{});
  return g;
}
function conditionFromRow(row){
  const left=expressionFromEditor(row.querySelector('.condition-left-expression')),operator=row.querySelector('.condition-operator').value;
  const out={kind:'condition',left:left,operator:operator};
  if(NO_RIGHT.has(operator))return out;
  if(RANGE_RIGHT.has(operator)){
    out.right=[parseLooseJson(row.querySelector('.range-a').value,''),parseLooseJson(row.querySelector('.range-b').value,'')];return out;
  }
  const mode=row.querySelector('.right-mode').value;
  if(mode==='variable'){out.right_type='variable';out.right=parameterReferenceValue(row.querySelector('.right-variable'));}
  else out.right=parseLooseJson(row.querySelector('.condition-value').value,'');
  return out;
}
function conditionFromGroup(group){
  const items=[];
  for(const child of group.querySelector(':scope > .group-items').children){items.push(child.dataset.kind==='group'?conditionFromGroup(child):conditionFromRow(child));}
  return {kind:'group',logic:group.querySelector(':scope > .group-head > .group-logic').value,items:items};
}
function renderConditionBuilder(condition){
  const root=$('#rule-condition-builder');root.replaceChildren();
  const node=condition&&condition.kind==='group'?condition:{kind:'group',logic:'all',items:condition?[condition]:[]};
  addConditionGroup(root,node,true);
}
function actionArgumentNames(method){
  const args=actionCatalog[method]?.arguments;
  return Array.isArray(args)?args:Object.keys(args||{});
}
function actionArgumentMode(raw){
  if(raw&&typeof raw==='object'&&raw.kind==='parameter'&&raw.expression)return 'parameter';
  if(raw&&typeof raw==='object'&&raw.kind==='literal'&&typeof raw.value==='string')return 'literal';
  if(typeof raw==='string'&&/^\{\{\s*[A-Za-z0-9_.-]+\s*\}\}$/.test(raw))return 'parameter';
  if(raw===null)return 'null';
  if(typeof raw==='boolean')return 'boolean';
  if(typeof raw==='number')return 'number';
  if(typeof raw==='string')return 'string';
  return 'json';
}
function actionArgumentValue(raw,mode){
  if(mode==='literal')return raw.value;
  if(mode==='parameter')return raw&&raw.kind==='parameter'?raw.expression:String(raw||'').replace(/^\{\{\s*|\s*\}\}$/g,'');
  return raw===undefined?'':raw;
}
function parseActionArgument(mode,value){
  switch(mode){
    case 'string':return value;
    case 'literal':return {kind:'literal',value:value};
    case 'number':{
      if(!value.trim()||!Number.isFinite(Number(value)))throw Error('Number must be finite');
      return Number(value);
    }
    case 'boolean':
      if(value!=='true'&&value!=='false')throw Error('Boolean must be true or false');
      return value==='true';
    case 'null':return null;
    case 'json':
      try{return JSON.parse(value)}catch{throw Error('Invalid JSON argument')}
    default:throw Error('Unknown argument type: '+mode);
  }
}
function renderActionArguments(row,item={}){
  const holder=row.querySelector('.action-arguments');holder.replaceChildren();
  const method=row.querySelector('.action-method').value;
  const existing=item.method===method?(item.arguments||{}):{};
  for(const name of actionArgumentNames(method)){
    const box=el('div',null,'action-arg');box.dataset.name=name;
    const label=el('label',name);
    const mode=document.createElement('select');mode.className='arg-mode';
    for(const [value,title] of [['string','String'],['number','Number'],['boolean','Boolean'],['null','Null'],['parameter','Parameter'],['literal','Literal (unconverted)'],['json','JSON (advanced)']]){
      const option=document.createElement('option');option.value=value;option.textContent=title;mode.append(option);
    }
    const raw=existing[name],initialMode=raw===undefined?'string':actionArgumentMode(raw);
    mode.value=initialMode;
    const valueHolder=el('span',null,'arg-value-holder');
    const draw=(value)=>{
      valueHolder.replaceChildren();
      if(mode.value==='parameter'){
        const editor=expressionEditor(value||'');editor.classList.add('arg-expression');valueHolder.append(editor);
      }else if(mode.value==='null'){
        valueHolder.append(el('span','null','muted'));
      }else if(mode.value==='boolean'){
        const select=document.createElement('select');select.className='arg-literal';
        for(const v of ['true','false']){const o=document.createElement('option');o.value=v;o.textContent=v;select.append(o);}
        select.value=String(value??false);valueHolder.append(select);
      }else{
        const input=document.createElement('input');input.className='arg-literal';
        input.value=mode.value==='json'?JSON.stringify(value??null):String(value??'');
        input.placeholder=mode.value==='literal'?'Exact text, no conversions':'Value';
        valueHolder.append(input);
      }
      updateActionPreview();
    };
    let saved={};
    const read=()=>{
      if(mode.value==='parameter')return expressionFromEditor(box.querySelector('.arg-expression'));
      if(mode.value==='null')return null;
      return box.querySelector('.arg-literal')?.value??'';
    };
    mode.onchange=()=>{
      const old=mode.dataset.previous||initialMode;
      const previous=readBeforeSwitch(saved,old);
      const next=(mode.value==='string'||mode.value==='literal'||mode.value==='number')?String(previous??''):mode.value==='boolean'?'false':mode.value==='json'?previous:'';
      draw(next);mode.dataset.previous=mode.value;
    };
    function readBeforeSwitch(cache,oldMode){
      return Object.prototype.hasOwnProperty.call(cache,oldMode)?cache[oldMode]:actionArgumentValue(raw,initialMode);
    }
    // Preserve each mode's editor contents when switching types.
    mode.addEventListener('focus',()=>{saved[mode.value]=read();});
    mode.dataset.previous=initialMode;
    draw(actionArgumentValue(raw,initialMode));
    label.append(mode,valueHolder);box.append(label);holder.append(box);
    box.addEventListener('input',updateActionPreview);
    box.addEventListener('change',updateActionPreview);
  }
  if(!holder.children.length)holder.append(el('span','No arguments required','muted'));
}
function addRuleAction(item={}){
  const row=el('div',null,'action-row');
  const select=document.createElement('select');select.className='action-method';
  for(const entry of Object.entries(actionCatalog)){
    const name=entry[0],info=entry[1],o=document.createElement('option');
    o.value=name;o.textContent=name+(info.enabled===false?' (disabled)':'');if(name===item.method)o.selected=true;select.append(o);
  }
  const args=el('div',null,'action-arguments');
  const remove=el('button','Remove');remove.type='button';remove.className='danger';remove.onclick=()=>{row.remove();updateActionPreview();};
  row.append(select,args,remove);$('#rule-action-builder').append(row);
  select.onchange=()=>{renderActionArguments(row,{});updateActionPreview();};
  renderActionArguments(row,item);updateActionPreview();
}
function actionsFromBuilder(){
  return [...$('#rule-action-builder').children].map(row=>{
    const method=row.querySelector('.action-method').value,argumentsObj={};
    for(const box of row.querySelectorAll('.action-arg')){
      const name=box.dataset.name,mode=box.querySelector('.arg-mode').value;
      try{
        argumentsObj[name]=mode==='parameter'
          ? {kind:'parameter',expression:expressionFromEditor(box.querySelector('.arg-expression'))}
          :parseActionArgument(mode,box.querySelector('.arg-literal')?.value??'');
      }catch(err){throw Error(method+' / '+name+': '+err.message)}
    }
    return {method:method,arguments:argumentsObj};
  });
}
function updateActionPreview(){
  const target=$('#rule-action-preview');
  if(!target)return;
  try{
    const items=actionsFromBuilder();
    target.textContent=JSON.stringify(items,null,2);
  }catch(err){target.textContent='Invalid action argument: '+err.message;}
}
function renderActionBuilder(items=[]){$('#rule-action-builder').replaceChildren();for(const item of items)addRuleAction(item);updateActionPreview();}
function syncRawRule(){
  const group=$('#rule-condition-builder > .condition-group');
  const condition=group?conditionFromGroup(group):{kind:'group',logic:'all',items:[]};
  const actionItems=actionsFromBuilder();
  $('#rule-condition').value=JSON.stringify(condition,null,2);
  $('#rule-actions').value=JSON.stringify(actionItems,null,2);
  return {condition:condition,actions:actionItems};
}
async function openRule(rule){
  edit.rule=rule?.id||null;
  if(!ruleParameters.length)ruleParameters=await api('/api/global-variables');
  if(!Object.keys(actionCatalog).length)actionCatalog=await api('/api/modules/actions');
  $('#rule-edit').hidden=false;
  $('#rule-name').value=rule?.name||'';
  $('#rule-priority').value=rule?.priority??100;
  $('#rule-cooldown').value=rule?.cooldown_seconds??0;
  $('#rule-enabled').checked=rule?!!rule.enabled:true;
  $('#rule-stop').checked=!!rule?.stop_processing;
  renderConditionBuilder(rule?.condition||{kind:'group',logic:'all',items:[]});
  renderActionBuilder(rule?.actions||[]);
  syncRawRule();
  $('#rule-edit').scrollIntoView({behavior:'smooth'});
}
function loadRawRuleIntoBuilder(){
  try{renderConditionBuilder(JSON.parse($('#rule-condition').value));renderActionBuilder(JSON.parse($('#rule-actions').value));notice('Loaded raw JSON into builder');}
  catch(err){notice('Invalid rule JSON: '+err.message)}
}
async function saveRule(e){
  e.preventDefault();
  try{
    const built=syncRawRule();
    const data={name:$('#rule-name').value,priority:Number($('#rule-priority').value),cooldown_seconds:Number($('#rule-cooldown').value),enabled:$('#rule-enabled').checked,stop_processing:$('#rule-stop').checked,condition:built.condition,actions:built.actions};
    await api('/api/global-rules'+(edit.rule?'/'+edit.rule:''),edit.rule?'PUT':'POST',data);
    $('#rule-edit').hidden=true;await refreshRules();notice('Rule saved');
  }catch(err){notice(err.message)}
}

function logCategory(event){
  const type=String(event.event_type||'');
  if(String(event.level||'').toLowerCase()==='error')return 'errors';
  if(type.includes('rule')||type.includes('engine'))return 'rules';
  if(type.includes('action')||type.includes('http_action'))return 'actions';
  if(type.includes('source')||type.includes('poll'))return 'sources';
  return 'other';
}
function formatLogTime(value){
  if(!value)return '';
  const d=new Date(value);
  return Number.isNaN(d.getTime())?value:d.toLocaleString();
}
async function refreshLogs(){
  const limit=Number($('#log-limit')?.value||100);
  const filter=$('#log-filter')?.value||'all';
  const events=await api('/api/events?limit='+encodeURIComponent(limit));
  const body=$('#log-body');body.replaceChildren();
  let shown=0;
  for(const event of events){
    const category=logCategory(event);
    if(filter!=='all' && filter!==category)continue;
    const tr=el('tr',null,'log-'+String(event.level||'info').toLowerCase());
    const messageTd=el('td');
    messageTd.append(document.createTextNode(event.message||''));
    let details=null;
    if(event.details_json){
      try{details=JSON.parse(event.details_json)}catch{details=event.details_json}
    }
    if(details!==null){
      const expander=document.createElement('details');expander.className='log-details';
      const summary=document.createElement('summary');summary.textContent='Details';
      const pre=document.createElement('pre');
      pre.textContent=typeof details==='string'?details:JSON.stringify(details,null,2);
      expander.append(summary,pre);messageTd.append(expander);
    }
    tr.append(
      el('td',formatLogTime(event.created_at)),
      el('td',event.level||''),
      el('td',event.event_type||''),
      messageTd
    );
    body.append(tr);shown++;
  }
  $('#log-empty').hidden=shown!==0;
}
function switchTab(){
  let tab=location.hash.slice(1)||'state';
  if(!['state','sources','variables','actions','rules','logs','settings'].includes(tab))tab='state';
  for(const sec of document.querySelectorAll('main section'))sec.hidden=sec.id!==tab;
  for(const a of document.querySelectorAll('nav a[href^="#"]'))a.classList.toggle('active',a.hash==='#'+tab);
}
window.addEventListener('hashchange',switchTab);

$('#new-source').onclick=()=>openForm('source');
$('#new-action').onclick=()=>openForm('action');
$('#new-rule').onclick=()=>openRule();
$('#new-variable').onclick=()=>openVariable();
$('#cancel-source').onclick=()=>$('#source-edit').hidden=true;
$('#cancel-action').onclick=()=>$('#action-edit').hidden=true;
$('#cancel-rule').onclick=()=>$('#rule-edit').hidden=true;
$('#cancel-variable').onclick=()=>$('#variable-edit').hidden=true;
$('#cancel-derived').onclick=()=>$('#derived-edit').hidden=true;
$('#source-form').onsubmit=e=>saveForm('source',e);
$('#action-form').onsubmit=e=>saveForm('action',e);
$('#variable-form').onsubmit=saveVariable;
$('#derived-form').onsubmit=saveDerived;
$('#rule-form').onsubmit=saveRule;
$('#add-rule-action').onclick=()=>addRuleAction({});
$('#apply-raw-rule').onclick=loadRawRuleIntoBuilder;
$('#refresh-logs').onclick=()=>refreshLogs().catch(e=>notice(e.message));
$('#log-filter').onchange=()=>refreshLogs().catch(e=>notice(e.message));
$('#log-limit').onchange=()=>refreshLogs().catch(e=>notice(e.message));
$('#settings-form').onsubmit=async e=>{
  e.preventDefault();
  try{await api('/api/settings','PUT',{rule_interval_seconds:Number(e.target.elements.rule_interval_seconds.value)});notice('Settings saved');}
  catch(err){notice(err.message)}
};
$('#refresh').onclick=()=>Promise.all([pool(),refreshSources(),refreshVariables(),refreshActions(),refreshRules(),refreshLogs()]).catch(e=>notice(e.message));

(async()=>{
  switchTab();
  try{
    await Promise.all([pool(),refreshSources(),refreshVariables(),refreshActions(),refreshRules(),refreshLogs()]);
    $('#settings-form').elements.rule_interval_seconds.value=(await api('/api/settings')).rule_interval_seconds;
  }catch(e){notice(e.message)}
})();
