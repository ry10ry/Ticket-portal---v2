import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi.testclient import TestClient
from app.main import app

HEADERS={'X-Requested-With':'ServiceDesk'}
PASSWORD='TestPassword123!'

def login(client,email):
    assert client.post('/api/login',json={'email':email,'password':PASSWORD},headers=HEADERS).status_code==200

def post(c,path,body):
    return c.post('/api/changes'+path,json=body,headers=HEADERS)

def test_cr_approval_returns_permissions_and_history():
    with TestClient(app) as admin, TestClient(app) as infra, TestClient(app) as tl, TestClient(app) as manager, TestClient(app) as requester:
        login(admin,'admin@example.com')
        for c,role in [(infra,'infra'),(tl,'infra_tl'),(manager,'infra_manager'),(requester,'requester')]:
            email=f'cr-{role}@example.com'
            assert admin.post('/api/users',json={'name':role,'email':email,'role':role,'password':PASSWORD},headers=HEADERS).status_code==200
            login(c,email)
        assert requester.get('/api/changes').status_code==403
        assert post(requester,'',{}).status_code==403
        r=post(infra,'',{}).json(); path=f"/{r['id']}"
        day=datetime.now(ZoneInfo('Asia/Singapore')).strftime('%Y%m%d')
        assert re.fullmatch(r'CR# MOMCC-'+day+r'-\d{2,}',r['number'])
        number=r['number']
        assert requester.get('/api/changes'+path).status_code==403
        assert post(infra,path+'/actions',{'version':r['version'],'action':'submit'}).status_code==422
        content=dict(background='Reason for change',scope='Upload archived recordings',environment='PRD',description='Upload to S3',deployment_start='2026-10-16T10:00:00+08:00',deployment_end='2026-10-26T18:00:00+08:00',impact='No impact to users or Ops.',change_form='CR form details',runbook='Runbook steps',checklist='RFC impact checklist')
        invalid={**content,'deployment_end':'2026-10-15T00:00:00+08:00'}
        assert post(infra,path+'/save',{'version':r['version'],'content':invalid}).status_code==422
        assert post(tl,path+'/save',{'version':r['version'],'content':content}).status_code==403
        stale=r['version'];r=post(infra,path+'/save',{'version':stale,'content':content}).json()
        assert post(infra,path+'/save',{'version':stale,'content':content}).status_code==409
        r=post(infra,path+'/actions',{'version':r['version'],'action':'submit'}).json()
        assert r['status']=='Pending Review'
        assert post(infra,path+'/save',{'version':r['version'],'content':content}).status_code==409
        assert post(manager,path+'/actions',{'version':r['version'],'action':'approve'}).status_code==403
        assert post(tl,path+'/actions',{'version':r['version'],'action':'return'}).status_code==422
        r=post(tl,path+'/actions',{'version':r['version'],'action':'return','reason':'Add rollback details'}).json()
        assert r['status']=='In-progress'
        content['runbook']='Runbook with rollback details'
        r=post(infra,path+'/save',{'version':r['version'],'content':content}).json()
        r=post(infra,path+'/actions',{'version':r['version'],'action':'submit'}).json()
        r=post(tl,path+'/actions',{'version':r['version'],'action':'support'}).json()
        assert r['status']=='Pending Approval'
        assert post(tl,path+'/actions',{'version':r['version'],'action':'approve'}).status_code==403
        r=post(manager,path+'/actions',{'version':r['version'],'action':'return','reason':'Clarify impact'}).json()
        assert r['status']=='In-progress' and r['number']==number
        r=post(infra,path+'/actions',{'version':r['version'],'action':'submit'}).json()
        assert r['status']=='Pending Review'
        r=post(tl,path+'/actions',{'version':r['version'],'action':'support'}).json()
        r=post(manager,path+'/actions',{'version':r['version'],'action':'approve'}).json()
        assert r['status']=='Approved'
        assert post(infra,path+'/save',{'version':r['version'],'content':content}).status_code==409
        assert [h['reason'] for h in r['history'] if h['action']=='return']==['Add rollback details','Clarify impact']
        assert r['history'][1]['content']['runbook']=='Runbook steps'
        assert admin.get('/api/changes'+path).status_code==200
        counts=infra.get('/api/changes').json()['counts']
        assert counts['Total CR']==1 and counts['Pending Review']==0 and counts['Pending Approval']==0 and counts['In-progress']==0
        assert infra.post('/api/changes',json={}).status_code==403

def test_cr_concurrent_numbers_and_admin_override():
    def create():
        with TestClient(app) as c:
            login(c,'cr-infra@example.com')
            response=post(c,'',{})
            assert response.status_code==200
            return response.json()
    with ThreadPoolExecutor(max_workers=5) as pool:
        rows=list(pool.map(lambda _:create(),range(10)))
    numbers=[r['number'] for r in rows]
    assert len(set(numbers))==10
    sequences=sorted(int(n.rsplit('-',1)[1]) for n in numbers)
    assert sequences==list(range(sequences[0],sequences[0]+10))
    with TestClient(app) as admin, TestClient(app) as tl:
        login(admin,'admin@example.com');login(tl,'cr-infra_tl@example.com')
        r=post(tl,'',{}).json();path=f"/{r['id']}"
        content={k:'Complete content' for k in ['background','scope','environment','description','impact','change_form','runbook','checklist']}
        content.update(deployment_start='2026-10-16T00:00:00+08:00',deployment_end='2026-10-26T00:00:00+08:00')
        r=post(admin,path+'/save',{'version':r['version'],'content':content}).json()
        r=post(tl,path+'/actions',{'version':r['version'],'action':'submit'}).json()
        assert post(tl,path+'/actions',{'version':r['version'],'action':'support'}).status_code==403
        r=post(admin,path+'/actions',{'version':r['version'],'action':'support'}).json()
        r=post(admin,path+'/actions',{'version':r['version'],'action':'approve'}).json()
        assert r['status']=='Approved'
        counts=admin.get('/api/changes').json()['counts']
        assert counts['In-progress']==10 and counts['Total CR']==12
