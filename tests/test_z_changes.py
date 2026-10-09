import base64
from email import policy
from email.parser import BytesParser
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from app.main import app
H={'X-Requested-With':'ServiceDesk'}
def login(c,email):assert c.post('/api/login',json={'email':email,'password':'TestPassword123!'},headers=H).status_code==200
def uploads():
 r=Path(__file__).parents[1]/'app/templates'
 return [dict(kind=k,filename=p.name,data=base64.b64encode(p.read_bytes()).decode()) for k,p in [('change_form',r/'cr-form.docx'),('runbook',r/'runbook.xlsx'),('checklist',r/'Checklist-RFC-Impact_v1.0.xlsx')]]
def test_upload_review_return_delete():
 with TestClient(app) as a,TestClient(app) as i,TestClient(app) as t,TestClient(app) as m,TestClient(app) as q:
  login(a,'admin@example.com')
  for c,role in [(i,'infra'),(t,'infra_tl'),(m,'infra_manager'),(q,'requester')]:
   email='cr-'+role+'@example.com';assert a.post('/api/users',json=dict(name=role,email=email,role=role,password='TestPassword123!'),headers=H).status_code==200;login(c,email)
  assert q.get('/api/changes').status_code==403
  r=i.post('/api/changes',json={},headers=H).json();path='/api/changes/'+str(r['id']);number=r['number']
  assert i.get(path+'/istd-email').status_code==409
  assert q.get(path+'/istd-email').status_code==403
  assert r['status']=='In-progress' and i.get('/api/changes').json()['counts']['In-progress']==1
  def act(c,action,reason=''):
   return c.post(path+'/actions',json=dict(version=r['version'],action=action,reason=reason),headers=H)
  assert act(i,'close').status_code==409
  assert i.post(path+'/istd-evidence',json=dict(version=r['version'],filename='approval.pdf',data=base64.b64encode(b'%PDF-1.4').decode()),headers=H).status_code==409
  assert act(i,'submit').status_code==422
  data=uploads()
  assert q.get(path+'/templates/runbook').status_code==403
  assert i.get(path+'/templates/runbook').status_code==200
  assert i.get(path+'/documents/runbook').status_code==404
  bad=[{**data[0],'data':base64.b64encode(b'invalid').decode()}]
  assert i.post(path+'/save',json=dict(version=r['version'],content={},uploads=bad),headers=H).status_code==422
  stale=r['version'];r=i.post(path+'/save',json=dict(version=stale,content=dict(background='Reason',scope='Scope'),uploads=data),headers=H).json()
  assert len(r['files'])==3
  assert i.post(path+'/save',json=dict(version=stale,content={}),headers=H).status_code==409
  r=act(i,'submit').json();assert r['status']=='Pending Review'
  assert i.post(path+'/save',json=dict(version=r['version'],content={}),headers=H).status_code==409
  assert act(m,'approve').status_code==403
  for f in data:
   response=t.get(path+'/documents/'+f['kind']);assert response.content==base64.b64decode(f['data']);assert q.get(path+'/documents/'+f['kind']).status_code==403
  r=act(t,'comment','Please improve rollback').json();assert r['status']=='Pending Review'
  assert act(t,'return').status_code==422
  r=act(t,'return','Please update runbook').json();assert r['status']=='In-progress'
  r=i.post(path+'/save',json=dict(version=r['version'],content=dict(scope='Revised scope'),uploads=[data[1]]),headers=H).json()
  assert r['content']['background']=='Reason' and len(r['history'][-1]['content']['_files'])==4
  old=r['history'][1]['content']['_files'][1]
  assert t.get(path+'/files/'+str(old['id'])).status_code==200
  r=act(i,'submit').json();r=act(t,'support').json();assert r['status']=='Pending Approval'
  r=act(m,'return','Clarify plan').json();assert r['status']=='In-progress'
  r=act(i,'submit').json();r=act(t,'support').json();r=act(m,'approve').json();assert r['status']=='Approved' and r['number']==number
  response=i.get(path+'/istd-email');assert response.status_code==200
  message=BytesParser(policy=policy.default).parsebytes(response.content)
  assert message['X-Unsent']=='1' and message['To'] is None
  assert number.replace('CR# ','CR#') in message['Subject']
  attachments=list(message.iter_attachments());assert len(attachments)==4
  for f in data:
   part=next(p for p in attachments if p.get_filename()==f['filename'])
   assert part.get_payload(decode=True)==base64.b64decode(f['data'])
  body=message.get_body(preferencelist=('plain',)).get_content()
  assert 'Support and Approval Records' not in body and 'MOMCC Infra TL Support' not in body
  assert 'For you review and support.' in body
  html=message.get_body(preferencelist=('html',)).get_content()
  assert '<strong>Background / Reason for Change:</strong>' in html
  assert '<strong>Scope of Change:</strong>' in html
  assert '<table' in html and '<td>&nbsp;</td>' in html
  assert 'Support and Approval Records' not in html
  record=next(p for p in attachments if p.get_filename().endswith('Support and Approval Record.txt')).get_payload(decode=True).decode()
  assert 'SGT' in record and 'cr-infra_tl@example.com' in record and 'cr-infra_manager@example.com' in record
  assert 'MOMCC Infra TL Support' in record and 'MOMCC Infra Manager Approval' in record
  assert 'Please update runbook' not in body
  assert all(h['content'].get('_actor') for h in r['history'])
  # Closing requires genuine internal approval plus an uploaded external approval record.
  assert act(i,'close').status_code==422
  evidence=dict(version=r['version'],filename='ISTD Approval.eml',data=base64.b64encode(b'From: istd@example.com\r\nSubject: Approved change\r\n\r\nApproved.\r\n').decode())
  assert q.post(path+'/istd-evidence',json=evidence,headers=H).status_code==403
  assert t.post(path+'/istd-evidence',json=evidence,headers=H).status_code==403
  assert i.post(path+'/istd-evidence',json={**evidence,'filename':'bad.exe'},headers=H).status_code==422
  assert i.post(path+'/istd-evidence',json={**evidence,'data':base64.b64encode(b'not an email').decode()},headers=H).status_code==422
  assert i.post(path+'/istd-evidence',json={**evidence,'version':r['version']-1},headers=H).status_code==409
  workbook=next(f for f in data if f['kind']=='runbook')
  assert i.post(path+'/istd-evidence',json={**evidence,'filename':'invalid.xlsx'},headers=H).status_code==422
  excel=i.post(path+'/istd-evidence',json={**evidence,'filename':'Completed.xlsx','data':workbook['data']},headers=H)
  assert excel.status_code==200
  r=excel.json();evidence['version']=r['version']
  r=i.post(path+'/istd-evidence',json=evidence,headers=H).json()
  assert r['status']=='Approved'
  proof=next(f for f in r['files'] if f['kind']=='istd_approval')
  assert t.get(path+'/files/'+str(proof['id'])).content==base64.b64decode(evidence['data'])
  assert i.get(path+'/istd-email').status_code==200
  assert act(t,'close').status_code==403
  r=act(i,'close').json();assert r['status']=='Closed'
  assert r['history'][-1]['action']=='close' and r['history'][-1]['content']['_files'][-1]['kind']=='istd_approval'
  assert act(i,'close').status_code==409
  assert i.post(path+'/istd-evidence',json={**evidence,'version':r['version']},headers=H).status_code==409
  assert i.get(path+'/istd-email').status_code==200
  listed=i.get('/api/changes').json()
  assert listed['items'][0]['status']=='Closed' and listed['counts']['Total CR']==1
  assert listed['counts']['Pending Approval']==0
  assert i.post(path+'/delete',json=dict(version=r['version'],action='comment'),headers=H).status_code==403
  assert a.post(path+'/delete',json=dict(version=r['version']-1,action='comment'),headers=H).status_code==409
  assert a.post(path+'/delete',json=dict(version=r['version'],action='comment'),headers=H).status_code==200
  assert a.get(path).status_code==404 and a.get(path+'/files/'+str(old['id'])).status_code==404
  assert a.get('/api/changes').json()['counts']['Total CR']==0

def test_concurrent_creation_keeps_unique_numbers():
 def create(_):
  with TestClient(app) as c:
   login(c,'cr-infra@example.com');r=c.post('/api/changes',json={},headers=H);assert r.status_code==200;return r.json()['number']
 with ThreadPoolExecutor(max_workers=5) as pool:numbers=list(pool.map(create,range(10)))
 assert len(set(numbers))==10
