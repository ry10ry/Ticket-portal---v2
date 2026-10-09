"""Internal CR tracking: daily numbering, versioned edits and approval audit trail."""
import json
import base64
import binascii
import io
import zipfile
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Literal
from fastapi import Depends, HTTPException
from fastapi.responses import Response
from urllib.parse import quote
from app.cr_documents import schema, names, validate_forms, export_doc, export_xlsx
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, select, update, delete, LargeBinary
from sqlalchemy.orm import Session
from sqlalchemy.dialects.mysql import LONGTEXT, LONGBLOB
from app.main import Base, User, db, current_user, staff, write_guard, now


class CRSequence(Base):
    __tablename__ = 'cr_sequences'
    day = Column(String(8), primary_key=True)
    value = Column(Integer, nullable=False, default=0)


class ChangeRequest(Base):
    __tablename__ = 'change_requests'
    id = Column(Integer, primary_key=True)
    number = Column(String(50), unique=True, nullable=False)
    owner_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    status = Column(String(30), nullable=False, default='In-progress')
    version = Column(Integer, nullable=False, default=1)
    content = Column(Text().with_variant(LONGTEXT(), 'mysql'), nullable=False, default='{}')
    created_at = Column(DateTime, nullable=False, default=now)
    updated_at = Column(DateTime, nullable=False, default=now)


class CRHistory(Base):
    __tablename__ = 'cr_history'
    id = Column(Integer, primary_key=True)
    cr_id = Column(Integer, ForeignKey('change_requests.id'), nullable=False)
    actor_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    action = Column(String(30), nullable=False)
    reason = Column(Text, nullable=False, default='')
    version = Column(Integer, nullable=False)
    snapshot = Column(Text().with_variant(LONGTEXT(), 'mysql'), nullable=False)
    created_at = Column(DateTime, nullable=False, default=now)


class CRNotification(Base):
    __tablename__='cr_notifications'
    id=Column(Integer,primary_key=True)
    cr_id=Column(Integer,ForeignKey('change_requests.id'),nullable=False)
    user_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    text=Column(String(500),nullable=False)
    created_at=Column(DateTime,nullable=False,default=now)
    read_at=Column(DateTime)


def notify_change(session,row,user,action,reason=''):
    role='infra_tl' if action=='submit' else 'infra_manager' if action=='support' else None
    recipients=[row.owner_id] if action in ('return','approve','comment','close','istd_evidence') else []
    roles=[role,'admin'] if role else ['admin']
    recipients += [u.id for u in session.scalars(select(User).where(User.role.in_(roles)))]
    for id in set(recipients)-{user.id}:
        session.add(CRNotification(cr_id=row.id,user_id=id,text=row.number+' — '+action.capitalize()+(' — '+reason[:200] if reason else '')))


class CRFile(Base):
    __tablename__ = 'cr_files'
    id = Column(Integer, primary_key=True)
    cr_id = Column(Integer, ForeignKey('change_requests.id'), nullable=False)
    kind = Column(String(20), nullable=False)
    filename = Column(String(255), nullable=False)
    data = Column(LargeBinary().with_variant(LONGBLOB(), 'mysql'), nullable=False)
    version = Column(Integer, nullable=False)
    uploaded_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, nullable=False, default=now)


class CRUpload(BaseModel):
    kind: Literal['change_form', 'runbook', 'checklist']
    filename: str = Field(min_length=1, max_length=255)
    data: str = Field(max_length=14000000)


def file_list(session, cr_id):
    return [dict(id=f.id,kind=f.kind,filename=f.filename,size=len(f.data),version=f.version,
        created_at=f.created_at) for f in session.scalars(select(CRFile).where(CRFile.cr_id==cr_id).order_by(CRFile.id))]


def latest_files(session, cr_id):
    return {f['kind']: f for f in file_list(session,cr_id)}


class CRContent(BaseModel):
    background: str = Field(default='', max_length=20000)
    scope: str = Field(default='', max_length=20000)
    environment: str = Field(default='', max_length=100)
    description: str = Field(default='', max_length=1000)
    deployment_start: datetime | None = None
    deployment_end: datetime | None = None
    impact: str = Field(default='', max_length=20000)
    change_form: str = Field(default='', max_length=50000)
    runbook: str = Field(default='', max_length=50000)
    checklist: str = Field(default='', max_length=50000)
    forms: dict[str, dict[str, str]] = Field(default_factory=dict)

    @field_validator('forms')
    @classmethod
    def check_forms(cls, value):
        if len(json.dumps(value)) > 500000:
            raise ValueError('Combined form content is too large')
        validate_forms(value)
        return value


class CRSave(BaseModel):
    version: int = Field(ge=1)
    content: CRContent
    uploads: list[CRUpload] = Field(default_factory=list, max_length=3)


class CREvidence(BaseModel):
    version: int = Field(ge=1)
    filename: str = Field(min_length=1, max_length=255)
    data: str = Field(max_length=14000000)


class CRAction(BaseModel):
    version: int = Field(ge=1)
    action: Literal['submit', 'support', 'approve', 'return', 'comment', 'close']
    reason: str = Field(default='', max_length=20000)


def read_cr(session, cr_id):
    row = session.get(ChangeRequest, cr_id)
    if not row:
        raise HTTPException(404, 'Change request not found')
    return row


def serialize_cr(row, session, detail=False):
    result = {c.name: getattr(row, c.name) for c in ChangeRequest.__table__.columns}
    result['content'] = json.loads(row.content)
    result['document_names'] = names(row.number, result['content'].get('description',''))
    result['files'] = list(latest_files(session,row.id).values())
    result['owner'] = session.get(User, row.owner_id).name
    if detail:
        result['history'] = [dict(id=h.id, action=h.action, reason=h.reason,
            actor=session.get(User, h.actor_id).name, version=h.version,
            created_at=h.created_at, content=json.loads(h.snapshot))
            for h in session.scalars(select(CRHistory).where(CRHistory.cr_id==row.id).order_by(CRHistory.id))]
    return result


def audit(session, row, user, action, reason=''):
    snapshot=json.loads(row.content)
    snapshot['_files']=file_list(session,row.id)
    snapshot['_actor']={'name':user.name,'email':user.email,'role':user.role}
    session.add(CRHistory(cr_id=row.id, actor_id=user.id, action=action,
        reason=reason, version=row.version, snapshot=json.dumps(snapshot,default=str)))


def mutate(session, row, version, **values):
    # Compare-and-swap also protects SQLite, where SELECT FOR UPDATE is ignored.
    changed = session.execute(update(ChangeRequest).where(ChangeRequest.id==row.id,
        ChangeRequest.version==version).values(**values, version=version+1, updated_at=now()))
    if changed.rowcount != 1:
        session.rollback()
        raise HTTPException(409, 'CR changed. Reload before saving or reviewing.')
    session.refresh(row)


def register_changes(app):
    @app.get('/api/changes/{cr_id}/istd-email')
    def istd_email(cr_id:int,user:User=Depends(current_user),session:Session=Depends(db)):
        staff(user)
        from app.cr_email import current_approval_cycle,draft_message
        row=read_cr(session,cr_id)
        history=session.scalars(select(CRHistory).where(CRHistory.cr_id==cr_id).order_by(CRHistory.id)).all()
        submission,support,approval,file_ids=current_approval_cycle(row,history)
        files=[]
        for kind in ('change_form','runbook','checklist'):
            file=session.get(CRFile,file_ids[kind])
            if not file or file.cr_id!=row.id or file.kind!=kind:
                raise HTTPException(409,'Approved document is no longer available')
            files.append(file)
        identities={h.actor_id:session.get(User,h.actor_id) for h in (support,approval)}
        payload,filename=draft_message(row,submission,support,approval,files,identities)
        return Response(payload,media_type='message/rfc822',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(filename,safe=''),'Cache-Control':'no-store'})

    @app.get('/api/changes/forms/schema')
    def form_schema(user: User=Depends(current_user)):
        staff(user)
        return schema()

    @app.get('/api/changes/{cr_id}/documents/{kind}')
    def download_document(cr_id: int, kind: str, user: User=Depends(current_user), session: Session=Depends(db)):
        staff(user)
        if kind not in ('change_form','runbook','checklist'):
            raise HTTPException(404, 'Unknown document')
        row=read_cr(session,cr_id)
        data=serialize_cr(row,session,True)
        current=latest_files(session,cr_id).get(kind)
        if not current: raise HTTPException(404, 'No completed document uploaded yet')
        file=session.get(CRFile,current['id'])
        filename=file.filename
        mime='application/vnd.openxmlformats-officedocument.'+('wordprocessingml.document' if kind=='change_form' else 'spreadsheetml.sheet')
        return Response(file.data,media_type=mime,headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(filename,safe=''), 'Cache-Control':'no-store'})

    @app.get('/api/changes/{cr_id}/templates/{kind}')
    def template_download(cr_id:int,kind:str,user:User=Depends(current_user),session:Session=Depends(db)):
        staff(user)
        if kind not in ('change_form','runbook','checklist'):raise HTTPException(404,'Unknown template')
        data=serialize_cr(read_cr(session,cr_id),session,True)
        payload=export_doc(data,[]) if kind=='change_form' else export_xlsx(data,kind)
        return Response(payload,media_type='application/octet-stream',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(data['document_names'][kind],safe=''),'Cache-Control':'no-store'})

    @app.get('/api/changes/{cr_id}/files/{file_id}')
    def historic_download(cr_id:int,file_id:int,user:User=Depends(current_user),session:Session=Depends(db)):
        staff(user)
        read_cr(session,cr_id)
        file=session.get(CRFile,file_id)
        if not file or file.cr_id!=cr_id:raise HTTPException(404,'File not found')
        return Response(file.data,media_type='application/octet-stream',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(file.filename,safe=''),'Cache-Control':'no-store'})

    @app.post('/api/changes/{cr_id}/delete',dependencies=[Depends(write_guard)])
    def delete_change(cr_id:int,body:CRAction,user:User=Depends(current_user),session:Session=Depends(db)):
        if user.role!='admin':raise HTTPException(403,'Administrator access required')
        row=read_cr(session,cr_id)
        mutate(session,row,body.version)
        session.execute(delete(CRFile).where(CRFile.cr_id==cr_id))
        session.execute(delete(CRNotification).where(CRNotification.cr_id==cr_id))
        session.execute(delete(CRHistory).where(CRHistory.cr_id==cr_id))
        session.delete(row)
        session.commit()
        return {'ok':True}

    @app.get('/api/changes')
    def list_changes(user: User=Depends(current_user), session: Session=Depends(db)):
        staff(user)
        rows = session.scalars(select(ChangeRequest).order_by(ChangeRequest.id.desc())).all()
        counts = {status: sum(r.status==status for r in rows)
            for status in ['In-progress', 'Pending Review', 'Pending Approval']}
        counts['Total CR'] = len(rows)
        return {'counts': counts, 'items': [serialize_cr(r, session) for r in rows]}

    @app.post('/api/changes', dependencies=[Depends(write_guard)])
    def create_change(user: User=Depends(current_user), session: Session=Depends(db)):
        staff(user)
        day = datetime.now(ZoneInfo('Asia/Singapore')).strftime('%Y%m%d')
        # Upsert the date row, then increment under its transaction lock.
        if session.bind.dialect.name == 'mysql':
            from sqlalchemy.dialects.mysql import insert
            statement = insert(CRSequence).values(day=day, value=0)
            session.execute(statement.on_duplicate_key_update(day=statement.inserted.day))
        else:
            from sqlalchemy.dialects.sqlite import insert
            session.execute(insert(CRSequence).values(day=day, value=0).on_conflict_do_nothing())
        session.execute(update(CRSequence).where(CRSequence.day==day).values(value=CRSequence.value+1))
        sequence = session.scalar(select(CRSequence.value).where(CRSequence.day==day))
        row = ChangeRequest(number=f'CR# MOMCC-{day}-{sequence:02d}', owner_id=user.id,
            content=CRContent().model_dump_json())
        session.add(row)
        session.flush()
        audit(session, row, user, 'created')
        session.commit()
        return serialize_cr(row, session, True)

    @app.get('/api/changes/{cr_id}')
    def get_change(cr_id: int, user: User=Depends(current_user), session: Session=Depends(db)):
        staff(user)
        return serialize_cr(read_cr(session, cr_id), session, True)

    @app.post('/api/changes/{cr_id}/istd-evidence', dependencies=[Depends(write_guard)])
    def upload_istd_evidence(cr_id: int, body: CREvidence, user: User=Depends(current_user), session: Session=Depends(db)):
        staff(user)
        row=read_cr(session,cr_id)
        if user.id!=row.owner_id and user.role!='admin':
            raise HTTPException(403,'Only the submitting Infra or Admin can upload ISTD approval evidence')
        if row.status!='Approved':
            raise HTTPException(409,'ISTD approval evidence can only be uploaded after internal approval')
        safe_name=body.filename.replace('\\','/').split('/')[-1]
        safe_name=''.join(ch for ch in safe_name if ord(ch)>=32 and ord(ch)!=127)
        extension=Path(safe_name).suffix.lower()
        if extension not in ('.eml','.msg','.pdf','.docx','.png','.jpg','.jpeg'):
            raise HTTPException(422,'Use an Email (.eml/.msg), PDF, DOCX, PNG or JPEG approval document')
        try:
            data=base64.b64decode(body.data,validate=True)
            if not 0<len(data)<=10*1024*1024:raise ValueError()
            if extension=='.eml':
                from email.parser import BytesParser
                from email import policy
                message=BytesParser(policy=policy.default).parsebytes(data)
                valid=bool(message.get('From') and message.get('Subject') and not message.defects)
            elif extension=='.docx':
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    valid=sum(f.file_size for f in archive.infolist())<=100*1024*1024 and 'word/document.xml' in archive.namelist() and archive.testzip() is None
            else:
                signatures={'.msg':b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1','.pdf':b'%PDF-', '.png':b'\x89PNG\r\n\x1a\n', '.jpg':b'\xff\xd8\xff','.jpeg':b'\xff\xd8\xff'}
                valid=data.startswith(signatures[extension])
            if not valid:raise ValueError()
        except (ValueError,binascii.Error,zipfile.BadZipFile,RuntimeError):
            raise HTTPException(422,'Invalid approval evidence file (maximum 10 MB)')
        mutate(session,row,body.version)
        session.add(CRFile(cr_id=row.id,kind='istd_approval',filename=safe_name,data=data,version=row.version,uploaded_by=user.id))
        session.flush()
        audit(session,row,user,'istd_evidence','Uploaded ISTD approval evidence: '+safe_name)
        notify_change(session,row,user,'istd_evidence')
        session.commit()
        return serialize_cr(row,session,True)

    @app.post('/api/changes/{cr_id}/save', dependencies=[Depends(write_guard)])
    def save_change(cr_id: int, body: CRSave, user: User=Depends(current_user), session: Session=Depends(db)):
        staff(user)
        row = read_cr(session, cr_id)
        if user.id != row.owner_id and user.role != 'admin':
            raise HTTPException(403, 'Only the submitting Infra or Admin can edit this CR')
        if row.status != 'In-progress':
            raise HTTPException(409, 'Return the CR before editing reviewed content')
        start, end = body.content.deployment_start, body.content.deployment_end
        if any(d and d.tzinfo is None for d in (start, end)):
            raise HTTPException(422, 'Deployment times must include a timezone')
        if start and end and end < start:
            raise HTTPException(422, 'Deployment end must not precede start')
        decoded=[]
        seen=set()
        for upload in body.uploads:
            if upload.kind in seen:raise HTTPException(422,'One file per document type')
            seen.add(upload.kind)
            extension='.docx' if upload.kind=='change_form' else '.xlsx'
            if not upload.filename.lower().endswith(extension):raise HTTPException(422,'Expected '+extension)
            try:
                data=base64.b64decode(upload.data,validate=True)
                if not 0<len(data)<=10*1024*1024:raise ValueError()
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    required='word/document.xml' if upload.kind=='change_form' else 'xl/workbook.xml'
                    if sum(f.file_size for f in archive.infolist())>100*1024*1024:raise ValueError()
                    if required not in archive.namelist() or archive.testzip() is not None:raise ValueError()
            except (ValueError,binascii.Error,zipfile.BadZipFile,RuntimeError):
                raise HTTPException(422,'Invalid or oversized Office document (maximum 10 MB)')
            decoded.append((upload,data))
        # Retain earlier content fields; the new portal edits only background and scope.
        merged=json.loads(row.content)
        merged.update(body.content.model_dump(mode='json',exclude_unset=True))
        mutate(session,row,body.version,content=json.dumps(merged))
        for upload,data in decoded:
            safe_name=upload.filename.replace('\\','/').split('/')[-1]
            safe_name=''.join(ch for ch in safe_name if ord(ch)>=32 and ord(ch)!=127)
            session.add(CRFile(cr_id=row.id,kind=upload.kind,filename=safe_name,data=data,
                version=row.version,uploaded_by=user.id))
        session.flush()
        audit(session, row, user, 'saved')
        session.commit()
        return serialize_cr(row, session, True)

    @app.post('/api/changes/{cr_id}/actions', dependencies=[Depends(write_guard)])
    def action_change(cr_id: int, body: CRAction, user: User=Depends(current_user), session: Session=Depends(db)):
        staff(user)
        row = read_cr(session, cr_id)
        if row.version != body.version:
            raise HTTPException(409, 'CR changed. Reload before reviewing.')
        if body.action=='comment':
            if not body.reason.strip():raise HTTPException(422,'Comment is required')
            mutate(session,row,body.version)
            audit(session,row,user,'comment',body.reason.strip())
            notify_change(session,row,user,'comment',body.reason.strip())
            session.commit()
            return serialize_cr(row,session,True)
        if body.action=='close':
            if user.id!=row.owner_id and user.role!='admin':
                raise HTTPException(403,'Only the submitting Infra or Admin can close this CR')
            if row.status!='Approved':
                raise HTTPException(409,'Only an Approved CR can be closed')
            if 'istd_approval' not in latest_files(session,row.id):
                raise HTTPException(422,'Upload ISTD approval Email or supporting document before closing')
            target='Closed'
        elif body.action=='submit':
            if user.id!=row.owner_id and user.role!='admin':
                raise HTTPException(403, 'Only the submitting Infra or Admin can submit')
            if row.status!='In-progress':
                raise HTTPException(409, 'CR is not In-progress')
            content = json.loads(row.content)
            missing=[k for k in ('background','scope') if not content.get(k,'').strip()]
            missing += [k for k in ('change_form','runbook','checklist') if k not in latest_files(session,row.id)]
            if missing:raise HTTPException(422,'Complete before submitting: '+', '.join(missing))
            target = 'Pending Review'
        else:
            required_role = {'Pending Review':'infra_tl', 'Pending Approval':'infra_manager'}.get(row.status)
            if not required_role:
                raise HTTPException(409, 'CR is not awaiting review or approval')
            if user.role not in (required_role, 'admin'):
                raise HTTPException(403, 'Assigned approval role required')
            if user.id==row.owner_id and user.role!='admin':
                raise HTTPException(403, 'You cannot support or approve your own CR')
            if body.action=='return':
                if not body.reason.strip():
                    raise HTTPException(422, 'Return reason is required')
                target = 'In-progress'
            elif body.action=='support' and row.status=='Pending Review':
                target = 'Pending Approval'
            elif body.action=='approve' and row.status=='Pending Approval':
                target = 'Approved'
            else:
                raise HTTPException(409, 'Invalid action for current stage')
        mutate(session, row, body.version, status=target)
        audit(session, row, user, body.action, body.reason.strip())
        notify_change(session,row,user,body.action,body.reason.strip())
        session.commit()
        return serialize_cr(row, session, True)
