import json
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app.cr_email import current_approval_cycle

def record(id,action,background='Reason',files=None):
 return SimpleNamespace(id=id,action=action,snapshot=json.dumps({'background':background,'scope':'Scope','_files':files if files is not None else [{'kind':k,'id':i} for i,k in enumerate(['change_form','runbook','checklist'],1)]}))

def test_email_rejects_missing_or_stale_approval():
 row=SimpleNamespace(status='Approved',content=json.dumps({'background':'Reason','scope':'Scope'}))
 for history in [[record(1,'submit'),record(2,'approve')],
                 [record(1,'submit'),record(2,'support'),record(3,'return'),record(4,'approve')],
                 [record(1,'submit'),record(2,'support','Old reason'),record(3,'approve')],
                 [record(1,'submit'),record(2,'support'),record(3,'approve',files=[])],
                 [record(1,'support'),record(2,'approve'),record(3,'submit')]]:
  with pytest.raises(HTTPException) as error:current_approval_cycle(row,history)
  assert error.value.status_code==409

def test_email_uses_only_the_resubmitted_approval_cycle():
 row=SimpleNamespace(status='Approved',content=json.dumps({'background':'Reason','scope':'Scope'}))
 history=[record(1,'submit'),record(2,'support'),record(3,'return'),record(4,'submit'),record(5,'support'),record(6,'approve')]
 submission,support,approval,files=current_approval_cycle(row,history)
 assert (submission.id,support.id,approval.id)==(4,5,6)
 assert len(files)==3
