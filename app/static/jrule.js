"use strict";
const $=s=>document.querySelector(s);
let sources=[],actions=[],rules=[],variables=[],edit={source:null,action:null,rule:null,variable:null};

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

function objCard(name,info){
  const c=el('div',null,'card');
  c.append(el('h3',name),el('p',`${info.module} / ${info.type}`,'muted'));
  const table=el('table'),head=el('tr');
  for(const title of ['Property','Current','Last','Last changed'])head.append(el('th',title));
  table.append(head);
  for(const [name,p] of Object.entries(info.properties)){
    const tr=el('tr');
    for(const v of [name,j(p.current),j(p.last),p.last_changed||''])tr.append(el('td',v));
    table.append(tr);
  }
  c.append(table);
  return c;
}
async function pool(){
  const root=$('#objects');root.replaceChildren();
  for(const [name,info] of Object.entries(await api('/api/objects')))root.append(objCard(name,info));
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
  const catalog=await api('/api/modules/actions');
  $('#action-hint').textContent='Available actions: '+Object.keys(catalog).join(', ');
}
async function refreshRules(){
  rules=await api('/api/global-rules');
  renderList('#rules-list',rules,o=>openRule(o),o=>remove('global-rules',o.id,refreshRules));
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
  if(type==='source')renderMapping(obj?.mapping||{});
  $('#'+type+'-edit').hidden=false;
  $('#'+type+'-edit').scrollIntoView({behavior:'smooth'});
}
async function saveForm(type,e){
  e.preventDefault();
  const data={};
  for(const field of e.target.elements){
    if(!field.name)continue;
    if(field.name==='mapping'||field.name==='body')data[field.name]=JSON.parse(field.value);
    else if(field.type==='checkbox')data[field.name]=field.checked;
    else if(field.type==='number')data[field.name]=Number(field.value);
    else data[field.name]=field.value;
  }
  try{
    await api('/api/'+type+'s'+(edit[type]?'/'+encodeURIComponent(edit[type]):''),edit[type]?'PUT':'POST',data);
    $('#'+type+'-edit').hidden=true;
    await(type==='source'?refreshSources():refreshActions());
    notice('Saved '+type);
  }catch(err){notice(err.message)}
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

function openRule(rule){
  edit.rule=rule?.id||null;$('#rule-edit').hidden=false;
  $('#rule-name').value=rule?.name||'';$('#rule-priority').value=rule?.priority??100;
  $('#rule-cooldown').value=rule?.cooldown_seconds??0;$('#rule-enabled').checked=rule?!!rule.enabled:true;
  $('#rule-stop').checked=!!rule?.stop_processing;
  $('#rule-condition').value=JSON.stringify(rule?.condition||{kind:'condition',left:'current.presence.current_user',operator:'eq',right:'JGRA'},null,2);
  $('#rule-actions').value=JSON.stringify(rule?.actions||[],null,2);
  $('#rule-edit').scrollIntoView({behavior:'smooth'});
}
async function saveRule(e){
  e.preventDefault();
  try{
    const data={name:$('#rule-name').value,priority:Number($('#rule-priority').value),cooldown_seconds:Number($('#rule-cooldown').value),enabled:$('#rule-enabled').checked,stop_processing:$('#rule-stop').checked,condition:JSON.parse($('#rule-condition').value),actions:JSON.parse($('#rule-actions').value)};
    await api('/api/global-rules'+(edit.rule?'/'+edit.rule:''),edit.rule?'PUT':'POST',data);
    $('#rule-edit').hidden=true;await refreshRules();notice('Rule saved');
  }catch(err){notice(err.message)}
}
function switchTab(){
  let tab=location.hash.slice(1)||'state';
  if(!['state','sources','variables','actions','rules','settings'].includes(tab))tab='state';
  for(const sec of document.querySelectorAll('main section'))sec.hidden=sec.id!==tab;
  for(const a of document.querySelectorAll('nav a[href^="#"]'))a.classList.toggle('active',a.hash==='#'+tab);
}
window.addEventListener('hashchange',switchTab);

$('#new-source').onclick=()=>openForm('source');
$('#new-action').onclick=()=>openForm('action');
$('#new-rule').onclick=()=>openRule();
$('#new-variable').onclick=()=>openVariable();
$('#add-mapping').onclick=()=>{addMappingRow();syncMappingText();};
$('#cancel-source').onclick=()=>$('#source-edit').hidden=true;
$('#cancel-action').onclick=()=>$('#action-edit').hidden=true;
$('#cancel-rule').onclick=()=>$('#rule-edit').hidden=true;
$('#cancel-variable').onclick=()=>$('#variable-edit').hidden=true;
$('#source-form').onsubmit=e=>saveForm('source',e);
$('#action-form').onsubmit=e=>saveForm('action',e);
$('#variable-form').onsubmit=saveVariable;
$('#rule-form').onsubmit=saveRule;
$('#settings-form').onsubmit=async e=>{
  e.preventDefault();
  try{await api('/api/settings','PUT',{rule_interval_seconds:Number(e.target.elements.rule_interval_seconds.value)});notice('Settings saved');}
  catch(err){notice(err.message)}
};
$('#refresh').onclick=()=>Promise.all([pool(),refreshSources(),refreshVariables(),refreshActions(),refreshRules()]).catch(e=>notice(e.message));

(async()=>{
  switchTab();
  try{
    await Promise.all([pool(),refreshSources(),refreshVariables(),refreshActions(),refreshRules()]);
    $('#settings-form').elements.rule_interval_seconds.value=(await api('/api/settings')).rule_interval_seconds;
  }catch(e){notice(e.message)}
})();
