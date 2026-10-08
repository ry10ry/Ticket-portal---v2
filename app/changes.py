"""Internal CR tracking: daily numbering, versioned edits and approval audit trail."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Literal
from fastapi import Depends, HTTPException
from fastapi.responses import Response
from urllib.parse import quote
from app.cr_documents import schema, names, validate_forms, export_doc, export_xlsx
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, select, update
from sqlalchemy.orm import Session
from sqlalchemy.dialects.mysql import LONGTEXT
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


class CRAction(BaseModel):
    version: int = Field(ge=1)
    action: Literal['submit', 'support', 'approve', 'return']
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
    result['owner'] = session.get(User, row.owner_id).name
    if detail:
        result['history'] = [dict(id=h.id, action=h.action, reason=h.reason,
            actor=session.get(User, h.actor_id).name, version=h.version,
            created_at=h.created_at, content=json.loads(h.snapshot))
            for h in session.scalars(select(CRHistory).where(CRHistory.cr_id==row.id).order_by(CRHistory.id))]
    return result


def audit(session, row, user, action, reason=''):
    session.add(CRHistory(cr_id=row.id, actor_id=user.id, action=action,
        reason=reason, version=row.version, snapshot=row.content))


def mutate(session, row, version, **values):
    # Compare-and-swap also protects SQLite, where SELECT FOR UPDATE is ignored.
    changed = session.execute(update(ChangeRequest).where(ChangeRequest.id==row.id,
        ChangeRequest.version==version).values(**values, version=version+1, updated_at=now()))
    if changed.rowcount != 1:
        session.rollback()
        raise HTTPException(409, 'CR changed. Reload before saving or reviewing.')
    session.refresh(row)


def register_changes(app):
    @app.get('/api/changes/forms/schema')
    def form_schema(user: User=Depends(current_user)):
        staff(user)
        return schema()

    @app.get('/api/changes/{cr_id}/documents/{kind}')
    def download_document(cr_id: int, kind: str, user: User=Depends(current_user), session: Session=Depends(db)):
        staff(user)
        if kind not in ('change_form','runbook','checklist'):
            raise HTTPException(404, 'Unknown document')
        data=serialize_cr(read_cr(session,cr_id),session,True)
        payload=export_doc(data,data['history']) if kind=='change_form' else export_xlsx(data,kind)
        filename=data['document_names'][kind]
        mime='application/vnd.openxmlformats-officedocument.'+('wordprocessingml.document' if kind=='change_form' else 'spreadsheetml.sheet')
        return Response(payload,media_type=mime,headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(filename,safe=''), 'Cache-Control':'no-store'})

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
        mutate(session, row, body.version, content=body.content.model_dump_json())
        audit(session, row, user, 'saved')
        session.commit()
        return serialize_cr(row, session, True)

    @app.post('/api/changes/{cr_id}/actions', dependencies=[Depends(write_guard)])
    def action_change(cr_id: int, body: CRAction, user: User=Depends(current_user), session: Session=Depends(db)):
        staff(user)
        row = read_cr(session, cr_id)
        if row.version != body.version:
            raise HTTPException(409, 'CR changed. Reload before reviewing.')
        if body.action=='submit':
            if user.id!=row.owner_id and user.role!='admin':
                raise HTTPException(403, 'Only the submitting Infra or Admin can submit')
            if row.status!='In-progress':
                raise HTTPException(409, 'CR is not In-progress')
            content = json.loads(row.content)
            required = ['background','scope','environment','description','deployment_start','deployment_end',
                        'impact']
            missing = [k for k in required if not str(content.get(k) or '').strip()]
            if missing:
                raise HTTPException(422, 'Complete before submitting: '+', '.join(missing))
            if content.get('forms'):
                try: validate_forms(content['forms'], submission=True)
                except ValueError as error: raise HTTPException(422, str(error))
            elif any(not content.get(k,'').strip() for k in ('change_form','runbook','checklist')):
                raise HTTPException(422, 'Complete all three forms before submitting')
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
        session.commit()
        return serialize_cr(row, session, True)
