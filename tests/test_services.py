import base64,re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi.testclient import TestClient
from test_workflow import app
H={'X-Requested-With':'ServiceDesk'}
P='TestPassword123!'
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1cAAAAASUVORK5CYII=')
def login(c,email):assert c.post('/api/login',json={'email':email,'password':P},headers=H).status_code==200

def test_sr_simple_approval_preview_notifications_and_return():
 with TestClient(app) as admin,TestClient(app) as infra,TestClient(app) as tl,TestClient(app) as manager,TestClient(app) as requester:
  login(admin,'admin@example.com')
  for c,role in [(infra,'infra'),(tl,'infra_tl'),(manager,'infra_manager'),(requester,'requester')]:
   email='sr-'+role+'@example.com';assert admin.post('/api/users',json=dict(name=role,email=email,role=role,password=P),headers=H).status_code==200;login(c,email)
  assert requester.get('/api/services').status_code==403
  assert requester.get('/api/services/notifications').status_code==403
  r=infra.post('/api/services',json={},headers=H).json();path='/api/services/'+str(r['id']);number=r['number']
  assert re.fullmatch('SR#MOMCC-'+datetime.now(ZoneInfo('Asia/Singapore')).strftime('%Y%m%d')+'-\\d{2,}',number)
  assert r['status']=='In-progress'
  assert infra.get('/api/services').json()['counts']=={'Submitted SR':0,'Pending Approval':0,'Total SR':1}
  def act(c,action,comment=''):return c.post(path+'/actions',json=dict(version=r['version'],action=action,comment=comment),headers=H)
  assert act(infra,'submit').status_code==422
  old=r['version'];r=infra.post(path+'/save',json=dict(version=old,subject='Account access',description='Please provision access',uploads=[dict(filename='screenshot.png',data=base64.b64encode(PNG).decode()),dict(filename='note.svg',data=base64.b64encode(b'<svg onload="alert(1)"></svg>').decode())]),headers=H).json()
  assert infra.post(path+'/save',json=dict(version=old,subject='stale'),headers=H).status_code==409
  assert len(r['files'])==2 and r['files'][0]['preview'] and not r['files'][1]['preview']
  fpath=path+'/files/'+str(r['files'][0]['id'])
  response=tl.get(fpath+'?preview=true');assert response.status_code==200 and response.headers['content-type']=='image/png' and response.content==PNG
  assert response.headers['content-disposition'].startswith('inline')
  assert requester.get(fpath+'?preview=true').status_code==403
  svg=tl.get(path+'/files/'+str(r['files'][1]['id'])+'?preview=true');assert svg.headers['content-disposition'].startswith('attachment')
  r=act(infra,'submit').json();assert r['status']=='Pending Approval'
  assert infra.get('/api/services').json()['counts']=={'Submitted SR':1,'Pending Approval':1,'Total SR':1}
  assert len(tl.get('/api/services/notifications').json())==1
  assert len(manager.get('/api/services/notifications').json())==1
  assert act(infra,'approve').status_code==403
  assert infra.post(path+'/save',json=dict(version=r['version']),headers=H).status_code==409
  assert act(tl,'return').status_code==422
  r=act(tl,'return','Add justification').json();assert r['status']=='In-progress'
  assert 'Add justification' in infra.get('/api/services/notifications').json()[0]['text']
  r=infra.post(path+'/save',json=dict(version=r['version'],subject='Account access',description='Updated justification',remove_file_ids=[r['files'][1]['id']]),headers=H).json();assert len(r['files'])==1
  r=act(infra,'submit').json();r=act(manager,'approve').json();assert r['status']=='Approved' and r['number']==number
  assert act(tl,'approve').status_code==409
  assert infra.get('/api/services').json()['counts']=={'Submitted SR':1,'Pending Approval':0,'Total SR':1}
  assert any('Approve:' in n['text'] for n in infra.get('/api/services/notifications').json())
  infra.post('/api/services/notifications/read',json={},headers=H)
  assert all(n['read'] for n in infra.get('/api/services/notifications').json())
  # TL alone can approve the next independent SR without Manager involvement.
  r=infra.post('/api/services',json={},headers=H).json();path='/api/services/'+str(r['id'])
  r=infra.post(path+'/save',json=dict(version=r['version'],subject='Install software',description='Approved package'),headers=H).json()
  r=act(infra,'submit').json();r=act(tl,'approve').json();assert r['status']=='Approved'

def test_sr_concurrent_numbers_and_self_approval():
 def create(_):
  with TestClient(app) as c:
   login(c,'sr-infra@example.com');r=c.post('/api/services',json={},headers=H);assert r.status_code==200;return r.json()['number']
 with ThreadPoolExecutor(max_workers=5) as pool:numbers=list(pool.map(create,range(10)))
 assert len(set(numbers))==10
 with TestClient(app) as tl,TestClient(app) as admin:
  login(tl,'sr-infra_tl@example.com');login(admin,'admin@example.com')
  r=tl.post('/api/services',json={},headers=H).json();p='/api/services/'+str(r['id'])
  r=tl.post(p+'/save',json=dict(version=r['version'],subject='Test',description='Own SR'),headers=H).json()
  r=tl.post(p+'/actions',json=dict(version=r['version'],action='submit'),headers=H).json()
  assert tl.post(p+'/actions',json=dict(version=r['version'],action='approve'),headers=H).status_code==403
  assert admin.post(p+'/actions',json=dict(version=r['version'],action='approve'),headers=H).json()['status']=='Approved'

def test_admin_sr_delete_cleans_files_history_notifications_and_keeps_sequence():
 from sqlalchemy import select,func
 from sqlalchemy.orm import Session
 from app.main import engine
 from app.services import SRFile,SRHistory,SRNotification
 with TestClient(app) as admin,TestClient(app) as infra,TestClient(app) as tl,TestClient(app) as requester:
  login(admin,'admin@example.com');login(infra,'sr-infra@example.com');login(tl,'sr-infra_tl@example.com');login(requester,'sr-requester@example.com')
  r=infra.post('/api/services',json={},headers=H).json();p='/api/services/'+str(r['id']);number=r['number']
  r=infra.post(p+'/save',json=dict(version=r['version'],subject='Delete test',description='Temporary SR',uploads=[dict(filename='image.png',data=base64.b64encode(PNG).decode())]),headers=H).json();file_id=r['files'][0]['id']
  r=infra.post(p+'/actions',json=dict(version=r['version'],action='submit'),headers=H).json()
  for c in [infra,tl,requester]:assert c.post(p+'/delete',json=dict(version=r['version']),headers=H).status_code==403
  assert admin.post(p+'/delete',json=dict(version=r['version']-1),headers=H).status_code==409
  assert admin.get(p).status_code==200
  assert admin.post(p+'/delete',json=dict(version=r['version']),headers=H).status_code==200
  assert admin.get(p).status_code==404 and admin.get(p+'/files/'+str(file_id)).status_code==404
  with Session(engine) as s:
   for model in [SRFile,SRHistory,SRNotification]:assert s.scalar(select(func.count()).select_from(model).where(model.sr_id==r['id']))==0
  next_sr=infra.post('/api/services',json={},headers=H).json()
  assert int(next_sr['number'].rsplit('-',1)[1])>int(number.rsplit('-',1)[1])
