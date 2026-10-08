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
    if row.status!='Approved':
        raise HTTPException(409,'Only an Approved CR can generate an ISTD email draft')
    start=next((i for i in range(len(history)-1,-1,-1) if history[i].action=='submit'),None)
    if start is None:raise HTTPException(409,'Current submission record is missing')
    cycle=history[start:]
    if any(h.action=='return' for h in cycle):raise HTTPException(409,'CR was returned; a fresh review and approval is required')
    support=next((h for h in cycle if h.action=='support'),None)
    approval=next((h for h in cycle if h.action=='approve'),None)
    if not support or not approval or support.id>=approval.id:
        raise HTTPException(409,'Current TL Support and Manager Approval records are required')
    def content(h):return {k:v for k,v in json.loads(h.snapshot).items() if not k.startswith('_')}
    if content(support)!=content(approval) or content(approval)!=json.loads(row.content):
        raise HTTPException(409,'Support and approval do not cover the current CR content')
    # Only files captured in the actual approval revision may leave in the draft.
    def files(h):return {f['kind']:f['id'] for f in json.loads(h.snapshot).get('_files',[])}
    approved=files(approval)
    if files(support)!=approved or set(approved)!=set(KINDS):
        raise HTTPException(409,'All three uploaded documents must be covered by support and approval')
    return cycle[0],support,approval,approved


def singapore_time(value):
    if value.tzinfo is None:value=value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo('Asia/Singapore')).strftime('%d %b %Y, %I:%M %p SGT')


def draft_message(row, submission, support, approval, files, identities):
    number=clean_number(row.number);content=json.loads(approval.snapshot)
    lines=['Dear ISTD,','',f'Please review the following Change Request: {number}.','',
        'Background / Reason for Change:',content.get('background',''),'',
        'Scope of Change:',content.get('scope',''),'',
        'Support and Approval Records:']
    for label,h in [('MOMCC Infra TL Support',support),('MOMCC Infra Manager Approval',approval)]:
        person=identities[h.actor_id]
        captured=json.loads(h.snapshot).get('_actor',{})
        name=captured.get('name',person.name);email=captured.get('email',person.email)
        role=captured.get('role',person.role)
        lines += [label,f'Name: {name}',f'Email: {email}',f'Role: '+{'infra_tl':'MOMCC Infra TL','infra_manager':'MOMCC Infra Manager','admin':'Administrator','infra':'MOMCC Infra'}.get(role,role),
            f'Date/Time: {singapore_time(h.created_at)}',f'Audit record: {h.id}; CR revision: {h.version}',
            'Comment: '+(h.reason or '(No comment recorded)'), '']
    lines += ['Attached documents:']+[LABELS[f.kind]+': '+f.filename for f in files]
    lines += ['',f'Current submission: {singapore_time(submission.created_at)}',
              'The attached documents are the versions reviewed and approved in this submission cycle.','',
              'Thank you.']
    text='\n'.join(lines)
    msg=EmailMessage(policy=policy.SMTP)
    msg['Subject']=number+' - Change Request for ISTD Review'
    msg['X-Unsent']='1'
    msg.set_content(text)
    msg.add_alternative('<html><body>'+''.join('<p style="white-space:pre-wrap">'+escape(line)+'</p>' for line in lines)+'</body></html>',subtype='html')
    for f in files:
        subtype='vnd.openxmlformats-officedocument.'+('wordprocessingml.document' if f.kind=='change_form' else 'spreadsheetml.sheet')
        msg.add_attachment(f.data,maintype='application',subtype=subtype,filename=f.filename)
    msg.add_attachment(text.encode('utf-8'),maintype='text',subtype='plain',filename=number+' - Support and Approval Record.txt')
    return msg.as_bytes(),number+' - ISTD Email Draft.eml'
