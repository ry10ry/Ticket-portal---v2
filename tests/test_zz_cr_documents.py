import io,zipfile
from pathlib import Path
from app.cr_documents import names,export_doc,export_xlsx

def test_templates_remain_valid_office_files():
 row=dict(number='CR# MOMCC-20261101-01',owner='Infra',created_at='2026-11-01',status='In-progress',content={})
 for kind in ['change_form','runbook','checklist']:
  data=export_doc(row,[]) if kind=='change_form' else export_xlsx(row,kind)
  with zipfile.ZipFile(io.BytesIO(data)) as z:assert z.testzip() is None
 assert names(row['number'],'')['runbook']=='CR#MOMCC-20261101-01 - Runbook.xlsx'
