"""Shared CR/SR notifications and month-based CSV exports."""
import csv,io,json,re
from datetime import datetime,timedelta
from fastapi import Depends,HTTPException
from fastapi.responses import Response
from sqlalchemy import select,update
from sqlalchemy.orm import Session
from app.main import User,db,current_user,staff,write_guard,now
from app.changes import ChangeRequest,CRHistory,CRNotification,CRFile
from app.services import ServiceRequest,SRHistory,SRNotification,SRFile

def month_bounds(month):
    if not re.fullmatch(r'\d{4}-\d{2}',month):raise HTTPException(422,'Use YYYY-MM')
    try:
        start=datetime.strptime(month,'%Y-%m');end=(start.replace(day=28)+timedelta(days=4)).replace(day=1)
        return start-timedelta(hours=8),end-timedelta(hours=8)
    except (ValueError,OverflowError):raise HTTPException(422,'Use a valid YYYY-MM')
def local(value):return (value+timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S') if value else ''
def safe(value):
    text=str(value) if value is not None else ''
    return "'"+text if text.lstrip().startswith(('=','+','-','@')) else text
def register_internal_views(app):
    @app.get('/api/internal/notifications')
    def notifications(u:User=Depends(current_user),s:Session=Depends(db)):
        staff(u);items=[]
        for model,kind,key in [(CRNotification,'CR','cr_id'),(SRNotification,'SR','sr_id')]:
            for n in s.scalars(select(model).where(model.user_id==u.id).order_by(model.id.desc()).limit(100)):
                items.append(dict(id=f'{kind}-{n.id}',kind=kind,record_id=getattr(n,key),text=n.text,created_at=n.created_at,read=bool(n.read_at)))
        return sorted(items,key=lambda n:(n['created_at'],n['id']),reverse=True)
    @app.post('/api/internal/notifications/read',dependencies=[Depends(write_guard)])
    def read(u:User=Depends(current_user),s:Session=Depends(db)):
        staff(u)
        for model in [CRNotification,SRNotification]:s.execute(update(model).where(model.user_id==u.id,model.read_at==None).values(read_at=now()))
        s.commit();return {'ok':True}
    @app.get('/api/internal/reports/{kind}')
    def report(kind:str,month:str,u:User=Depends(current_user),s:Session=Depends(db)):
        staff(u)
        if kind not in ('cr','sr'):raise HTTPException(404,'Unknown report type')
        start,end=month_bounds(month);output=io.StringIO();writer=csv.writer(output)
        model=ChangeRequest if kind=='cr' else ServiceRequest
        history_model=CRHistory if kind=='cr' else SRHistory
        history_key=history_model.cr_id if kind=='cr' else history_model.sr_id
        headers=['CR Number','Status','Raised By','Background / Reason for Change','Scope of Change','Created SGT','Updated SGT','TL Supported By','TL Supported SGT','Approved By','Approved SGT','Uploaded Documents'] if kind=='cr' else ['SR Number','Status','Raised By','Subject','Description','Created SGT','Updated SGT','First Submitted SGT','Approved By','Approved SGT','Active Attachments']
        writer.writerow(headers)
        for row in s.scalars(select(model).where(model.created_at>=start,model.created_at<end).order_by(model.id)):
            history=s.scalars(select(history_model).where(history_key==row.id).order_by(history_model.id)).all()
            submission=next((i for i in range(len(history)-1,-1,-1) if history[i].action=='submit'),len(history))
            cycle=history[submission:]
            support=next((h for h in cycle if h.action=='support'),None) if kind=='cr' and row.status in ('Pending Approval','Approved') else None
            approval=next((h for h in cycle if h.action=='approve'),None) if row.status=='Approved' else None
            def actor(h):
                if not h:return ''
                snapshot=json.loads(h.snapshot)
                return snapshot.get('_actor',{}).get('name') or s.get(User,h.actor_id).name
            values=[row.number,row.status,s.get(User,row.owner_id).name]
            if kind=='cr':
                content=json.loads(row.content);files=s.execute(select(CRFile.kind,CRFile.filename).where(CRFile.cr_id==row.id).order_by(CRFile.id)).all();latest={f.kind:f.filename for f in files}
                values += [content.get('background',''),content.get('scope',''),local(row.created_at),local(row.updated_at),actor(support),local(support.created_at if support else None),actor(approval),local(approval.created_at if approval else None),'; '.join(latest.values())]
            else:
                files=s.scalars(select(SRFile.filename).where(SRFile.sr_id==row.id,SRFile.active==True)).all()
                values += [row.subject,row.description,local(row.created_at),local(row.updated_at),local(row.first_submitted_at),actor(approval),local(approval.created_at if approval else None),'; '.join(files)]
            writer.writerow([safe(v) for v in values])
        return Response('\ufeff'+output.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="MOMCC-{kind.upper()}-{month}.csv"','Cache-Control':'no-store'})
