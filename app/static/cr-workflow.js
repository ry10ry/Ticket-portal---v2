/* Admin-only editor. Server checks access and pins the workflow on every CR. */
const crWorkflowDefaults=[{label:'Review & Support',role:'infra_tl',action:'support'},{label:'Approve',role:'infra_manager',action:'approve'}];
const previousAdminClick=$('#navAdmin').onclick;
$('#navAdmin').onclick=async function(){
 await previousAdminClick();
 if(me.role!=='admin'||!$('#settingsForm'))return;
 try{
  const saved=await api('/cr-workflow');let stages=saved.stages.map(s=>({...s}));
  const panel=document.createElement('section');panel.className='panel settings-panel';panel.style.marginTop='24px';
  panel.innerHTML='<h2>Change Request Workflow</h2><p>Raised by Infra. Configure review and approval stages in order. Changes apply to new CRs; existing CRs retain their original workflow.</p><form id="crWorkflowForm"><div id="crWorkflowStages"></div><div class="toolbar"><button type="button" id="addCRStage" class="secondary">Add stage</button><button type="button" id="resetCRStages" class="secondary">Use default flow</button></div><p>The last stage must be an approval. Return to Infra restarts all stages after resubmission. Completion evidence remains required before closing.</p><button>Save CR Workflow</button></form>';
  $('#otherView').append(panel);
  const read=()=>{stages=[...panel.querySelectorAll('[data-stage]')].map(row=>({label:row.querySelector('[name=label]').value,role:row.querySelector('[name=role]').value,action:row.querySelector('[name=action]').value}))};
  const draw=()=>{
   $('#crWorkflowStages').innerHTML=stages.map((s,i)=>`<fieldset data-stage="${i}" class="workflow-stage"><legend>Stage ${i+1}</legend><label>Button / stage label<input name="label" value="${escape(s.label)}" maxlength="80" required></label><div class="grid"><label>Assigned role<select name="role">${Object.entries(crLabels).map(([role,label])=>`<option value="${role}" ${role===s.role?'selected':''}>${label}</option>`).join('')}</select></label><label>Action type<select name="action"><option value="support" ${s.action==='support'?'selected':''}>Review & Support</option><option value="approve" ${s.action==='approve'?'selected':''}>Approval</option></select></label></div><div class="toolbar"><button type="button" data-stage-move="${i}" data-direction="-1" class="secondary" ${i===0?'disabled':''}>Move up</button><button type="button" data-stage-move="${i}" data-direction="1" class="secondary" ${i===stages.length-1?'disabled':''}>Move down</button><button type="button" data-stage-remove="${i}" class="secondary" ${stages.length===1?'disabled':''}>Remove</button></div></fieldset>`).join('');
   $('#addCRStage').disabled=stages.length>=8;
   panel.querySelectorAll('[data-stage-move]').forEach(b=>b.onclick=()=>{read();const i=Number(b.dataset.stageMove),j=i+Number(b.dataset.direction);[stages[i],stages[j]]=[stages[j],stages[i]];draw()});
   panel.querySelectorAll('[data-stage-remove]').forEach(b=>b.onclick=()=>{read();stages.splice(Number(b.dataset.stageRemove),1);draw()});
  };
  $('#addCRStage').onclick=()=>{read();stages.push({label:'Approve',role:'infra_manager',action:'approve'});draw()};
  $('#resetCRStages').onclick=()=>{stages=crWorkflowDefaults.map(s=>({...s}));draw()};
  $('#crWorkflowForm').onsubmit=async e=>{e.preventDefault();const button=e.target.querySelector('button:not([type])');button.disabled=true;try{read();if(stages.at(-1).action!=='approve')throw Error('The final stage must be an approval');await api('/cr-workflow',{version:saved.version,stages});toast('CR workflow saved for new requests');await $('#navAdmin').onclick()}catch(x){toast(x.message)}finally{button.disabled=false}};
  draw();
 }catch(x){toast(x.message)}
};
