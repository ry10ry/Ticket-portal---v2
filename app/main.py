import base64
import binascii
import json
import csv
import hashlib
import hmac
import io
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, select, event
from sqlalchemy.orm import declarative_base, Session

def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

engine = create_engine(os.environ.get('DATABASE_URL', 'mysql+pymysql://servicedesk:change-me@db/servicedesk?charset=utf8mb4'), pool_pre_ping=True, pool_recycle=1800)
if engine.dialect.name == 'sqlite':
    @event.listens_for(engine, 'connect')
    def foreign_keys(conn, _):
        conn.execute('PRAGMA foreign_keys=ON')
Base = declarative_base()

UPLOAD_DIR = Path(os.environ.get('UPLOAD_DIR', '/data/attachments'))
DEFAULT_SETTINGS = {
    'categories': ['Others', 'Amazon Connect', 'Agent desktop', 'Network / connectivity', 'Reset SharePoint/Knowledge Folder Password', 'Cisco voice / telephony'],
    'domains': ['UAT', 'PROD', 'Corporate', 'Others'],
    'severities': ['Medium', 'Low', 'High', 'Critical'],
    'status_labels': {v:v for v in ['New','In Progress','On Hold','Additional Information Requested','Resolved, Pending Confirmation','Closed','Cancelled']},
    'action_labels': {'note':'Add a note','assign':'Accept / assign technician','decline':'Decline / cancel','hold':'Put on hold','information':'Request information','resume':'Resume work','resolve':'Resolve — request verification','close':'Verify & close'},
    'fault_templates': {'Others': ''}
}
class Setting(Base):
    __tablename__ = 'settings'
    key = Column(String(40), primary_key=True)
    value = Column(Text, nullable=False)

class Attachment(Base):
    __tablename__ = 'attachments'
    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey('tickets.id'), nullable=False)
    entry_id = Column(Integer, ForeignKey('entries.id'))
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    filename = Column(String(255), nullable=False)
    storage_name = Column(String(64), nullable=False)
    size = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=now)

class Upload(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    data: str = Field(max_length=14000000)

def settings(session):
    row = session.get(Setting, 'portal')
    config = json.loads(row.value) if row else json.loads(json.dumps(DEFAULT_SETTINGS))
    legacy = config.get('action_labels', DEFAULT_SETTINGS['action_labels'])
    for role, allowed in [('requester', ['note','close']), ('engineer', list(DEFAULT_SETTINGS['action_labels']))]:
        config.setdefault(role+'_actions', {key:{'label':legacy[key], 'enabled':True} for key in allowed})
    for role in ['requester','engineer']:
        config[role+'_actions'].pop('worklog', None)
    config['action_labels'].pop('worklog', None)
    config['status_labels']['Additional Information Requested'] = 'Pending Customer'
    return config

def save_attachments(session, ticket, user, files, entry=None):
    if len(files) > 5:
        raise HTTPException(422, 'Maximum 5 attachments per submission')
    decoded = []
    total = 0
    for f in files:
        try:
            data = base64.b64decode(f.data, validate=True)
        except (ValueError, binascii.Error):
            raise HTTPException(422, 'Invalid attachment data')
        total += len(data)
        if not data or len(data) > 10*1024*1024 or total > 20*1024*1024:
            raise HTTPException(422, 'Maximum 10 MB per file and 20 MB per submission')
        filename = f.filename.replace('\\', '/').split('/')[-1]
        if not filename or any(ord(c)<32 for c in filename):
            raise HTTPException(422, 'Invalid filename')
        decoded.append((filename, data))
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    try:
        for filename, data in decoded:
            name = secrets.token_hex(24)
            path = UPLOAD_DIR/name
            path.write_bytes(data)
            paths.append(path)
            session.add(Attachment(ticket_id=ticket.id, entry_id=entry.id if entry else None, user_id=user.id, filename=filename, storage_name=name, size=len(data)))
        session.commit()
    except Exception:
        session.rollback()
        for path in paths:
            path.unlink(missing_ok=True)
        raise

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    email = Column(String(180), unique=True, nullable=False)
    password = Column(String(200), nullable=False)
    role = Column(String(20), nullable=False)

class LoginSession(Base):
    __tablename__ = 'sessions'
    token = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    expires = Column(DateTime, nullable=False)

class Ticket(Base):
    __tablename__ = 'tickets'
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    category = Column(String(80), nullable=False)
    location = Column(String(100), nullable=False)
    domain = Column(String(100), nullable=False)
    severity = Column(String(20), nullable=False)
    status = Column(String(50), nullable=False, default='New')
    requester_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    assignee_id = Column(Integer, ForeignKey('users.id'))
    created_at = Column(DateTime, nullable=False, default=now)
    updated_at = Column(DateTime, nullable=False, default=now)
    first_response_at = Column(DateTime)
    resolved_at = Column(DateTime)
    closed_at = Column(DateTime)
    resolution = Column(Text)

class Entry(Base):
    __tablename__ = 'entries'
    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey('tickets.id'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'))
    kind = Column(String(20), nullable=False)
    text = Column(Text, nullable=False)
    minutes = Column(Integer, default=0)
    created_at = Column(DateTime, nullable=False, default=now)

class Notification(Base):
    __tablename__ = 'notifications'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    ticket_id = Column(Integer, ForeignKey('tickets.id'), nullable=False)
    text = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=now)
    read_at = Column(DateTime)

def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + ':' + hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()

def check_password(password, stored):
    salt, _ = stored.split(':')
    return hmac.compare_digest(hash_password(password, salt), stored)

def db():
    with Session(engine) as session:
        yield session

def current_user(request: Request, session: Session = Depends(db)):
    token = request.cookies.get('session', '')
    row = session.get(LoginSession, hashlib.sha256(token.encode()).hexdigest())
    if not row or row.expires < now():
        raise HTTPException(401, 'Please sign in')
    return session.get(User, row.user_id)

def write_guard(request: Request):
    if request.headers.get('x-requested-with') != 'ServiceDesk':
        raise HTTPException(403, 'Invalid request origin')

def staff(user):
    if user.role not in ('infra', 'infra_tl', 'infra_manager', 'admin'):
        raise HTTPException(403, 'Infra access required')

def view_ticket(session, user, ticket_id):
    ticket = session.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, 'Ticket not found')
    return ticket

def record(session, ticket, user_id, kind, text, minutes=0):
    session.add(Entry(ticket_id=ticket.id, user_id=user_id, kind=kind, text=text, minutes=minutes))
    ticket.updated_at = now()

def notify(session, ticket, text, recipient_ids):
    for uid in set(recipient_ids):
        session.add(Notification(user_id=uid, ticket_id=ticket.id, text=f'MOMCC-{ticket.id:06d} — '+text))

def auto_close(session):
    tickets = session.scalars(select(Ticket).where(Ticket.status == 'Resolved, Pending Confirmation', Ticket.resolved_at <= now()-timedelta(days=3)).with_for_update()).all()
    for ticket in tickets:
        ticket.status = 'Closed'
        ticket.closed_at = now()
        record(session, ticket, None, 'Status', 'Automatically closed after 3 days without confirmation.')
        notify(session, ticket, 'Ticket automatically closed after 3 days.', [ticket.requester_id])
    session.commit()
    return len(tickets)

@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        if not session.scalar(select(User).limit(1)):
            password = os.environ.get('ADMIN_PASSWORD', '')
            if len(password) < 12:
                raise RuntimeError('Set ADMIN_PASSWORD to at least 12 characters')
            session.add(User(name='ServiceDesk Administrator', email=os.environ.get('ADMIN_EMAIL','admin@example.com'), password=hash_password(password), role='admin'))
            session.commit()
    yield

app = FastAPI(title='MOMCC ServiceDesk', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=Path(__file__).parent/'static'), name='static')

@app.get('/')
def home():
    return FileResponse(Path(__file__).parent/'static'/'index.html')

class Login(BaseModel):
    email: str = Field(max_length=180)
    password: str = Field(max_length=200)

@app.post('/api/login', dependencies=[Depends(write_guard)])
def login(body: Login, response: Response, session: Session = Depends(db)):
    user = session.scalar(select(User).where(User.email == body.email.lower().strip()))
    if not user or not check_password(body.password, user.password):
        raise HTTPException(401, 'Invalid email or password')
    token = secrets.token_urlsafe(32)
    session.add(LoginSession(token=hashlib.sha256(token.encode()).hexdigest(), user_id=user.id, expires=now()+timedelta(hours=8)))
    session.commit()
    response.set_cookie('session', token, httponly=True, samesite='strict', secure=os.environ.get('COOKIE_SECURE','true') == 'true', max_age=28800)
    return {'id':user.id,'name':user.name,'role':user.role}

@app.post('/api/logout', dependencies=[Depends(write_guard)])
def logout(request: Request, response: Response, session: Session=Depends(db)):
    row = session.get(LoginSession, hashlib.sha256(request.cookies.get('session','').encode()).hexdigest())
    if row:
        session.delete(row)
        session.commit()
    response.delete_cookie('session')
    return {'ok':True}

@app.get('/api/me')
def me(user: User=Depends(current_user)):
    return {'id':user.id,'name':user.name,'role':user.role}

@app.get('/api/users')
def users(user: User=Depends(current_user), session: Session=Depends(db)):
    staff(user)
    return [{'id':u.id,'name':u.name,'email':u.email,'role':u.role} for u in session.scalars(select(User))]

class UserCreate(BaseModel):
    name: str = Field(min_length=1,max_length=100)
    email: str = Field(min_length=3,max_length=180)
    password: str = Field(min_length=12,max_length=200)
    role: str

@app.post('/api/users', dependencies=[Depends(write_guard)])
def create_user(body: UserCreate, user: User=Depends(current_user), session: Session=Depends(db)):
    if user.role != 'admin':
        raise HTTPException(403,'Administrator access required')
    if body.role not in ('requester','infra','infra_tl','infra_manager','admin') or '@' not in body.email:
        raise HTTPException(422,'Invalid role or email')
    if session.scalar(select(User).where(User.email == body.email.lower().strip())):
        raise HTTPException(409,'Email already exists')
    row = User(name=body.name,email=body.email.lower().strip(),password=hash_password(body.password),role=body.role)
    session.add(row)
    session.commit()
    return {'id':row.id}

def serialize(ticket, session):
    result = {c.name:getattr(ticket,c.name) for c in Ticket.__table__.columns}
    result['reference'] = f'MOMCC-{ticket.id:06d}'
    result['requester'] = session.get(User,ticket.requester_id).name
    result['assignee'] = session.get(User,ticket.assignee_id).name if ticket.assignee_id else 'Unassigned'
    return result

@app.get('/api/tickets')
def tickets(user: User=Depends(current_user), session: Session=Depends(db)):
    auto_close(session)
    query = select(Ticket).order_by(Ticket.created_at.desc())
    return [serialize(t,session) for t in session.scalars(query)]

class TicketCreate(BaseModel):
    title: str = Field(min_length=3,max_length=200)
    description: str = Field(min_length=5,max_length=20000)
    category: str = Field(min_length=1,max_length=80)
    location: str = Field(default='',max_length=100)
    domain: str = Field(min_length=1,max_length=100)
    severity: str
    attachments: list[Upload] = Field(default_factory=list, max_length=5)

@app.post('/api/tickets', dependencies=[Depends(write_guard)])
def create_ticket(body: TicketCreate, user: User=Depends(current_user), session: Session=Depends(db)):
    config = settings(session)
    if body.severity not in config['severities'] or body.category not in config['categories'] or body.domain not in config['domains']:
        raise HTTPException(422,'Select a valid category, domain and severity')
    ticket = Ticket(**body.model_dump(exclude={'attachments'}), requester_id=user.id)
    session.add(ticket)
    session.flush()
    record(session,ticket,user.id,'Status','Fault reported — New')
    notify(session,ticket,'New fault report: '+ticket.title,[u.id for u in session.scalars(select(User).where(User.role.in_(['infra','infra_tl','infra_manager','admin'])))])
    save_attachments(session,ticket,user,body.attachments)
    return serialize(ticket,session)

@app.get('/api/tickets/{ticket_id}')
def detail(ticket_id:int, user:User=Depends(current_user), session:Session=Depends(db)):
    ticket = view_ticket(session,user,ticket_id)
    result = serialize(ticket,session)
    result['entries'] = [{'id':e.id,'kind':e.kind,'text':e.text,'minutes':e.minutes,'created_at':e.created_at,'author':session.get(User,e.user_id).name if e.user_id else 'System'} for e in session.scalars(select(Entry).where(Entry.ticket_id==ticket_id).order_by(Entry.id))]
    result['attachments'] = [{'id':a.id,'entry_id':a.entry_id,'filename':a.filename,'size':a.size} for a in session.scalars(select(Attachment).where(Attachment.ticket_id==ticket_id).order_by(Attachment.id))]
    return result

class Action(BaseModel):
    action: str
    attachments: list[Upload] = Field(default_factory=list, max_length=5)
    text: str = Field(default='',max_length=20000)
    assignee_id: int | None = None
    severity: str | None = None
    minutes: int = Field(default=0,ge=0,le=1440)

@app.post('/api/tickets/{ticket_id}/actions', dependencies=[Depends(write_guard)])
def action(ticket_id:int, body:Action, user:User=Depends(current_user), session:Session=Depends(db)):
    ticket = view_ticket(session,user,ticket_id)
    session.refresh(ticket,with_for_update=True)
    if user.role == 'requester' and ticket.requester_id != user.id:
        raise HTTPException(403, 'You can only update tickets you reported')
    closed_infra_comment = ticket.status == 'Closed' and user.role == 'infra' and body.action == 'note'
    if ticket.status == 'Closed' and user.role == 'infra' and (body.action != 'note' or body.severity is not None or body.assignee_id is not None or body.minutes):
        raise HTTPException(403, 'Closed tickets allow comments only')
    if ticket.status in ('Closed','Cancelled') and not (closed_infra_comment or (user.role == 'admin' and body.action == 'severity')):
        raise HTTPException(409,'Ticket is already closed or cancelled')
    text = body.text.strip()
    a = body.action
    role_key = 'requester_actions' if user.role == 'requester' else 'engineer_actions'
    if a in ('hold','resume') or (a.startswith('custom_')):
        raise HTTPException(403, 'This action is no longer available')
    if not closed_infra_comment and a != 'severity' and not settings(session)[role_key].get(a, {}).get('enabled', False):
        raise HTTPException(403, 'This action is disabled for your role')
    severity_changed = False
    if body.severity is not None:
        staff(user)
        if body.severity not in settings(session)['severities']:
            raise HTTPException(422, 'Invalid severity')
        if ticket.severity != body.severity:
            old_severity = ticket.severity
            ticket.severity = body.severity
            severity_changed = True
            record(session,ticket,user.id,'Severity',old_severity+' → '+body.severity)
    if a == 'severity':
        staff(user)
        if not severity_changed:
            raise HTTPException(422, 'Select a different severity')
    elif a == 'note' or a.startswith('custom_'):
        if not text and not severity_changed:
            raise HTTPException(422,'A comment is required')
        label = settings(session)[role_key][a]['label']
        if user.role == 'requester' and ticket.status == 'Additional Information Requested':
            ticket.status = 'In Progress'
            record(session,ticket,user.id,'Status','Requester replied — In Progress')
        if text:
            record(session,ticket,user.id,'Note', (label+': ' if a.startswith('custom_') else '')+text)
    elif a == 'close':
        if ticket.requester_id != user.id:
            raise HTTPException(403,'Only the requester can verify and close')
        if ticket.status != 'Resolved, Pending Confirmation' or not text:
            raise HTTPException(422,'Resolve first and provide close comments')
        ticket.status = 'Closed'
        ticket.closed_at = now()
        record(session,ticket,user.id,'Status','Verified and closed: '+text)
    else:
        staff(user)
        if a == 'assign':
            if ticket.status not in ('New','In Progress','On Hold','Additional Information Requested'):
                raise HTTPException(409,'Cannot assign in current status')
            assignee = session.get(User,body.assignee_id or user.id)
            if not assignee or assignee.role not in ('infra','infra_tl','infra_manager','admin'):
                raise HTTPException(422,'Select an Infra technician')
            ticket.assignee_id = assignee.id
            if ticket.first_response_at is None:
                ticket.first_response_at = now()
            ticket.status = 'In Progress'
            if body.severity:
                if body.severity not in settings(session)['severities']:
                    raise HTTPException(422,'Invalid severity')
                ticket.severity = body.severity
            text = 'Assigned to '+assignee.name
        elif a == 'decline':
            if ticket.status != 'New' or not text:
                raise HTTPException(422,'Only new tickets can be declined; reason required')
            ticket.status = 'Cancelled'
        elif a in ('hold','information','resume','resolve'):
            allowed = {'hold':['In Progress','Additional Information Requested'], 'information':['New','In Progress','On Hold','Additional Information Requested'], 'resume':['On Hold','Additional Information Requested'], 'resolve':['In Progress','On Hold','Additional Information Requested']}
            if ticket.status not in allowed[a] or not text:
                raise HTTPException(422,'Invalid status change or missing reason/resolution')
            if a == 'resume' and not ticket.assignee_id:
                raise HTTPException(422,'Assign a technician first')
            ticket.status = {'hold':'On Hold','information':'Additional Information Requested','resume':'In Progress','resolve':'Resolved, Pending Confirmation'}[a]
            if a == 'resolve':
                ticket.resolution = text
                ticket.resolved_at = now()
        else:
            raise HTTPException(422,'Unknown action')
        record(session,ticket,user.id,'Status',ticket.status+': '+text)
    recipients = [ticket.requester_id] + ([ticket.assignee_id] if ticket.assignee_id else [u.id for u in session.scalars(select(User).where(User.role.in_(['infra','infra_tl','infra_manager','admin'])))])
    notify(session,ticket,'Ticket updated: '+ticket.status,[uid for uid in recipients if uid != user.id])
    session.flush()
    entry = session.scalar(select(Entry).where(Entry.ticket_id==ticket_id).order_by(Entry.id.desc()).limit(1))
    save_attachments(session,ticket,user,body.attachments,entry)
    return serialize(ticket,session)

@app.get('/api/notifications')
def notifications(user:User=Depends(current_user),session:Session=Depends(db)):
    return [{'id':n.id,'ticket_id':n.ticket_id,'reference':f'MOMCC-{n.ticket_id:06d}','text':n.text,'read':bool(n.read_at),'created_at':n.created_at} for n in session.scalars(select(Notification).where(Notification.user_id==user.id).order_by(Notification.id.desc()).limit(100))]

@app.post('/api/notifications/read',dependencies=[Depends(write_guard)])
def read_notifications(user:User=Depends(current_user),session:Session=Depends(db)):
    for n in session.scalars(select(Notification).where(Notification.user_id==user.id,Notification.read_at==None)):
        n.read_at=now()
    session.commit()
    return {'ok':True}

@app.get('/api/report')
def report(month:str,user:User=Depends(current_user),session:Session=Depends(db)):
    staff(user)
    try:
        start=datetime.strptime(month,'%Y-%m')
    except ValueError:
        raise HTTPException(422,'Use YYYY-MM')
    end=(start.replace(day=28)+timedelta(days=4)).replace(day=1)
    start -= timedelta(hours=8)
    end -= timedelta(hours=8)
    out=io.StringIO()
    writer=csv.writer(out)
    writer.writerow(['Reference','Title','Category','Domain','Severity','Status','Requester','Assignee','Created SGT (UTC+8)','First response SGT (UTC+8)','Resolved SGT (UTC+8)','Closed SGT (UTC+8)'])
    def cell(v):
        s=str(v) if v is not None else ''
        return "'"+s if s.lstrip().startswith(('=','+','-','@')) else s
    for t in session.scalars(select(Ticket).where(Ticket.created_at>=start,Ticket.created_at<end)):
        row=serialize(t,session)
        writer.writerow([cell(v) for v in [row['reference'],t.title,t.category,t.domain,t.severity,settings(session)['status_labels'].get(t.status,t.status),row['requester'],row['assignee'],*[ (v+timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S') if v else '' for v in (t.created_at,t.first_response_at,t.resolved_at,t.closed_at)]]])
    return Response('\ufeff'+out.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="MOMCC-faults-{month}.csv"'})

@app.get('/api/settings')
def get_settings(user:User=Depends(current_user),session:Session=Depends(db)):
    return settings(session)

@app.post('/api/settings', dependencies=[Depends(write_guard)])
def update_settings(body:dict,user:User=Depends(current_user),session:Session=Depends(db)):
    if user.role != 'admin':
        raise HTTPException(403,'Administrator access required')
    for key, maximum in [('categories',80),('domains',100),('severities',20)]:
        values=body.get(key)
        if not isinstance(values,list) or not 1<=len(values)<=100 or any(not isinstance(v,str) or not v.strip() or len(v)>maximum for v in values) or len(set(values))!=len(values):
            raise HTTPException(422,'Invalid '+key)
    for key in ['status_labels']:
        values=body.get(key)
        if not isinstance(values,dict) or set(values)!=set(DEFAULT_SETTINGS[key]) or any(not isinstance(v,str) or not v.strip() or len(v)>100 for v in values.values()):
            raise HTTPException(422,'Invalid '+key)
    templates=body.get('fault_templates',{})
    if not isinstance(templates,dict) or len(templates)>100 or any(k not in body['categories'] or not isinstance(v,str) or len(v)>20000 for k,v in templates.items()):
        raise HTTPException(422,'Invalid fault templates')
    config={k:body.get(k,{}) for k in DEFAULT_SETTINGS}
    config['action_labels']=DEFAULT_SETTINGS['action_labels']
    existing = settings(session)
    for role, allowed in [('requester', ['note','close']), ('engineer', list(DEFAULT_SETTINGS['action_labels']))]:
        key=role+'_actions'
        values=body.get(key, existing[key])
        if not isinstance(values,dict) or any(k not in allowed and not (isinstance(k,str) and k.startswith('custom_') and len(k)<=60) for k in values) or any(not isinstance(v,dict) or not isinstance(v.get('label'),str) or not v['label'].strip() or len(v['label'])>100 or not isinstance(v.get('enabled'),bool) for v in values.values()):
            raise HTTPException(422,'Invalid '+key)
        if len(values)>100 or len({v['label'].strip().lower() for v in values.values() if v['enabled']})!=sum(v['enabled'] for v in values.values()):
            raise HTTPException(422,'Action names must be unique; maximum 100 actions')
        config[key]=values
    row=session.get(Setting,'portal')
    if not row:
        row=Setting(key='portal');session.add(row)
    row.value=json.dumps(config)
    session.commit()
    return config

@app.get('/api/attachments/{attachment_id}')
def download_attachment(attachment_id:int,user:User=Depends(current_user),session:Session=Depends(db)):
    row=session.get(Attachment,attachment_id)
    if not row:
        raise HTTPException(404,'Attachment not found')
    view_ticket(session,user,row.ticket_id)
    path=UPLOAD_DIR/row.storage_name
    if not path.is_file():
        raise HTTPException(404,'Attachment file is unavailable')
    return FileResponse(path,filename=row.filename,media_type='application/octet-stream',headers={'X-Content-Type-Options':'nosniff'})

class UserEdit(BaseModel):
    name: str = Field(min_length=1,max_length=100)
    email: str = Field(min_length=3,max_length=180)
    role: str
    password: str = Field(default='',max_length=200)

@app.post('/api/users/{user_id}',dependencies=[Depends(write_guard)])
def edit_user(user_id:int,body:UserEdit,user:User=Depends(current_user),session:Session=Depends(db)):
    if user.role != 'admin':
        raise HTTPException(403,'Administrator access required')
    row=session.get(User,user_id)
    if not row:
        raise HTTPException(404,'User not found')
    if body.role not in ('admin','infra','infra_tl','infra_manager','requester') or '@' not in body.email or (body.password and len(body.password)<12):
        raise HTTPException(422,'Invalid role, email or password (minimum 12 characters)')
    if row.id==user.id and body.role!='admin':
        raise HTTPException(422,'You cannot remove your own administrator role')
    if session.scalar(select(User).where(User.email==body.email.lower().strip(),User.id!=user_id)):
        raise HTTPException(409,'Email already exists')
    row.name=body.name;row.email=body.email.lower().strip();row.role=body.role
    if body.password:
        row.password=hash_password(body.password)
    for login_session in session.scalars(select(LoginSession).where(LoginSession.user_id==user_id)):
        if body.password or body.role!=user.role or row.id!=user.id:
            session.delete(login_session)
    session.commit()
    return {'ok':True}

class SeverityEdit(BaseModel):
    severity: str

@app.post('/api/tickets/{ticket_id}/severity',dependencies=[Depends(write_guard)])
def edit_severity(ticket_id:int,body:SeverityEdit,user:User=Depends(current_user),session:Session=Depends(db)):
    staff(user)
    ticket=view_ticket(session,user,ticket_id)
    session.refresh(ticket,with_for_update=True)
    if ticket.status == 'Closed' and user.role == 'infra':
        raise HTTPException(403, 'Closed tickets allow comments only')
    if body.severity not in settings(session)['severities']:
        raise HTTPException(422,'Select a valid severity')
    old=ticket.severity
    if old != body.severity:
        ticket.severity=body.severity
        record(session,ticket,user.id,'Severity',old+' → '+body.severity)
        notify(session,ticket,'Severity updated: '+body.severity,[uid for uid in [ticket.requester_id,ticket.assignee_id] if uid and uid!=user.id])
        session.commit()
    return serialize(ticket,session)

@app.post('/api/tickets/{ticket_id}/delete',dependencies=[Depends(write_guard)])
def delete_ticket(ticket_id:int,user:User=Depends(current_user),session:Session=Depends(db)):
    if user.role != 'admin':
        raise HTTPException(403,'Administrator access required')
    ticket=view_ticket(session,user,ticket_id)
    session.refresh(ticket,with_for_update=True)
    paths=[]
    for row in session.scalars(select(Attachment).where(Attachment.ticket_id==ticket_id)):
        paths.append(UPLOAD_DIR/row.storage_name);session.delete(row)
    session.flush()
    for model in [Notification,Entry]:
        for row in session.scalars(select(model).where(model.ticket_id==ticket_id)):
            session.delete(row)
    session.flush();session.delete(ticket);session.commit()
    for path in paths:
        path.unlink(missing_ok=True)
    return {'ok':True}


# Register internal change management models and routes before application startup.
from app.changes import register_changes
register_changes(app)
