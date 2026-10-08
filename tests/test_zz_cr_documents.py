import io
import re
import zipfile
from urllib.parse import unquote
from lxml import etree as ET
from fastapi.testclient import TestClient
from app.main import app
from app.cr_documents import names,schema,export_xlsx,NS

HEADERS={'X-Requested-With':'ServiceDesk'}

def login(c,email):
    assert c.post('/api/login',json={'email':email,'password':'TestPassword123!'},headers=HEADERS).status_code==200

def form_values():
    values={kind:{} for kind in schema()}
    for kind,fields in schema().items():
        for f in fields:
            if f['required']:
                values[kind][f['key']]=f['options'][0] if f['options'] else '2026-11-01' if f['kind']=='date' else 'Filled template detail'
    values['runbook'].update({'RunBook!C7':'Upload recordings','RunBook!C19':'Rollback if verification fails','RunBook!E7':'30','RunBook!F7':'2026-11-01T20:00','Actual Start-End!A2':'Server1','Actual Start-End!B2':'1 Nov 2026 20:00','Contact  List!D2':'New contact'})
    values['checklist'].update(business='Low',technical='Low',completed_by='Test Infra')
    return values

def test_structured_forms_export_names_roundtrip_and_permissions():
    with TestClient(app) as infra, TestClient(app) as requester, TestClient(app) as admin:
        login(infra,'cr-infra@example.com');login(requester,'cr-requester@example.com');login(admin,'admin@example.com')
        assert requester.get('/api/changes/forms/schema').status_code==403
        assert infra.get('/api/changes/forms/schema').status_code==200
        r=infra.post('/api/changes',json={},headers=HEADERS).json();path='/api/changes/'+str(r['id'])
        content={'background':'Retention requirement','scope':'Archive records','description':'Upload recordings to S3','environment':'PRD','deployment_start':'2026-11-01T20:00:00+08:00','deployment_end':'2026-11-01T23:00:00+08:00','forms':form_values()}
        content['impact']='No impact to users or Ops.'
        bad={**content,'forms':{'runbook':{'RunBook!ZZ99':'=HYPERLINK("evil")'}}}
        assert infra.post(path+'/save',json={'version':r['version'],'content':bad},headers=HEADERS).status_code==422
        r=infra.post(path+'/save',json={'version':r['version'],'content':content},headers=HEADERS).json()
        assert r['content']['forms']==content['forms']
        assert r['document_names']['change_form'].endswith(' - Upload recordings to S3.docx')
        assert 'CR#MOMCC-' in r['document_names']['change_form']
        docs={}
        for kind in schema():
            response=infra.get(path+'/documents/'+kind)
            assert response.status_code==200
            filename=unquote(response.headers['content-disposition'].split("''",1)[1])
            assert filename==r['document_names'][kind]
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                assert z.testzip() is None
                docs[kind]={n:z.read(n) for n in z.namelist()}
            assert requester.get(path+'/documents/'+kind).status_code==403
            assert admin.get(path+'/documents/'+kind).status_code==200
        xml=ET.fromstring(docs['change_form']['word/document.xml'])
        text=' '.join(xml.xpath('//w:t/text()',namespaces=NS))
        assert 'Filled template detail' in text and 'Vincent Lau' not in text
        assert 'Tannie Yeoh' not in text and 'Will Koa' not in text
        assert 'Pending' in text and r['number'].replace('CR# ','CR#') in text
        # Template images, relationships and all non-document XML assets survive unchanged.
        from app.cr_documents import ROOT
        with zipfile.ZipFile(ROOT/'cr-form.docx') as z:
            for n in z.namelist():
                if n!='word/document.xml':assert docs['change_form'][n]==z.read(n)
        run=ET.fromstring(docs['runbook']['xl/worksheets/sheet1.xml'])
        assert run.find('.//x:c[@r="G7"]/x:f',NS).text=='F7+TIME(0,E7,0)'
        assert run.find('.//x:c[@r="E7"]/x:v',NS).text=='30'
        assert 'Upload recordings' in ET.tostring(run).decode()
        actual=ET.fromstring(docs['runbook']['xl/worksheets/sheet3.xml'])
        assert not actual.xpath('//x:t[contains(text(),"BBPMS")]',namespaces=NS)
        checklist=ET.fromstring(docs['checklist']['xl/worksheets/sheet1.xml'])
        assert checklist.find('.//x:c[@r="C22"]/x:is/x:t',NS).text=='Minor / Localized'
        assert checklist.find('.//x:c[@r="C23"]/x:is/x:t',NS).text=='Test Infra'
        assert infra.get(path+'/documents/unknown').status_code==404
        incomplete={**content,'forms':{'change_form':{},'runbook':{},'checklist':{}}}
        r=infra.post(path+'/save',json={'version':r['version'],'content':incomplete},headers=HEADERS).json()
        assert infra.post(path+'/actions',json={'version':r['version'],'action':'submit'},headers=HEADERS).status_code==422
        r=infra.post(path+'/save',json={'version':r['version'],'content':content},headers=HEADERS).json()
        r=infra.post(path+'/actions',json={'version':r['version'],'action':'submit'},headers=HEADERS).json()
        assert r['status']=='Pending Review'
        r=admin.post(path+'/actions',json={'version':r['version'],'action':'support'},headers=HEADERS).json()
        r=admin.post(path+'/actions',json={'version':r['version'],'action':'approve'},headers=HEADERS).json()
        with zipfile.ZipFile(io.BytesIO(admin.get(path+'/documents/change_form').content)) as z:
            text=' '.join(ET.fromstring(z.read('word/document.xml')).xpath('//w:t/text()',namespaces=NS))
        assert 'Pending' not in text and 'ServiceDesk Administrator' in text
        assert r['history'][-1]['content']['forms']==content['forms']

def test_names_and_formula_injection_protection():
    result=names('CR# MOMCC-20261101-01','日本語 / report\\name\r\n:*?<>|')
    assert result['runbook']=='CR#MOMCC-20261101-01 - Runbook.xlsx'
    assert result['checklist']=='Checklist-RFC-Impact_v1.0.xlsx'
    assert not re.search(r'[\\/\r\n:*?<>|]',result['change_form'])
    row={'number':'CR# MOMCC-20261101-01','content':{'forms':{'runbook':{'RunBook!C7':'=HYPERLINK("https://example.com")'}}}}
    with zipfile.ZipFile(io.BytesIO(export_xlsx(row,'runbook'))) as z:
        tree=ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
    cell=tree.find('.//x:c[@r="C7"]',NS)
    assert cell.get('t')=='inlineStr' and cell.find('x:f',NS) is None
