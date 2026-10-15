"use strict";

/*
 * Friendly jAPI action body editor.
 *
 * The stored representation deliberately remains the existing JSON body
 * template, so the backend, rule catalogue and existing saved actions do not
 * need a migration. Rule arguments are written as exact {{ args.name }}
 * templates; fixed values retain their JSON type.
 */

const actionBodyArgumentPattern=/^\{\{\s*args\.([A-Za-z][A-Za-z0-9_-]*)\s*\}\}$/;

function actionBodyFixedType(value){
  if(value===null)return 'null';
  if(typeof value==='boolean')return 'boolean';
  if(typeof value==='number')return 'number';
  if(typeof value==='string')return 'string';
  return 'json';
}

function actionBodyFixedDisplay(value,type){
  if(type==='null')return '';
  if(type==='json')return JSON.stringify(value??null);
  return String(value??'');
}

function actionBodyReadFixed(type,raw){
  if(type==='string')return raw;
  if(type==='number'){
    if(!String(raw).trim()||!Number.isFinite(Number(raw)))throw Error('Number must be finite');
    return Number(raw);
  }
  if(type==='boolean'){
    if(raw!=='true'&&raw!=='false')throw Error('Boolean must be true or false');
    return raw==='true';
  }
  if(type==='null')return null;
  if(type==='json'){
    try{return JSON.parse(raw)}
    catch{throw Error('JSON value is invalid')}
  }
  throw Error('Unknown value type: '+type);
}

function actionBodyDrawValue(row,value){
  const source=row.querySelector('.body-source').value;
  const type=row.querySelector('.body-value-type');
  const holder=row.querySelector('.body-value-holder');
  holder.replaceChildren();

  if(source==='argument'){
    type.parentElement.hidden=true;
    row.querySelector('.body-optional').disabled=false;
    const input=document.createElement('input');
    input.className='body-argument-name';
    input.placeholder='argument_name';
    input.value=String(value||'');
    holder.append(input);
    const hint=el('div','The rule will supply this as '+(input.value||'an argument')+'.','body-argument-hint');
    holder.append(hint);
    input.addEventListener('input',()=>{
      hint.textContent='The rule will supply this as '+(input.value.trim()||'an argument')+'.';
      actionBodySyncBestEffort();
    });
    return;
  }

  type.parentElement.hidden=false;
  row.querySelector('.body-optional').disabled=true;
  row.querySelector('.body-optional').checked=false;
  if(type.value==='null'){
    holder.append(el('span','null','muted'));
  }else if(type.value==='boolean'){
    const select=document.createElement('select');
    select.className='body-fixed-value';
    for(const v of ['true','false']){
      const option=document.createElement('option');
      option.value=v;option.textContent=v;select.append(option);
    }
    select.value=String(value??false);
    holder.append(select);
  }else{
    const input=document.createElement('input');
    input.className='body-fixed-value';
    input.value=actionBodyFixedDisplay(value,type.value);
    input.placeholder=type.value==='json'?'JSON value':'Value';
    holder.append(input);
  }
  holder.querySelectorAll('input,select').forEach(control=>{
    control.addEventListener('input',actionBodySyncBestEffort);
    control.addEventListener('change',actionBodySyncBestEffort);
  });
}

function addActionBodyRow(name='',value='',optional=false){
  const row=el('div',null,'action-body-param');

  const nameLabel=el('label','POST parameter');
  const nameInput=document.createElement('input');
  nameInput.className='body-param-name';
  nameInput.placeholder='extension';
  nameInput.value=name;
  nameLabel.append(nameInput);

  const sourceLabel=el('label','Supply from');
  const source=document.createElement('select');
  source.className='body-source';
  for(const pair of [['value','Value'],['argument','Rule argument']]){
    const option=document.createElement('option');
    option.value=pair[0];option.textContent=pair[1];source.append(option);
  }
  const argumentMatch=typeof value==='string'?value.match(actionBodyArgumentPattern):null;
  source.value=argumentMatch?'argument':'value';
  sourceLabel.append(source);

  const typeLabel=el('label','Value type');
  const type=document.createElement('select');
  type.className='body-value-type';
  for(const pair of [['string','String'],['number','Number'],['boolean','Boolean'],['null','Null'],['json','JSON (advanced)']]){
    const option=document.createElement('option');
    option.value=pair[0];option.textContent=pair[1];type.append(option);
  }
  const initialType=actionBodyFixedType(value);
  type.value=initialType;
  typeLabel.append(type);

  const valueLabel=el('label',argumentMatch?'Argument name':'Value');
  valueLabel.className='body-value-label';
  const holder=el('div',null,'body-value-holder');
  valueLabel.append(holder);

  const remove=el('button','Remove','danger');
  remove.type='button';
  remove.onclick=()=>{row.remove();actionBodySyncBestEffort();actionBodyRenderEmptyState();};

  const optionalLabel=el('label','Optional');
  const optionalBox=document.createElement('input');optionalBox.type='checkbox';optionalBox.className='body-optional';optionalBox.checked=optional;
  optionalLabel.append(optionalBox);
  row.append(nameLabel,sourceLabel,typeLabel,valueLabel,optionalLabel,remove);
  $('#action-body-builder').append(row);

  source.onchange=()=>{
    const isArgument=source.value==='argument';
    valueLabel.firstChild.textContent=isArgument?'Argument name':'Value';
    const next=isArgument?(nameInput.value.trim()||'argument'):'';
    if(!isArgument)type.value='string';
    actionBodyDrawValue(row,next);
    actionBodySyncBestEffort();
  };
  type.onchange=()=>{
    let next='';
    if(type.value==='boolean')next=false;
    else if(type.value==='null')next=null;
    else if(type.value==='json')next=null;
    actionBodyDrawValue(row,next);
    actionBodySyncBestEffort();
  };
  nameInput.addEventListener('input',actionBodySyncBestEffort);
  optionalBox.addEventListener('change',actionBodySyncBestEffort);

  actionBodyDrawValue(row,argumentMatch?argumentMatch[1]:value);
  actionBodyRenderEmptyState();
  return row;
}

function actionBodyRenderEmptyState(){
  const root=$('#action-body-builder');
  const old=root.querySelector('.action-body-empty');
  if(old)old.remove();
  if(!root.querySelector('.action-body-param')){
    root.append(el('div','No POST parameters. The action will send an empty JSON object.','action-body-empty'));
  }
}

function actionBodyFromBuilder(){
  const body={};
  const seen=new Set();
  for(const row of $('#action-body-builder').querySelectorAll('.action-body-param')){
    const name=row.querySelector('.body-param-name').value.trim();
    if(!name)throw Error('Each POST parameter needs a name');
    if(seen.has(name))throw Error('Duplicate POST parameter: '+name);
    seen.add(name);

    if(row.querySelector('.body-source').value==='argument'){
      const arg=row.querySelector('.body-argument-name')?.value.trim()||'';
      if(!/^[A-Za-z][A-Za-z0-9_-]*$/.test(arg)){
        throw Error(name+': argument name must start with a letter and contain only letters, digits, - or _');
      }
      body[name]='{{ args.'+arg+' }}';
      continue;
    }

    const type=row.querySelector('.body-value-type').value;
    const raw=type==='null'?'':(row.querySelector('.body-fixed-value')?.value??'');
    try{body[name]=actionBodyReadFixed(type,raw)}
    catch(err){throw Error(name+': '+err.message)}
  }
  return body;
}

function actionBodySync(){
  const body=actionBodyFromBuilder();
  $('#action-form').elements.body.value=JSON.stringify(body);
  const optional=[];
  for(const row of $('#action-body-builder').querySelectorAll('.action-body-param')){
    if(row.querySelector('.body-source').value==='argument'&&row.querySelector('.body-optional').checked)
      optional.push(row.querySelector('.body-argument-name').value.trim());
  }
  $('#action-form').elements.optional_arguments.value=JSON.stringify([...new Set(optional)]);
  return body;
}

function actionBodySyncBestEffort(){
  try{actionBodySync()}catch{}
}

function renderActionBodyBuilder(body,optional=[]){
  const root=$('#action-body-builder');
  root.replaceChildren();
  if(body&&typeof body==='object'&&!Array.isArray(body)){
    for(const entry of Object.entries(body)){
      const match=typeof entry[1]==='string'?entry[1].match(actionBodyArgumentPattern):null;
      addActionBodyRow(entry[0],entry[1],!!match&&optional.includes(match[1]));
    }
  }
  actionBodyRenderEmptyState();
  actionBodySyncBestEffort();
}

const actionBuilderPreviousOpenForm=openForm;
openForm=function(type,obj){
  actionBuilderPreviousOpenForm(type,obj);
  if(type==='action')renderActionBodyBuilder(obj?.body||{},obj?.optional_arguments||[]);
};

const actionBuilderPreviousSaveForm=saveForm;
saveForm=async function(type,e){
  if(type==='action'){
    try{actionBodySync()}
    catch(err){
      e.preventDefault();
      notice(err?.message||String(err));
      return;
    }
  }
  return actionBuilderPreviousSaveForm(type,e);
};

$('#add-action-body-param').onclick=()=>{
  const row=addActionBodyRow('','');
  row.querySelector('.body-param-name').focus();
};
