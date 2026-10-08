/* Internal change management. Server enforces every role and transition. */
let crCurrent, crFilter='';
const crLabels={infra:'MOMCC Infra',infra_tl:'Infra TL',infra_manager:'Infra Manager',admin:'Administrator'};
const crCanEdit=r=>r.status==='In-progress'&&(r.owner_id===me.id||me.role==='admin');
function crDate(value){return value?new Date(value).toLocaleString('en-SG',{timeZone:'Asia/Singapore',dateStyle:'medium',timeStyle:'short'}):'—'}
function crLocal(value){if(!value)return '';return new Date(new Date(value).getTime()+8*3600000).toISOString().slice(0,16)}
function crSummary(r,i=1){const c=r.content;return `<tr><td>${i}</td><td>${escape(c.environment)}</td><td>${escape(r.number)}</td><td>${escape(c.description)}</td><td>${crDate(c.deployment_start)} – ${crDate(c.deployment_end)}</td><td>${escape(c.impact)}</td></tr>`}
const crHead=`<thead><tr><th rowspan="2">S/N</th><th rowspan="2">Environment</th><th rowspan="2">CR Number</th><th rowspan="2">Description</th><th>Deployment</th><th rowspan="2">Impact Assessment (e.g. what services will be down? IVRS, Self-help? Any maintenance announcement?)</th></tr><tr><th>Start Date/Time</th></tr></thead>`;
async function renderChanges(){
 const data=await api('/changes');showView('changes');$('#heading').textContent='Change Management';
 $('#otherView').innerHTML=`<div class="stats">${[['In-progress','In-progress CR'],['Pending Review','Pending Review'],['Pending Approval','Pending Approval'],['Total CR','Total CR']].map(([key,label])=>`<button class="stat secondary" data-cr-filter="${key==='Total CR'?'':key}"><span>${label}</span><strong>${data.counts[key]}</strong></button>`).join('')}</div><div class="toolbar"><button id="raiseCR">Raise Change Request</button><button id="refreshCR" class="secondary">Refresh</button><select id="crFilter" aria-label="CR status"><option value="">All CR</option>${['In-progress','Pending Review','Pending Approval','Approved'].map(s=>`<option ${crFilter===s?'selected':''}>${s}</option>`).join('')}</select></div><div class="tablewrap"><table><thead><tr><th>CR Number</th><th>Description</th><th>Environment</th><th>Status</th><th>Raised by</th><th></th></tr></thead><tbody>${data.items.filter(r=>!crFilter||r.status===crFilter).map(r=>`<tr><td>${escape(r.number)}</td><td>${escape(r.content.description||'New change request')}</td><td>${escape(r.content.environment)}</td><td>${escape(r.status)}</td><td>${escape(r.owner)}</td><td><button data-open-cr="${r.id}" class="secondary">Open</button></td></tr>`).join('')}</tbody></table></div>${data.items.length?'':'<p>No change requests yet.</p>'}`;
 $('#raiseCR').onclick=async()=>{try{const r=await api('/changes',{});await renderChanges();await openCR(r.id)}catch(e){toast(e.message)}};
 $('#refreshCR').onclick=()=>renderChanges().catch(e=>toast(e.message));
 $('#crFilter').onchange=e=>{crFilter=e.target.value;renderChanges().catch(e=>toast(e.message))};
 document.querySelectorAll('[data-cr-filter]').forEach(b=>b.onclick=()=>{crFilter=b.dataset.crFilter;renderChanges().catch(e=>toast(e.message))});
 document.querySelectorAll('[data-open-cr]').forEach(b=>b.onclick=()=>openCR(b.dataset.openCr).catch(e=>toast(e.message)));
}
const crDialog=document.createElement('dialog');crDialog.id='crDialog';crDialog.className='detail cr-dialog';document.body.append(crDialog);
async function openCR(id){
 const r=await api('/changes/'+id);crCurrent=r;const c=r.content,editable=crCanEdit(r);
 const input=(key,label,type='text')=>`<label>${label}<input name="${key}" type="${type}" value="${escape(type==='datetime-local'?crLocal(c[key]):c[key])}" ${type==='datetime-local'?'':'maxlength="1000"'}></label>`;
 const area=(key,label,max=20000)=>`<label>${label}<textarea name="${key}" rows="5" maxlength="${max}">${escape(c[key])}</textarea></label>`;
 crDialog.innerHTML=`<div class="dialoghead"><div><div class="eyebrow">${escape(r.number)}</div><h2>Change Request</h2><p>${escape(r.status)} · Raised by ${escape(r.owner)}</p></div><button id="closeCR" class="secondary">Close</button></div><form id="crForm"><fieldset ${editable?'':'disabled'}>${area('background','Background / Reason for Change')}${area('scope','Scope of Change')}<h3>Change summary</h3><div class="grid">${input('environment','Environment (e.g. PRD)')}${input('description','Description')}${input('deployment_start','Deployment start (Singapore time)','datetime-local')}${input('deployment_end','Deployment end (Singapore time)','datetime-local')}</div>${area('impact','Impact Assessment — affected services, downtime, IVRS / Self-help, maintenance announcement')}<h3>Forms</h3><p>The three template layouts will be added when supplied. You can save their content below now.</p>${area('change_form','Change Request Form',50000)}${area('runbook','Runbook',50000)}${area('checklist','Checklist-RFC-Impact',50000)}${editable?'<button type="submit">Save CR</button>':''}</fieldset></form><h3>CR summary</h3><div class="tablewrap"><table>${crHead}<tbody>${crSummary(r)}</tbody></table></div><div id="crActions" class="toolbar" style="margin-top:20px"></div><h3>Review & approval history</h3><div>${r.history.map(h=>`<article class="entry"><strong>${escape(h.actor)} — ${escape(h.action)}</strong><small> · ${crDate(h.created_at)} · Revision ${h.version}</small><p>${escape(h.reason)}</p><details><summary>Content at this revision</summary><pre class="cr-snapshot">${escape(JSON.stringify(h.content,null,2))}</pre></details></article>`).join('')}</div>`;
 $('#closeCR').onclick=()=>crDialog.close();
 $('#crForm').onsubmit=async e=>{e.preventDefault();try{await saveCR();await openCR(id);await renderChanges();toast('CR saved')}catch(x){toast(x.message)}};
 const buttons=[];
 if(editable)buttons.push(['submit','Save & submit to TL Review']);
 if(r.status==='Pending Review'&&['infra_tl','admin'].includes(me.role)&&(r.owner_id!==me.id||me.role==='admin'))buttons.push(['support','Support'],['return','Return to Infra']);
 if(r.status==='Pending Approval'&&['infra_manager','admin'].includes(me.role)&&(r.owner_id!==me.id||me.role==='admin'))buttons.push(['approve','Approve'],['return','Return to Infra']);
 $('#crActions').innerHTML=buttons.map(([a,label])=>`<button type="button" data-cr-action="${a}" class="${a==='return'?'secondary':''}">${label}</button>`).join('');
 document.querySelectorAll('[data-cr-action]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{const action=b.dataset.crAction;let reason='';if(action==='return'){reason=prompt('Reason for returning this CR:');if(reason===null)return;if(!reason.trim())throw Error('Return reason is required')}if(action==='submit')await saveCR();await api('/changes/'+id+'/actions',{version:crCurrent.version,action,reason});await openCR(id);await renderChanges();toast(action==='return'?'CR returned to Infra':'CR updated')}catch(e){toast(e.message)}finally{b.disabled=false}});
 if(!crDialog.open)crDialog.showModal();
}
async function saveCR(){const c=Object.fromEntries(new FormData($('#crForm')));for(const key of ['deployment_start','deployment_end'])c[key]=c[key]?c[key]+':00+08:00':null;crCurrent=await api('/changes/'+crCurrent.id+'/save',{version:crCurrent.version,content:c});}
$('#navChanges').onclick=()=>renderChanges().catch(e=>toast(e.message));
const originalStart=start;start=async function(){await originalStart();$('#navChanges').hidden=!crLabels[me.role]};
// Initial sign-in may have already begun before this script loaded.
setInterval(()=>{$('#navChanges').hidden=!me||!crLabels[me.role]},500);
