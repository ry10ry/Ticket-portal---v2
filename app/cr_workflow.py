"""Versioned Admin configuration and per-CR approval stages."""
import json
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from typing import Literal
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.main import Setting, User, current_user, db, write_guard

DEFAULT_STAGES = [
    {'label':'Review & Support', 'role':'infra_tl', 'action':'support'},
    {'label':'Approve', 'role':'infra_manager', 'action':'approve'},
]

class Stage(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    role: Literal['infra','infra_tl','infra_manager','admin']
    action: Literal['support','approve']

class WorkflowSave(BaseModel):
    version: int = Field(ge=0)
    stages: list[Stage] = Field(min_length=1, max_length=8)

    @model_validator(mode='after')
    def validate_stages(self):
        for stage in self.stages:
            stage.label=stage.label.strip()
            if not stage.label: raise ValueError('Stage label is required')
        if self.stages[-1].action!='approve':
            raise ValueError('The final stage must be an approval')
        return self

def configuration(session):
    row=session.get(Setting,'cr_workflow')
    return json.loads(row.value) if row else {'version':0,'stages':DEFAULT_STAGES}

def stages_for(row):
    # Legacy CRs keep the original TL -> Manager workflow.
    return json.loads(row.content).get('_workflow',DEFAULT_STAGES)

def current_stage(row):
    if row.status not in ('Pending Review','Pending Approval'): return None
    content=json.loads(row.content)
    index=content.get('_stage',0 if row.status=='Pending Review' else 1)
    stages=stages_for(row)
    if not isinstance(index,int) or not 0<=index<len(stages):
        raise HTTPException(409,'CR workflow stage is invalid')
    return {'index':index,**stages[index]}

def stage_status(stage):
    return 'Pending Review' if stage['action']=='support' else 'Pending Approval'

def register_workflow(app):
    def admin(user):
        if user.role!='admin': raise HTTPException(403,'Administrator access required')

    @app.get('/api/cr-workflow')
    def get_workflow(user:User=Depends(current_user),session:Session=Depends(db)):
        admin(user)
        return configuration(session)

    @app.post('/api/cr-workflow',dependencies=[Depends(write_guard)])
    def save_workflow(body:WorkflowSave,user:User=Depends(current_user),session:Session=Depends(db)):
        admin(user)
        row=session.get(Setting,'cr_workflow')
        previous=configuration(session)
        if body.version!=previous['version']:
            raise HTTPException(409,'Workflow changed. Reload Admin settings before saving.')
        value={'version':body.version+1,'stages':[stage.model_dump() for stage in body.stages],
               'updated_by':user.id}
        encoded=json.dumps(value)
        if row:
            result=session.execute(update(Setting).where(Setting.key=='cr_workflow',Setting.value==row.value).values(value=encoded))
            if result.rowcount!=1:
                session.rollback()
                raise HTTPException(409,'Workflow changed. Reload Admin settings before saving.')
        else:
            session.add(Setting(key='cr_workflow',value=encoded))
        try:session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(409,'Workflow changed. Reload Admin settings before saving.')
        return value
