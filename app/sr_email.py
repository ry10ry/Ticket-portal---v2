"""Approved SR customer drafts with uploaded screenshots and approval evidence."""
import json
import mimetypes
from email import policy
from email.message import EmailMessage
from html import escape
from fastapi import HTTPException
from app.cr_email import singapore_time


def current_approval(row, history, files):
    if row.status!='Approved':
        raise HTTPException(409,'Only an Approved SR can generate a customer email draft')
    start=next((i for i in range(len(history)-1,-1,-1) if history[i].action=='submit'),None)
    if start is None:raise HTTPException(409,'Submission record is missing')
    cycle=history[start:]
    approvals=[h for h in cycle if h.action=='approve']
    if len(approvals)!=1 or any(h.action=='return' for h in cycle):
        raise HTTPException(409,'Current SR approval record is required')
    approval=approvals[0]
    snapshot=json.loads(approval.snapshot)
    submitted=json.loads(cycle[0].snapshot)
    def content(value):return (value.get('subject'),value.get('description'))
    def ids(value):return [f['id'] for f in value.get('files',[])]
    if content(snapshot)!=(row.subject,row.description) or content(submitted)!=content(snapshot):
        raise HTTPException(409,'Approval does not cover the current SR content')
    if ids(submitted)!=ids(snapshot) or ids(snapshot)!=[f.id for f in files]:
        raise HTTPException(409,'Approval does not cover the current SR attachments')
    return cycle[0],approval


def draft_message(row, submission, approval, files, actor, owner):
    msg=EmailMessage(policy=policy.SMTP)
    msg['Subject']=row.subject.replace('\r',' ').replace('\n',' ')
    msg['X-Unsent']='1'
    msg.set_content(row.description)
    html='<html><body style="font-family:Arial,sans-serif;font-size:11pt;">'
    html+='<p>'+escape(row.description).replace('\n','<br>')+'</p>'
    images=[f for f in files if f.mime in ('image/png','image/jpeg','image/gif','image/webp')]
    for f in images:
        html+=f'<p><img src="cid:sr-{row.id}-file-{f.id}@servicedesk" alt="{escape(f.filename,quote=True)}" style="max-width:100%;height:auto;"></p>'
    msg.add_alternative(html+'</body></html>',subtype='html')
    html_part=msg.get_payload()[-1]
    for f in images:
        html_part.add_related(f.data,maintype='image',subtype=f.mime.split('/')[1],
            cid=f'<sr-{row.id}-file-{f.id}@servicedesk>',filename=f.filename,disposition='inline')
    for f in files:
        if f in images:continue
        mime=mimetypes.guess_type(f.filename)[0] or 'application/octet-stream'
        main,sub=mime.split('/',1)
        msg.add_attachment(f.data,maintype=main,subtype=sub,filename=f.filename)
    captured=json.loads(approval.snapshot).get('_actor',{})
    name=captured.get('name',actor.name)
    email=captured.get('email',actor.email)
    role=captured.get('role',actor.role)
    roles={'infra_tl':'MOMCC Infra TL','infra_manager':'MOMCC Infra Manager','admin':'Administrator'}
    record=['Service Request Approval Record',row.number,'Subject: '+row.subject,
        'Raised by: '+owner.name,'Submitted: '+singapore_time(submission.created_at),'',
        'Approved by: '+name,'Email: '+email,'Role: '+roles.get(role,role),
        'Approved at: '+singapore_time(approval.created_at),
        'Comment: '+(approval.comment or '(No comment recorded)'),
        f'Audit record: {approval.id}; SR revision: '+str(json.loads(approval.snapshot).get('version','')),
        '', 'Approved attachments and screenshots:']+[f.filename for f in files]
    msg.add_attachment('\n'.join(record).encode('utf-8'),maintype='text',subtype='plain',
        filename=row.number+' - Approval Record.txt')
    return msg.as_bytes(),row.number+' - Customer Email Draft.eml'
