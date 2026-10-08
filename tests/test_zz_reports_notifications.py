import csv,io
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.main import app,engine,User,Ticket,Notification
from app.services import ServiceRequest
from test_z_changes import uploads
H={'X-Requested-With':'ServiceDesk'}
def login(c,email):assert c.post('/api/login',json={'email':email,'password':'TestPassword123!'},headers=H).status_code==200

def test_cr_sr_combined_notifications_and_separate_fault_reads():
 with TestClient(app) as infra,TestClient(app) as tl,TestClient(app) as manager,TestClient(app) as requester:
  login(infra,'cr-infra@example.com');login(tl,'cr-infra_tl@example.com');login(manager,'cr-infra_manager@example.com');login(requester,'cr-requester@example.com')
  r=infra.post('/api/changes',json={},headers=H).json();p='/api/changes/'+str(r['id'])
  r=infra.post(p+'/save',json=dict(version=r['version'],content=dict(background='Reason',scope='Scope'),uploads=uploads()),headers=H).json()
  r=infra.post(p+'/actions',json=dict(version=r['version'],action='submit'),headers=H).json()
  assert any(n['kind']=='CR' and n['record_id']==r['id'] for n in tl.get('/api/internal/notifications').json())
  r=tl.post(p+'/actions',json=dict(version=r['version'],action='support'),headers=H).json()
  assert any(n['kind']=='CR' and n['record_id']==r['id'] for n in manager.get('/api/internal/notifications').json())
  r=manager.post(p+'/actions',json=dict(version=r['version'],action='approve'),headers=H).json()
  assert any(n['kind']=='CR' and n['record_id']==r['id'] for n in infra.get('/api/internal/notifications').json())
  sr=infra.post('/api/services',json={},headers=H).json();sp='/api/services/'+str(sr['id'])
  sr=infra.post(sp+'/save',json=dict(version=sr['version'],subject='Test',description='Request'),headers=H).json()
  infra.post(sp+'/actions',json=dict(version=sr['version'],action='submit'),headers=H)
  ns=tl.get('/api/internal/notifications').json();assert {n['kind'] for n in ns}=={'CR','SR'}
  assert len({n['id'] for n in ns})==len(ns)
  assert requester.get('/api/internal/notifications').status_code==403
  with Session(engine) as s:
   user=s.scalar(select(User).where(User.email=='cr-infra_tl@example.com'));ticket=s.scalar(select(Ticket))
   s.add(Notification(user_id=user.id,ticket_id=ticket.id,text='FAULT ONLY'));s.commit()
  assert not any(n['text']=='FAULT ONLY' for n in ns)
  tl.post('/api/internal/notifications/read',json={},headers=H)
  assert all(n['read'] for n in tl.get('/api/internal/notifications').json())
  assert any(n['text']=='FAULT ONLY' and not n['read'] for n in tl.get('/api/notifications').json())

def test_monthly_reports_singapore_boundaries_and_formula_escaping():
 with TestClient(app) as admin,TestClient(app) as requester:
  login(admin,'admin@example.com');login(requester,'cr-requester@example.com')
  ids=[]
  for when in [datetime(2026,9,30,15,59,59),datetime(2026,9,30,16),datetime(2026,10,31,15,59,59),datetime(2026,10,31,16)]:
   r=admin.post('/api/services',json={},headers=H).json()
   admin.post('/api/services/'+str(r['id'])+'/save',json=dict(version=r['version'],subject='=SUM(1,1)',description='line1\nline2'),headers=H)
   with Session(engine) as s:s.get(ServiceRequest,r['id']).created_at=when;s.commit()
   ids.append(r['number'])
  response=admin.get('/api/internal/reports/sr?month=2026-10');assert response.status_code==200
  rows=list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
  assert ids[1] in [r['SR Number'] for r in rows] and ids[2] in [r['SR Number'] for r in rows]
  assert ids[0] not in [r['SR Number'] for r in rows] and ids[3] not in [r['SR Number'] for r in rows]
  assert next(r for r in rows if r['SR Number']==ids[1])['Created SGT']=='2026-10-01 00:00:00'
  assert next(r for r in rows if r['SR Number']==ids[1])['Subject']=="'=SUM(1,1)"
  cr=admin.get('/api/internal/reports/cr?month='+datetime.now(ZoneInfo('Asia/Singapore')).strftime('%Y-%m'));assert cr.status_code==200
  assert 'Background / Reason for Change' in cr.text and 'TL Supported By' in cr.text
  for kind in ['cr','sr']:
   assert requester.get('/api/internal/reports/'+kind+'?month=2026-10').status_code==403
   assert admin.get('/api/internal/reports/'+kind+'?month=2026-13').status_code==422
   assert admin.get('/api/internal/reports/'+kind+'?month=invalid').status_code==422
