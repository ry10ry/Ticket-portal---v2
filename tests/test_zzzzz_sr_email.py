import base64
from pathlib import Path
from email import policy
from email.parser import BytesParser
from fastapi.testclient import TestClient
from app.main import app
from test_services import H, PNG, login


def test_sr_customer_email_current_approval_files_screenshots_and_record():
 with TestClient(app) as infra,TestClient(app) as tl,TestClient(app) as manager,TestClient(app) as requester:
  for c,email in [(infra,'sr-infra@example.com'),(tl,'sr-infra_tl@example.com'),(manager,'sr-infra_manager@example.com'),(requester,'sr-requester@example.com')]:login(c,email)
  r=infra.post('/api/services',json={'sr_date':'2026-10-16'},headers=H).json();p='/api/services/'+str(r['id'])
  assert infra.get(p+'/customer-email').status_code==409
  assert requester.get(p+'/customer-email').status_code==403
  workbook=(Path(__file__).parents[1]/'app/templates/runbook.xlsx').read_bytes()
  uploads=[dict(filename=name,data=base64.b64encode(data).decode()) for name,data in [('Visitor list.xlsx',workbook),('screenshot.png',PNG),('old.txt',b'Old attachment')]]
  r=infra.post(p+'/save',json=dict(version=r['version'],subject='Old subject',description='Old description',uploads=uploads),headers=H).json()
  def act(c,action,comment=''):
   return c.post(p+'/actions',json=dict(version=r['version'],action=action,comment=comment),headers=H)
  r=act(infra,'submit').json();assert infra.get(p+'/customer-email').status_code==409
  r=act(tl,'return','Update request').json();assert infra.get(p+'/customer-email').status_code==409
  old_file=next(f['id'] for f in r['files'] if f['filename']=='old.txt')
  description='For your approval on clearance.\nThank you.\n<script>alert(1)</script>'
  r=infra.post(p+'/save',json=dict(version=r['version'],subject='MOMCC - Clearance for NCS Hub C4',description=description,remove_file_ids=[old_file]),headers=H).json()
  r=act(infra,'submit').json();r=act(manager,'approve','Approved for customer review').json()
  approved_version=r['version']
  r=act(infra,'comment','Follow-up note').json()
  response=infra.get(p+'/customer-email');assert response.status_code==200
  msg=BytesParser(policy=policy.default).parsebytes(response.content)
  assert msg['X-Unsent']=='1' and msg['To'] is None and msg['Cc'] is None and msg['From'] is None
  assert msg['Subject']=='MOMCC - Clearance for NCS Hub C4'
  assert msg.get_body(preferencelist=('plain',)).get_content().replace('\r\n','\n').rstrip()=='For your approval on clearance for access to NCS Hub Block C Level 4. Thank you.\n\n'+description
  html=msg.get_body(preferencelist=('html',)).get_content()
  assert '<script>' not in html and '&lt;script&gt;' in html and '<br>' in html
  image=next(part for part in msg.walk() if part.get_content_type()=='image/png')
  assert image.get_payload(decode=True)==PNG and image.get_content_disposition()=='inline'
  assert image['Content-ID'].strip('<>') in html and image.get_filename()=='screenshot.png'
  attachments=list(msg.iter_attachments());assert len(attachments)==2
  excel=next(part for part in attachments if part.get_filename()=='Visitor list.xlsx')
  assert excel.get_payload(decode=True)==workbook
  record=next(part for part in attachments if part.get_filename().endswith('Approval Record.txt')).get_payload(decode=True).decode()
  assert 'MOMCC Infra Manager' in record and 'sr-infra_manager@example.com' in record and 'SGT' in record
  assert 'Approved for customer review' in record and 'SR revision: '+str(approved_version) in record
  assert 'Update request' not in record and 'old.txt' not in record
  assert 'Approval Record' not in html and 'sr-infra_manager@example.com' not in html
  assert r['history'][-2]['snapshot']['_actor']['role']=='infra_manager'
  # TL approval is equally valid and an SR without uploads still includes the approval record.
  r=infra.post('/api/services',json={'sr_date':'2026-10-16'},headers=H).json();p='/api/services/'+str(r['id'])
  r=infra.post(p+'/save',json=dict(version=r['version'],subject='Software request',description='Please approve.'),headers=H).json()
  r=act(infra,'submit').json();r=act(tl,'approve').json()
  msg=BytesParser(policy=policy.default).parsebytes(infra.get(p+'/customer-email').content)
  parts=list(msg.iter_attachments());assert len(parts)==1
  assert 'MOMCC Infra TL' in parts[0].get_payload(decode=True).decode()


def test_sr_email_rejects_stale_content_files_and_missing_approval():
 import json
 import pytest
 from types import SimpleNamespace
 from fastapi import HTTPException
 from app.sr_email import current_approval
 row=SimpleNamespace(status='Approved',subject='Subject',description='Description')
 def history(action,files=None,subject='Subject'):
  return SimpleNamespace(action=action,snapshot=json.dumps(dict(subject=subject,description='Description',files=[] if files is None else files)))
 for records,files in [([history('approve')],[]),([history('submit'),history('return'),history('approve')],[]),
                       ([history('submit'),history('approve',subject='Changed')],[]),
                       ([history('submit'),history('approve')],[SimpleNamespace(id=1)])]:
  with pytest.raises(HTTPException) as error:current_approval(row,records,files)
  assert error.value.status_code==409
