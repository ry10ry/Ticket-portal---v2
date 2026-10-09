"""Build a local Outlook-compatible .eml draft; never connects to a mail service."""
import json
from datetime import timezone
from email.message import EmailMessage
from email import policy
from html import escape
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from app.cr_documents import clean_number

KINDS=('change_form','runbook','checklist')
LABELS={'change_form':'CR Form','runbook':'Runbook','checklist':'Checklist-RFC-Impact'}


def current_approval_cycle(row, history):
    if row.status not in ('Approved','Closed'):
        raise HTTPException(409,'Only an Approved or Closed CR can generate an ISTD email draft')
    start=next((i for i in range(len(history)-1,-1,-1) if history[i].action=='submit'),None)
    if start is None:raise HTTPException(409,'Current submission record is missing')
    cycle=history[start:]
    if any(h.action=='return' for h in cycle):raise HTTPException(409,'CR was returned; a fresh review and approval is required')
    from app.cr_workflow import stages_for
    stages=stages_for(row)
    decisions=[h for h in cycle if h.action in ('support','approve')]
    if len(decisions)!=len(stages) or any(h.action!=stage['action'] for h,stage in zip(decisions,stages)):
        raise HTTPException(409,'All configured review and approval stages must be completed')
    def content(value):return {k:v for k,v in json.loads(value).items() if not k.startswith('_')}
    expected=content(row.content)
    def files(h):return {f['kind']:f['id'] for f in json.loads(h.snapshot).get('_files',[]) if f['kind'] in KINDS}
    approved=files(decisions[-1])
    for index,(h,stage) in enumerate(zip(decisions,stages)):
        snapshot=json.loads(h.snapshot)
        captured=snapshot.get('_stage_decision')
        if captured and captured!={'index':index,**stage}:
            raise HTTPException(409,'Approval record does not match the configured stage')
        actor=snapshot.get('_actor')
        if actor and actor['role'] not in (stage['role'],'admin'):
            raise HTTPException(409,'Approval record has an invalid actor role')
        if content(h.snapshot)!=expected or files(h)!=approved or set(approved)!=set(KINDS):
            raise HTTPException(409,'Approvals do not cover the current content and three documents')
    support=next((h for h in reversed(decisions) if h.action=='support'),None)
    return cycle[0],support,decisions[-1],approved


def approval_decisions(history):
    start=next((i for i in range(len(history)-1,-1,-1) if history[i].action=='submit'),len(history))
    return [h for h in history[start:] if h.action in ('support','approve')]


def singapore_time(value):
    if value.tzinfo is None:value=value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo('Asia/Singapore')).strftime('%d %b %Y, %I:%M %p SGT')


def draft_message(row, submission, support, approval, files, identities, decisions=None):
    number=clean_number(row.number);content=json.loads(approval.snapshot)
    lines=['Dear ISTD,','',
        'Background / Reason for Change:',content.get('background',''),'',
        'Scope of Change:',content.get('scope',''),'',
        'For you review and support.','',
        'S/N | Environment | CR Number | Description | Deployment - Start Date/Time | Impact Assessment',
        f'1 |  | {number} |  |  | ']
    records=['Support and Approval Records:',number,'']
    for h in (decisions if decisions is not None else [h for h in (support,approval) if h]):
        stage=json.loads(h.snapshot).get('_stage_decision',{})
        role=stage.get('role','infra_tl' if h.action=='support' else 'infra_manager')
        role_name={'infra':'MOMCC Infra','infra_tl':'MOMCC Infra TL','infra_manager':'MOMCC Infra Manager','admin':'Administrator'}[role]
        label=role_name+(' Support' if h.action=='support' else ' Approval')
        person=identities[h.actor_id]
        captured=json.loads(h.snapshot).get('_actor',{})
        name=captured.get('name',person.name);email=captured.get('email',person.email)
        role=captured.get('role',person.role)
        records += [label,'Stage: '+stage.get('label',h.action.capitalize()),f'Name: {name}',f'Email: {email}',f'Role: '+{'infra_tl':'MOMCC Infra TL','infra_manager':'MOMCC Infra Manager','admin':'Administrator','infra':'MOMCC Infra'}.get(role,role),
            f'Date/Time: {singapore_time(h.created_at)}',f'Audit record: {h.id}; CR revision: {h.version}',
            'Comment: '+(h.reason or '(No comment recorded)'), '']
    records += ['Attached documents:']+[LABELS[f.kind]+': '+f.filename for f in files]
    records += ['',f'Current submission: {singapore_time(submission.created_at)}',
              'The attached documents are the versions reviewed and approved in this submission cycle.','',
              'Thank you.']
    text='\n'.join(lines)
    msg=EmailMessage(policy=policy.SMTP)
    msg['Subject']=number+' - Change Request for ISTD Review'
    msg['X-Unsent']='1'
    msg.set_content(text)
    def paragraph(value):
        return '<p style="margin:0 0 18px 0;">'+escape(value).replace('\n','<br>')+'</p>'
    html='<html><body style="font-family:Arial,sans-serif;font-size:11pt;">'+paragraph('Dear ISTD,')
    html+='<p><strong>Background / Reason for Change:</strong></p>'+paragraph(content.get('background',''))
    html+='<p><strong>Scope of Change:</strong></p>'+paragraph(content.get('scope',''))
    html+=paragraph('For you review and support.')
    html+='<table border="1" cellspacing="0" cellpadding="7" style="border-collapse:collapse;width:100%;font-size:10pt;border:1px solid black;"><thead>'
    html+='<tr style="background-color:#d3d3d3;"><th rowspan="2">S/N</th><th rowspan="2">Environment</th><th rowspan="2">CR Number</th><th rowspan="2">Description</th><th>Deployment</th><th rowspan="2">Impact Assessment (e.g. what services will be down? IVRS, Self-help? Any maintenance announcement?)</th></tr><tr style="background-color:#d3d3d3;"><th>Start Date/Time</th></tr>'
    html+='</thead><tbody><tr><td>1</td><td>&nbsp;</td><td>'+escape(number)+'</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr></tbody></table></body></html>'
    msg.add_alternative(html,subtype='html')
    for f in files:
        subtype='vnd.openxmlformats-officedocument.'+('wordprocessingml.document' if f.kind=='change_form' else 'spreadsheetml.sheet')
        msg.add_attachment(f.data,maintype='application',subtype=subtype,filename=f.filename)
    msg.add_attachment('\n'.join(records).encode('utf-8'),maintype='text',subtype='plain',filename=number+' - Support and Approval Record.txt')
    return msg.as_bytes(),number+' - ISTD Email Draft.eml'
