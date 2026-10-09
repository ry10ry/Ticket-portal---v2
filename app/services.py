"""Internal service requests with a single TL-or-Manager approval stage."""
import base64,binascii
from datetime import datetime,date
from zoneinfo import ZoneInfo
from typing import Literal
from urllib.parse import quote
from fastapi import Depends,HTTPException
from fastapi.responses import Response
from pydantic import BaseModel,Field
from sqlalchemy import Column,Integer,String,Text,DateTime,ForeignKey,LargeBinary,Boolean,select,update,delete
from sqlalchemy.dialects.mysql import LONGTEXT,LONGBLOB
from sqlalchemy.orm import Session
from app.main import Base,User,db,current_user,staff,write_guard,now

class SRSequence(Base):
    __tablename__='sr_sequences'
    day=Column(String(8),primary_key=True)
    value=Column(Integer,nullable=False,default=0)
class ServiceRequest(Base):
    __tablename__='service_requests'
    id=Column(Integer,primary_key=True)
    number=Column(String(50),unique=True,nullable=False)
    owner_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    subject=Column(String(200),nullable=False,default='')
    description=Column(Text().with_variant(LONGTEXT(),'mysql'),nullable=False,default='')
    status=Column(String(30),nullable=False,default='In-progress')
    version=Column(Integer,nullable=False,default=1)
    first_submitted_at=Column(DateTime)
    created_at=Column(DateTime,nullable=False,default=now)
    updated_at=Column(DateTime,nullable=False,default=now)
class SRFile(Base):
    __tablename__='sr_files'
    id=Column(Integer,primary_key=True)
    sr_id=Column(Integer,ForeignKey('service_requests.id'),nullable=False)
    filename=Column(String(255),nullable=False)
    mime=Column(String(100),nullable=False)
    data=Column(LargeBinary().with_variant(LONGBLOB(),'mysql'),nullable=False)
    active=Column(Boolean,nullable=False,default=True)
class SRHistory(Base):
    __tablename__='sr_history'
    id=Column(Integer,primary_key=True)
    sr_id=Column(Integer,ForeignKey('service_requests.id'),nullable=False)
    actor_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    action=Column(String(30),nullable=False)
    comment=Column(Text,nullable=False,default='')
    snapshot=Column(Text().with_variant(LONGTEXT(),'mysql'),nullable=False)
    created_at=Column(DateTime,nullable=False,default=now)
class SRNotification(Base):
    __tablename__='sr_notifications'
    id=Column(Integer,primary_key=True)
    sr_id=Column(Integer,ForeignKey('service_requests.id'),nullable=False)
    user_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    text=Column(String(500),nullable=False)
    created_at=Column(DateTime,nullable=False,default=now)
    read_at=Column(DateTime)
class Create(BaseModel):
    sr_date: date

class Upload(BaseModel):
    filename:str=Field(min_length=1,max_length=255)
    data:str=Field(max_length=14000000)
class Save(BaseModel):
    version:int=Field(ge=1)
    subject:str=Field(default='',max_length=200)
    description:str=Field(default='',max_length=20000)
    uploads:list[Upload]=Field(default_factory=list,max_length=5)
    remove_file_ids:list[int]=Field(default_factory=list,max_length=5)
class Delete(BaseModel):
    version:int=Field(ge=1)
class Action(BaseModel):
    version:int=Field(ge=1)
    action:Literal['submit','approve','return','comment']
    comment:str=Field(default='',max_length=20000)
def get_sr(s,id):
    r=s.get(ServiceRequest,id)
    if not r:raise HTTPException(404,'Service request not found')
    return r
def files(s,id):
    return [dict(id=f.id,filename=f.filename,size=len(f.data),preview=f.mime.startswith('image/')) for f in s.scalars(select(SRFile).where(SRFile.sr_id==id,SRFile.active==True).order_by(SRFile.id))]
def serialize(s,r,detail=False):
    data={c.name:getattr(r,c.name) for c in ServiceRequest.__table__.columns};data['owner']=s.get(User,r.owner_id).name
    if detail:
        data['files']=files(s,r.id)
        data['history']=[dict(id=h.id,action=h.action,comment=h.comment,actor=s.get(User,h.actor_id).name,created_at=h.created_at,snapshot=__import__('json').loads(h.snapshot)) for h in s.scalars(select(SRHistory).where(SRHistory.sr_id==r.id).order_by(SRHistory.id))]
    return data
def audit(s,r,u,action,comment=''):
    import json
    s.add(SRHistory(sr_id=r.id,actor_id=u.id,action=action,comment=comment,snapshot=json.dumps(dict(subject=r.subject,description=r.description,status=r.status,version=r.version,files=files(s,r.id)))))
def mutate(s,r,version,**values):
    result=s.execute(update(ServiceRequest).where(ServiceRequest.id==r.id,ServiceRequest.version==version).values(**values,version=version+1,updated_at=now()))
    if result.rowcount!=1:s.rollback();raise HTTPException(409,'SR changed. Reload before saving or approving.')
    s.refresh(r)
def notify(s,r,ids,text):
    for id in set(ids):s.add(SRNotification(sr_id=r.id,user_id=id,text=r.number+' — '+text))
def approvers(s):return [u.id for u in s.scalars(select(User).where(User.role.in_(['infra_tl','infra_manager','admin'])))]
def image_type(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n'):return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):return 'image/jpeg'
    if data[:6] in (b'GIF87a',b'GIF89a'):return 'image/gif'
    if data[:4]==b'RIFF' and data[8:12]==b'WEBP':return 'image/webp'
    return 'application/octet-stream'
def register_services(app):
    @app.get('/api/services/notifications')
    def notifications(u:User=Depends(current_user),s:Session=Depends(db)):
        staff(u)
        return [dict(id=n.id,sr_id=n.sr_id,text=n.text,read=bool(n.read_at),created_at=n.created_at) for n in s.scalars(select(SRNotification).where(SRNotification.user_id==u.id).order_by(SRNotification.id.desc()).limit(100))]
    @app.post('/api/services/notifications/read',dependencies=[Depends(write_guard)])
    def read_notifications(u:User=Depends(current_user),s:Session=Depends(db)):
        staff(u)
        s.execute(update(SRNotification).where(SRNotification.user_id==u.id,SRNotification.read_at==None).values(read_at=now()));s.commit();return {'ok':True}
    @app.get('/api/services')
    def listing(u:User=Depends(current_user),s:Session=Depends(db)):
        staff(u);rows=s.scalars(select(ServiceRequest).order_by(ServiceRequest.id.desc())).all()
        return dict(items=[serialize(s,r) for r in rows],counts={'Submitted SR':sum(r.first_submitted_at is not None for r in rows),'Pending Approval':sum(r.status=='Pending Approval' for r in rows),'Total SR':len(rows)})
    @app.post('/api/services',dependencies=[Depends(write_guard)])
    def create(body:Create,u:User=Depends(current_user),s:Session=Depends(db)):
        staff(u);day=body.sr_date.strftime('%Y%m%d')
        if s.bind.dialect.name=='mysql':
            from sqlalchemy.dialects.mysql import insert
            stmt=insert(SRSequence).values(day=day,value=0);s.execute(stmt.on_duplicate_key_update(day=stmt.inserted.day))
        else:
            from sqlalchemy.dialects.sqlite import insert
            s.execute(insert(SRSequence).values(day=day,value=0).on_conflict_do_nothing())
        s.execute(update(SRSequence).where(SRSequence.day==day).values(value=SRSequence.value+1));seq=s.scalar(select(SRSequence.value).where(SRSequence.day==day))
        r=ServiceRequest(number=f'SR#MOMCC-{day}-{seq:02d}',owner_id=u.id);s.add(r);s.flush();audit(s,r,u,'created');s.commit();return serialize(s,r,True)
    @app.get('/api/services/{id}')
    def detail(id:int,u:User=Depends(current_user),s:Session=Depends(db)):
        staff(u);return serialize(s,get_sr(s,id),True)
    @app.get('/api/services/{id}/files/{file_id}')
    def download(id:int,file_id:int,preview:bool=False,u:User=Depends(current_user),s:Session=Depends(db)):
        staff(u);get_sr(s,id);f=s.get(SRFile,file_id)
        if not f or f.sr_id!=id:raise HTTPException(404,'Attachment not found')
        inline=preview and f.mime.startswith('image/')
        return Response(f.data,media_type=f.mime if inline else 'application/octet-stream',headers={'Content-Disposition':('inline' if inline else 'attachment')+"; filename*=UTF-8''"+quote(f.filename,safe=''),'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'})
    @app.post('/api/services/{id}/delete',dependencies=[Depends(write_guard)])
    def remove(id:int,b:Delete,u:User=Depends(current_user),s:Session=Depends(db)):
        if u.role!='admin':raise HTTPException(403,'Administrator access required')
        r=get_sr(s,id)
        mutate(s,r,b.version)
        for model in [SRNotification,SRHistory,SRFile]:s.execute(delete(model).where(model.sr_id==id))
        s.delete(r);s.commit();return {'ok':True}
    @app.post('/api/services/{id}/save',dependencies=[Depends(write_guard)])
    def save(id:int,b:Save,u:User=Depends(current_user),s:Session=Depends(db)):
        staff(u);r=get_sr(s,id)
        if u.id!=r.owner_id and u.role!='admin':raise HTTPException(403,'Only the submitting Infra or Admin can edit')
        if r.status!='In-progress':raise HTTPException(409,'Return the SR before editing')
        active=s.scalars(select(SRFile).where(SRFile.sr_id==id,SRFile.active==True)).all()
        if set(b.remove_file_ids)-{f.id for f in active}:raise HTTPException(422,'Invalid attachment removal')
        decoded=[]
        for f in b.uploads:
            try:data=base64.b64decode(f.data,validate=True)
            except (ValueError,binascii.Error):raise HTTPException(422,'Invalid attachment')
            if not 0<len(data)<=10*1024*1024:raise HTTPException(422,'Maximum 10 MB per attachment')
            name=f.filename.replace('\\','/').split('/')[-1]
            if not name or any(ord(c)<32 or ord(c)==127 for c in name):raise HTTPException(422,'Invalid filename')
            decoded.append((name,data))
        remaining=[f for f in active if f.id not in b.remove_file_ids]
        if len(remaining)+len(decoded)>5 or sum(len(f.data) for f in remaining)+sum(len(d) for _,d in decoded)>20*1024*1024:raise HTTPException(422,'Maximum 5 attachments and 20 MB total')
        mutate(s,r,b.version,subject=b.subject.strip(),description=b.description.strip())
        for f in active:
            if f.id in b.remove_file_ids:f.active=False
        for name,data in decoded:s.add(SRFile(sr_id=id,filename=name,data=data,mime=image_type(data)))
        s.flush();audit(s,r,u,'saved');s.commit();return serialize(s,r,True)
    @app.post('/api/services/{id}/actions',dependencies=[Depends(write_guard)])
    def action(id:int,b:Action,u:User=Depends(current_user),s:Session=Depends(db)):
        staff(u);r=get_sr(s,id)
        if r.version!=b.version:raise HTTPException(409,'SR changed. Reload before acting.')
        values={};recipients=[]
        if b.action=='submit':
            if u.id!=r.owner_id and u.role!='admin':raise HTTPException(403,'Only the submitting Infra or Admin can submit')
            if r.status!='In-progress':raise HTTPException(409,'SR is not In-progress')
            if not r.subject.strip() or not r.description.strip():raise HTTPException(422,'Subject and Description are required')
            values=dict(status='Pending Approval',first_submitted_at=r.first_submitted_at or now());recipients=approvers(s)
        elif b.action in ('approve','return'):
            if u.role not in ('infra_tl','infra_manager','admin'):raise HTTPException(403,'TL or Manager approval role required')
            if u.id==r.owner_id and u.role!='admin':raise HTTPException(403,'You cannot approve or return your own SR')
            if r.status!='Pending Approval':raise HTTPException(409,'SR is not pending approval')
            if b.action=='return' and not b.comment.strip():raise HTTPException(422,'Return reason is required')
            values={'status':'Approved' if b.action=='approve' else 'In-progress'};recipients=[r.owner_id]+approvers(s)
        elif not b.comment.strip():raise HTTPException(422,'Comment is required')
        mutate(s,r,b.version,**values);audit(s,r,u,b.action,b.comment.strip())
        if recipients:notify(s,r,recipients,f'{b.action.capitalize()}: {r.subject}'+(' — '+b.comment.strip()[:200] if b.comment else ''))
        s.commit();return serialize(s,r,True)
