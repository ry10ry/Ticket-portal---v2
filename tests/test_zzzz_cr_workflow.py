from fastapi.testclient import TestClient
from email import policy
from email.parser import BytesParser
from app.main import app
from test_z_changes import H, login, uploads


def test_editable_stages_pin_roles_return_and_email_records():
 with TestClient(app) as admin, TestClient(app) as infra, TestClient(app) as tl, TestClient(app) as manager:
  for c,email in [(admin,'admin@example.com'),(infra,'cr-infra@example.com'),(tl,'cr-infra_tl@example.com'),(manager,'cr-infra_manager@example.com')]:login(c,email)
  original=admin.get('/api/cr-workflow').json()
  assert infra.get('/api/cr-workflow').status_code==403
  stages=[dict(label='Technical support',role='infra_tl',action='support'),dict(label='Manager sign-off',role='infra_manager',action='approve'),dict(label='Final approval',role='admin',action='approve')]
  assert infra.post('/api/cr-workflow',json=dict(version=original['version'],stages=stages),headers=H).status_code==403
  for invalid in [[],[dict(label=' ',role='infra_tl',action='approve')],[dict(label='Review',role='requester',action='approve')],[dict(label='Review',role='infra_tl',action='support')]]:
   assert admin.post('/api/cr-workflow',json=dict(version=original['version'],stages=invalid),headers=H).status_code==422
  legacy=infra.post('/api/changes',json={'cr_date':'2026-12-01'},headers=H).json()
  saved=admin.post('/api/cr-workflow',json=dict(version=original['version'],stages=stages),headers=H).json()
  assert admin.post('/api/cr-workflow',json=dict(version=original['version'],stages=stages),headers=H).status_code==409
  assert infra.get('/api/changes/'+str(legacy['id'])).json()['workflow']==original['stages']
  r=infra.post('/api/changes',json={'cr_date':'2026-12-01'},headers=H).json();p='/api/changes/'+str(r['id'])
  try:
   assert r['workflow']==stages
   # New configuration cannot change a CR that already exists.
   changed=admin.post('/api/cr-workflow',json=dict(version=saved['version'],stages=[stages[1]]),headers=H).json()
   r=infra.post(p+'/save',json=dict(version=r['version'],content=dict(background='Reason',scope='Scope'),uploads=uploads()),headers=H).json()
   def act(c,action,reason=''):
    return c.post(p+'/actions',json=dict(version=r['version'],action=action,reason=reason),headers=H)
   r=act(infra,'submit').json();assert r['active_stage']['label']=='Technical support'
   assert act(manager,'approve').status_code==403
   r=act(tl,'support').json();assert r['active_stage']['index']==1
   r=act(manager,'approve').json();assert r['status']=='Pending Approval' and r['active_stage']['role']=='admin'
   assert infra.get(p+'/istd-email').status_code==409
   assert act(manager,'approve').status_code==403
   r=act(admin,'return','Update the plan').json();assert r['status']=='In-progress'
   r=act(infra,'submit').json();assert r['active_stage']['index']==0
   r=act(tl,'support').json();r=act(manager,'approve').json();r=act(admin,'approve').json()
   assert r['status']=='Approved' and r['active_stage'] is None
   email=infra.get(p+'/istd-email');assert email.status_code==200
   msg=BytesParser(policy=policy.default).parsebytes(email.content)
   record=next(part for part in msg.iter_attachments() if part.get_filename().endswith('Record.txt')).get_payload(decode=True).decode()
   for stage in stages:assert stage['label'] in record
   # A single manager approval is also valid, without manufacturing TL support.
   single=infra.post('/api/changes',json={'cr_date':'2026-12-01'},headers=H).json();sp='/api/changes/'+str(single['id'])
   single=infra.post(sp+'/save',json=dict(version=single['version'],content=dict(background='Reason',scope='Scope'),uploads=uploads()),headers=H).json()
   single=infra.post(sp+'/actions',json=dict(version=single['version'],action='submit'),headers=H).json()
   assert single['active_stage']['role']=='infra_manager'
   single=manager.post(sp+'/actions',json=dict(version=single['version'],action='approve'),headers=H).json()
   assert single['status']=='Approved'
   assert infra.get(sp+'/istd-email').status_code==200
  finally:
   current=admin.get('/api/cr-workflow').json()
   assert admin.post('/api/cr-workflow',json=dict(version=current['version'],stages=original['stages']),headers=H).status_code==200
