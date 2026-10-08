"""Template-backed form schema and OOXML exports; untouched assets stay intact."""
import io
import re
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from lxml import etree as ET

ROOT = Path(__file__).parent / 'templates'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
X = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
NS = {'w': W, 'x': X}
for prefix, uri in [('w', W), ('r', 'http://schemas.openxmlformats.org/officeDocument/2006/relationships')]:
    ET.register_namespace(prefix, uri)


def field(key, label, kind='text', options=None, required=False):
    return dict(key=key, label=label, kind=kind, options=options, required=required)


def schema():
    doc = [
        field('system_name', 'System Name', required=True),
        field('change_type', 'Type of Change', 'select', ['O/S','Software','Hardware','Job schedule','Others'], True),
        field('change_other', 'Others — specify type'),
        field('reason_type', 'Reason for Change', 'select', ['New Requirement','Upgrade','Bug Fixes','Others'], True),
        field('reason_other', 'Others — specify reason'),
        field('component', 'Component affected — Hardware / Software / Parameter', required=True),
        field('current_version', 'Current version/value'), field('new_version', 'New version/value'),
        field('component_comments', 'Component comments', 'textarea'),
        field('impact_analysis', 'Part 2: Impact Analysis', 'textarea', required=True),
        field('contingency', 'Part 3: Highlight Contingency Plan (use N/A if not applicable)', 'textarea', required=True),
        field('istd_decision', 'Part 5: ISTD recorded decision', 'select', ['Proceed','Do not proceed','Pending','N/A']),
        field('istd_remarks', 'ISTD remarks', 'textarea'), field('istd_signoff', 'ISTD Name / Signature / Date'),
        field('crd_decision', 'Part 6: CRD recorded decision', 'select', ['Proceed','Do not proceed','Pending','N/A']),
        field('crd_remarks', 'CRD remarks', 'textarea'), field('crd_signoff', 'CRD Name / Signature / Date'),
    ]
    for category in ['hardware','software','administration','asset','others']:
        label={'hardware':'System Hardware','software':'System Software','administration':'System Administration','asset':'Asset list','others':'Others'}[category]
        doc += [field('fm_'+category+'_required', 'FM — '+label+' update required?', 'select', ['Yes','No','N/A']),
                field('fm_'+category+'_document', label+' — Document Name')]
    doc.append(field('fm_remarks', 'FM Remarks', 'textarea'))
    activity_rows=[5,*range(7,13),*range(14,18),*range(20,25),26]
    labels=['S/N','Planned Date / Period','Activities','Responsible Parties','Plan Duration (Min)','Plan Start Time','Plan End Time','Actual Plan Duration (Min)','Actual Start Time','Actual End Time']
    run=[field('RunBook!C19','Rollback decision date / time and criteria', 'textarea')]
    for row in activity_rows:
        for col,label in zip('ABCDEFGHIJ',labels):
            if col=='G': continue  # Keep the original calculated end-time formula.
            kind='number' if col in 'EH' else 'datetime-local' if col in 'FIJ' else 'textarea' if col=='C' else 'text'
            run.append(field(f'RunBook!{col}{row}',f'Row {row} — {label}',kind))
    # All original supporting sheets are represented, without inheriting old change data.
    for row in range(2,43):
        for col,label in [('B','Team'),('C','Initial'),('D','Name')]:
            run.append(field(f'Contact  List!{col}{row}',f'Contact {row-1} — {label}'))
    for row in range(2,49):
        for col,label in [('A','Hostname'),('B','Actual start time'),('C','Actual end time')]:
            run.append(field(f'Actual Start-End!{col}{row}',f'Actual record {row-1} — {label}'))
    questions={5:'1.1 Users affected — details',6:'1.2 Services affected — details',7:'1.3 Does total downtime exceed the allowed scheduled window?',8:'1.4 Is LD imposed if SLA is missed? Include details',11:'2.1 Configuration items affected — details',12:'2.2 Are changed CIs urgent / high criticality?',13:'2.3 Can the change be reverted using a fallback plan?',14:'2.4 Can rollback finish within the scheduled downtime window?'}
    checklist=[]
    for row,label in questions.items():
        checklist += [field(f'details_{row}',label,'textarea',required=True),field(f'rating_{row}',label+' — Impact Level','select',['Critical','High','Medium','Low','NA'],True)]
    for row,label in [(17,'Capacity of Production & Capacity Plan'),(18,'Disaster Recovery Resources & Plan'),(19,'Service Availability & Plan'),(20,'Security Management'),(21,'Suppliers & Supplier Contracts')]:
        checklist.append(field(f'resource_{row}',label+' — impact (N/A if none)','textarea',required=True))
    checklist += [field('business','Overall Business Impact Level','select',['Critical','High','Medium','Low'],True),field('technical','Overall Technical Impact Level','select',['Critical','High','Medium','Low'],True),field('completed_by','Completed By',required=True),field('completed_date','Date','date',required=True)]
    return {'change_form':doc,'runbook':run,'checklist':checklist}


def clean_number(number):
    return number.replace('CR# ', 'CR#')


def names(number, description):
    # Windows-safe, header-safe names, while preserving readable CR# and Unicode.
    description=re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', ' ', description).strip(' .')[:120].rstrip(' .')
    number=clean_number(number)
    return {'change_form':f'{number} - {description or "Change Request"}.docx',
            'runbook':f'{number} - Runbook.xlsx', 'checklist':'Checklist-RFC-Impact_v1.0.xlsx'}


def validate_forms(forms, submission=False):
    definitions=schema()
    if set(forms)-set(definitions): raise ValueError('Unknown form')
    missing=[]
    for kind, fields in definitions.items():
        values=forms.get(kind,{})
        keys={f['key'] for f in fields}
        if set(values)-keys: raise ValueError('Unknown field in '+kind)
        for f in fields:
            value=values.get(f['key'],'').strip()
            if len(value)>20000: raise ValueError('Form field is too long')
            if value and f['options'] and value not in f['options']: raise ValueError('Invalid selection: '+f['label'])
            if value and f['kind']=='number':
                try:
                    n=float(value)
                    if not 0<=n<=100000: raise ValueError()
                except ValueError: raise ValueError('Invalid number: '+f['label'])
            if value and f['kind'] in ('date','datetime-local'):
                try: datetime.fromisoformat(value)
                except ValueError: raise ValueError('Invalid date: '+f['label'])
            if submission and f['required'] and not value: missing.append(f['label'])
    if submission:
        run=forms.get('runbook',{})
        if not any(run.get(f'RunBook!C{r}','').strip() for r in [5,*range(7,13)]): missing.append('Runbook activities')
        if not run.get('RunBook!C19','').strip(): missing.append('Runbook rollback decision')
    if missing: raise ValueError('Complete before submitting: '+', '.join(missing))


def transform_zip(path, replacements):
    out=io.BytesIO()
    with zipfile.ZipFile(path) as source,zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as target:
        for entry in source.infolist(): target.writestr(entry,replacements.get(entry.filename,source.read(entry.filename)))
    return out.getvalue()


def cell_text(cell, text):
    texts=cell.findall('.//w:t',NS)
    if texts:
        texts[0].text=str(text)
        texts[0].set('{http://www.w3.org/XML/1998/namespace}space','preserve')
        for t in texts[1:]: t.text=''
    else:
        p=cell.find('w:p',NS)
        if p is None:p=ET.SubElement(cell,'{'+W+'}p')
        r=ET.SubElement(p,'{'+W+'}r');ET.SubElement(r,'{'+W+'}t').text=str(text)


def export_doc(row, history):
    path=ROOT/'cr-form.docx'
    with zipfile.ZipFile(path) as z: tree=ET.fromstring(z.read('word/document.xml'))
    tables=tree.findall('.//w:tbl',NS)
    def put(t,r,c,value):cell_text(tables[t].findall('w:tr',NS)[r].findall('w:tc',NS)[c],value)
    c=row['content'];v=c.get('forms',{}).get('change_form',{})
    put(1,0,1,str(row['created_at'])[:10]);put(1,0,3,clean_number(row['number']))
    put(1,1,1,v.get('system_name',''));put(1,2,1,c.get('environment',''))
    for col,label in enumerate(['O/S','Software','Hardware','Job schedule'],1):put(2,0,col,('☒ ' if v.get('change_type')==label else '☐ ')+label)
    put(2,1,1,('☒ ' if v.get('change_type')=='Others' else '☐ ')+'Others, please specify:');put(2,1,2,v.get('change_other',''))
    for col,label in enumerate(['New Requirement','Upgrade','Bug Fixes'],1):put(3,0,col,('☒ ' if v.get('reason_type')==label else '☐ ')+label)
    put(3,1,1,('☒ ' if v.get('reason_type')=='Others' else '☐ ')+'Others, please specify:');put(3,1,2,v.get('reason_other',''))
    for col,key in enumerate(['component','current_version','new_version','component_comments'],1):put(0,6,col,v.get(key,''))
    put(4,0,1,c.get('deployment_start',''));put(4,1,1,row['owner']+' / '+str(row['created_at'])[:10])
    put(5,2,0,v.get('impact_analysis',''));put(5,4,0,v.get('contingency',''))
    # Only the current Approved state carries an actual system Manager decision.
    approval=next((h for h in reversed(history) if h['action']=='approve'),None) if row['status']=='Approved' else None
    for table,decision,remarks in [(7,'Proceed' if approval else '',approval['reason'] if approval else ''),(8,v.get('istd_decision',''),v.get('istd_remarks','')),(9,v.get('crd_decision',''),v.get('crd_remarks',''))]:
        put(table,0,1,('☒ ' if decision=='Proceed' else '☐ ')+'Proceed')
        put(table,0,2,('☒ ' if decision=='Do not proceed' else '☐ ')+'Do not proceed')
        put(table,1,1,remarks)
    for r,signoff in [(2,(approval['actor']+' / '+str(approval['created_at'])) if approval else 'Pending'),(4,v.get('istd_signoff','')),(6,v.get('crd_signoff',''))]:
        cell=tables[6].findall('w:tr',NS)[r].findall('w:tc',NS)[0]
        for p in cell.findall('w:p',NS):
            if 'Name/' in ''.join(t.text or '' for t in p.findall('.//w:t',NS)):cell_text(p,'Name / Signature / Date: '+signoff)
    for r,key in enumerate(['hardware','software','administration','asset','others'],4):
        value=v.get('fm_'+key+'_required','')
        put(10,r,1,('☒' if value=='Yes' else '☐')+' Yes\n'+('☒' if value=='No' else '☐')+' No')
        put(10,r,3,v.get('fm_'+key+'_document',''))
    put(10,9,0,'Remarks:\n'+v.get('fm_remarks',''))
    return transform_zip(path,{'word/document.xml':ET.tostring(tree,encoding='utf-8',xml_declaration=True)})


def set_xcell(tree,ref,value,number=False):
    data=tree.find('x:sheetData',NS)
    rownum=int(re.search(r'\d+',ref).group())
    row=next((r for r in data if int(r.get('r'))==rownum),None)
    if row is None:
        row=ET.Element('{'+X+'}row',r=str(rownum));data.append(row)
    cell=next((c for c in row if c.get('r')==ref),None)
    if cell is None:cell=ET.SubElement(row,'{'+X+'}c',r=ref)
    for child in list(cell):cell.remove(child)
    if number and value!='':
        cell.attrib.pop('t',None);ET.SubElement(cell,'{'+X+'}v').text=str(value)
    else:
        cell.set('t','inlineStr');text=ET.SubElement(ET.SubElement(cell,'{'+X+'}is'),'{'+X+'}t');text.text=str(value);text.set('{http://www.w3.org/XML/1998/namespace}space','preserve')
    # Keep new cells in Excel's required column order.
    def column(c):
        n=0
        for ch in re.match('[A-Z]+',c.get('r')).group():n=n*26+ord(ch)-64
        return n
    row[:]=sorted(row,key=column)
    data[:]=sorted(data,key=lambda r:int(r.get('r')))


def excel_date(value):
    return (datetime.fromisoformat(value)-datetime(1899,12,30)).total_seconds()/86400


def export_xlsx(row,kind):
    path=ROOT/('runbook.xlsx' if kind=='runbook' else 'Checklist-RFC-Impact_v1.0.xlsx')
    with zipfile.ZipFile(path) as z:
        trees={n:ET.fromstring(z.read(n)) for n in z.namelist() if re.fullmatch(r'xl/worksheets/sheet\d+\.xml',n)}
        workbook=ET.fromstring(z.read('xl/workbook.xml'))
    c=row['content'];v=c.get('forms',{}).get(kind,{})
    if kind=='runbook':
        sheets={'RunBook':trees['xl/worksheets/sheet1.xml'],'Contact  List':trees['xl/worksheets/sheet2.xml'],'Actual Start-End':trees['xl/worksheets/sheet3.xml']}
        for f in schema()['runbook']:
            sheet,ref=f['key'].split('!');value=v.get(f['key'],'')
            # Formulas for chained planned start times remain when not overridden.
            original=sheets[sheet].find(f'.//x:c[@r="{ref}"]/x:f',NS)
            if not value and original is not None:continue
            numeric=f['kind'] in ('number','datetime-local') and bool(value)
            if f['kind']=='datetime-local' and value:value=excel_date(value)
            set_xcell(sheets[sheet],ref,value,numeric)
        set_xcell(sheets['RunBook'],'D1',c.get('description',''))
        set_xcell(sheets['RunBook'],'D2',clean_number(row['number']))
        # Remove an old stray sample cell outside the actual-record table.
        set_xcell(sheets['Actual Start-End'],'J13','')
    else:
        t=trees['xl/worksheets/sheet1.xml']
        questions={5:'Users affected. Please provide details:',6:'Services affected. Please provide details:',7:'Does downtime exceed the allowed scheduled window?',8:'Is LD imposed if SLA is missed? Provide details:',11:'Configuration items affected. Please provide details:',12:'Are changed CIs urgent / high criticality?',13:'Can the change be reverted using a fallback plan?',14:'Can rollback finish within the scheduled downtime window?'}
        for r,label in questions.items():
            set_xcell(t,f'B{r}',label+'\n'+v.get(f'details_{r}',''))
            for col,level in zip('CDEFG',['Critical','High','Medium','Low','NA']):set_xcell(t,f'{col}{r}','P' if v.get(f'rating_{r}')==level else '')
        labels={17:'Capacity of Production & Capacity Plan',18:'Disaster Recovery Resources & Plan',19:'Service Availability & Plan',20:'Security Management',21:'Suppliers & Supplier Contracts'}
        for r,label in labels.items():set_xcell(t,f'B{r}',label+'\n\n'+v.get(f'resource_{r}',''))
        set_xcell(t,'C9',v.get('business',''));set_xcell(t,'C15',v.get('technical',''))
        levels=['Critical','High','Medium','Low'];business=v.get('business');technical=v.get('technical')
        matrix=[['Extensive / Widespread']*3+['Significant / Large'],['Extensive / Widespread','Significant / Large','Significant / Large','Moderate / Limited'],['Significant / Large','Moderate / Limited','Moderate / Limited','Minor / Localized'],['Moderate / Limited','Moderate / Limited','Minor / Localized','Minor / Localized']]
        set_xcell(t,'C22',matrix[levels.index(technical)][levels.index(business)] if business in levels and technical in levels else '')
        set_xcell(t,'C23',v.get('completed_by',''));date=v.get('completed_date','');set_xcell(t,'C24',excel_date(date) if date else '',bool(date))
    # Repair invalid template print-title definitions only; preserve all other workbook assets.
    for parent in workbook.iter():
        for el in list(parent):
            if el.tag=='{'+X+'}definedName' and el.text=='#N/A':parent.remove(el)
    calc=workbook.find('x:calcPr',NS)
    if calc is None:calc=ET.SubElement(workbook,'{'+X+'}calcPr')
    calc.set('fullCalcOnLoad','1');calc.set('forceFullCalc','1')
    replacements={n:ET.tostring(t,encoding='utf-8',xml_declaration=True) for n,t in trees.items()}
    replacements['xl/workbook.xml']=ET.tostring(workbook,encoding='utf-8',xml_declaration=True)
    return transform_zip(path,replacements)
