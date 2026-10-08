import base64
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
  assert r['status']=='In-progress' and i.get('/api/changes').json()['counts']['In-progress']==1
  def act(c,action,reason=''):
   return c.post(path+'/actions',json=dict(version=r['version'],action=action,reason=reason),headers=H)
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
