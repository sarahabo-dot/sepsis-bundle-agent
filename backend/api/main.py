"""
api/main.py
Authenticated, persistent FastAPI layer for the Sepsis Bundle Agent.

Governance boundary unchanged from the prototype: /sofa/calculate,
/bundle/* are pure deterministic computation against the database.
/interpretation is the ONLY endpoint that calls the Claude API, and it
receives already-computed values -- it never recalculates a score.

Run:
    pip install -r requirements.txt
    python seed_user.py            # creates a first login (dev only)
    uvicorn api.main:app --reload --port 8000
"""

import os
import sys
import uuid
from datetime import datetime, timedelta
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.orm import Session
import httpx

from database import (
    init_db, get_db, User, PatientSession, ClinicalValueRecord,
    BundleConfirmation, AuditEventRecord,
)
from auth import authenticate_user, create_access_token, get_current_user, ACCESS_TOKEN_EXPIRE_MINUTES

from models import SofaInput, ClinicalValue, PressorState, PressorDrug, DataStatus
from sofa_calculator import calculate_sofa, delta_sofa, meets_sepsis3_criteria
from bundle_tracker import build_bundle_state, elapsed_minutes, overdue_items, bundle_summary
from data_validation import evaluate_clinical_value, requires_dual_confirmation

app = FastAPI(title="Sepsis Bundle Agent API", version="1.0.0")

# Restrict this list to the actual frontend origin(s) in real deployment --
# "*" is for local development only.
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()


def record_audit(db: Session, actor: str, action: str, detail: dict):
    event = AuditEventRecord(
        event_id=str(uuid.uuid4()), timestamp=datetime.utcnow(),
        actor=actor, action=action, detail=detail,
    )
    db.add(event)
    db.commit()


def get_or_create_session(db: Session, patient_id: str) -> PatientSession:
    session = db.query(PatientSession).filter(PatientSession.patient_id == patient_id).order_by(
        PatientSession.created_at.desc()).first()
    if session is None:
        session = PatientSession(patient_id=patient_id)
        db.add(session)
        db.commit()
        db.refresh(session)
    return session


def last_confirmed_value(db: Session, session_id: int, domain: str) -> Optional[float]:
    rec = db.query(ClinicalValueRecord).filter(
        ClinicalValueRecord.session_id == session_id,
        ClinicalValueRecord.domain == domain,
        ClinicalValueRecord.status == DataStatus.CONFIRMED.value,
    ).order_by(ClinicalValueRecord.timestamp.desc()).first()
    return rec.value if rec else None


# ---------- Auth ----------

@app.post("/auth/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    token = create_access_token(
        {"sub": user.username, "role": user.role},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return {"access_token": token, "token_type": "bearer", "role": user.role, "full_name": user.full_name}


# ---------- Schemas ----------

class SofaRequest(BaseModel):
    patient_id: str
    pao2_fio2: Optional[float] = None
    platelets: Optional[float] = None
    bilirubin: Optional[float] = None
    map_mmhg: Optional[float] = None
    pressor_drug: str = "none"
    pressor_dose: Optional[float] = None
    gcs: Optional[float] = None
    creatinine: Optional[float] = None
    urine_output_24h: Optional[float] = None


class BundleStartRequest(BaseModel):
    patient_id: str


class BundleConfirmRequest(BaseModel):
    patient_id: str
    item_key: str
    confirmed_by: Optional[str] = None  # a second, distinct clinician for high-risk items


class InterpretationRequest(BaseModel):
    patient_id: str
    suspected_source: Optional[str] = None


# ---------- SOFA (deterministic, authenticated) ----------

@app.post("/sofa/calculate")
def sofa_calculate(req: SofaRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    session = get_or_create_session(db, req.patient_id)
    now = datetime.utcnow()
    raw = {
        "pao2_fio2": req.pao2_fio2, "platelets": req.platelets, "bilirubin": req.bilirubin,
        "map_mmhg": req.map_mmhg, "gcs": req.gcs, "creatinine": req.creatinine,
        "urine_output_24h": req.urine_output_24h,
    }

    confirmed = {}
    draft_flags = {}
    for domain, value in raw.items():
        if value is None:
            confirmed[domain] = None
            continue
        cv = ClinicalValue(value=value, unit="", source=user.username, timestamp=now)
        prev = last_confirmed_value(db, session.id, domain)
        checked = evaluate_clinical_value(domain, cv, previous_value=prev, now=now)

        db.add(ClinicalValueRecord(
            session_id=session.id, domain=domain, value=checked.value, unit=checked.unit,
            source=checked.source, status=checked.status.value, flag_reason=checked.flag_reason,
            timestamp=checked.timestamp,
        ))
        db.commit()

        if checked.status == DataStatus.CONFIRMED:
            confirmed[domain] = checked.value
        else:
            confirmed[domain] = None
            draft_flags[domain] = checked.flag_reason

    sofa_input = SofaInput(
        pao2_fio2=ClinicalValue(confirmed["pao2_fio2"], "", "", now) if confirmed["pao2_fio2"] is not None else None,
        platelets=ClinicalValue(confirmed["platelets"], "", "", now) if confirmed["platelets"] is not None else None,
        bilirubin=ClinicalValue(confirmed["bilirubin"], "", "", now) if confirmed["bilirubin"] is not None else None,
        map_mmhg=ClinicalValue(confirmed["map_mmhg"], "", "", now) if confirmed["map_mmhg"] is not None else None,
        pressor=PressorState(drug=PressorDrug(req.pressor_drug), dose_mcg_kg_min=req.pressor_dose),
        gcs=ClinicalValue(confirmed["gcs"], "", "", now) if confirmed["gcs"] is not None else None,
        creatinine=ClinicalValue(confirmed["creatinine"], "", "", now) if confirmed["creatinine"] is not None else None,
        urine_output_24h=ClinicalValue(confirmed["urine_output_24h"], "", "", now) if confirmed["urine_output_24h"] is not None else None,
    )
    result = calculate_sofa(sofa_input)

    if session.baseline_sofa is None:
        session.baseline_sofa = result.total
        db.commit()

    delta = delta_sofa(result, session.baseline_sofa)
    sepsis3 = meets_sepsis3_criteria(result, session.baseline_sofa)

    record_audit(db, user.username, "sofa_calculated", {
        "patient_id": req.patient_id, "total": result.total, "draft_flags": draft_flags,
    })

    return {
        "total": result.total, "components": result.components,
        "completeness": result.completeness, "missing_domains": result.missing_domains,
        "baseline": session.baseline_sofa, "delta_from_baseline": delta,
        "meets_sepsis3_criteria": sepsis3, "draft_flags": draft_flags,
    }


# ---------- Bundle (deterministic, authenticated) ----------

@app.post("/bundle/start")
def bundle_start(req: BundleStartRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    session = get_or_create_session(db, req.patient_id)
    session.recognition_time = datetime.utcnow()
    db.commit()
    record_audit(db, user.username, "bundle_started", {"patient_id": req.patient_id})
    return {"recognition_time": session.recognition_time.isoformat()}


@app.get("/bundle/status")
def bundle_status(patient_id: str, map_mmhg: Optional[float] = None, lactate_initial: Optional[float] = None,
                   db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    session = get_or_create_session(db, patient_id)
    confs = db.query(BundleConfirmation).filter(BundleConfirmation.session_id == session.id).all()
    confirmations = {
        c.item_key: {"done": c.done, "confirmed_at": c.confirmed_at,
                      "acknowledged_by": c.acknowledged_by, "confirmed_by": c.confirmed_by}
        for c in confs
    }
    states = build_bundle_state(session.recognition_time, map_mmhg, lactate_initial, confirmations)
    overdue = overdue_items(states, session.recognition_time)
    return {
        "recognition_time": session.recognition_time.isoformat() if session.recognition_time else None,
        "elapsed_minutes": elapsed_minutes(session.recognition_time),
        "items": [s.__dict__ for s in states],
        "overdue": [s.key for s in overdue],
        "summary": bundle_summary(states),
    }


@app.post("/bundle/confirm")
def bundle_confirm(req: BundleConfirmRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    session = get_or_create_session(db, req.patient_id)
    dual_required = requires_dual_confirmation(req.item_key)

    if dual_required:
        if not req.confirmed_by:
            raise HTTPException(
                status_code=400,
                detail=f"'{req.item_key}' is a high-risk action and requires a second, "
                       f"distinct clinician (confirmed_by) in addition to the acknowledging user.",
            )
        if req.confirmed_by == user.username:
            raise HTTPException(
                status_code=400,
                detail="confirmed_by must be a different clinician than the one acknowledging the action.",
            )

    confirmation = BundleConfirmation(
        session_id=session.id, item_key=req.item_key, done=True,
        confirmed_at=datetime.utcnow(), acknowledged_by=user.username, confirmed_by=req.confirmed_by,
    )
    db.add(confirmation)
    db.commit()

    record_audit(db, user.username, "bundle_item_confirmed", {
        "patient_id": req.patient_id, "item_key": req.item_key,
        "acknowledged_by": user.username, "confirmed_by": req.confirmed_by,
        "dual_confirmation_required": dual_required,
    })
    return {"status": "confirmed", "item_key": req.item_key}


# ---------- Interpretation (the ONLY LLM call in this service) ----------

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

SYSTEM_INSTRUCTION = (
    "You are the Sepsis Bundle Specialist agent inside a multi-agent ICU clinical "
    "decision support system. You receive already-computed, deterministic values "
    "(SOFA components, bundle timer status). Do NOT recalculate scores or invent "
    "numbers. Interpret only: explain which organ system is driving the SOFA score, "
    "flag any overdue bundle elements, and note a plausible source of sepsis given "
    "the organ pattern and suspected source if provided. End by reiterating that all "
    "actions require physician confirmation. Keep it under 150 words, no markdown headers."
)


@app.post("/interpretation")
async def get_interpretation(req: InterpretationRequest, db: Session = Depends(get_db),
                              user: User = Depends(get_current_user)):
    session = get_or_create_session(db, req.patient_id)
    if not CLAUDE_API_KEY:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured.")

    confs = db.query(BundleConfirmation).filter(BundleConfirmation.session_id == session.id).all()
    confirmations = {c.item_key: {"done": c.done} for c in confs}
    states = build_bundle_state(session.recognition_time, None, None, confirmations)

    payload_summary = {
        "patient_id": req.patient_id,
        "suspected_source": req.suspected_source or "not specified",
        "baseline_sofa": session.baseline_sofa,
        "bundle_items": [{"item": s.label, "done": s.done, "required": s.required} for s in states],
        "elapsed_minutes": elapsed_minutes(session.recognition_time),
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            CLAUDE_API_URL,
            headers={"x-api-key": CLAUDE_API_KEY, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
            json={
                "model": "claude-sonnet-4-6", "max_tokens": 500, "system": SYSTEM_INSTRUCTION,
                "messages": [{"role": "user", "content": "Data:\n" + str(payload_summary)}],
            },
        )
    data = resp.json()
    text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    narration = "\n".join(text_blocks) if text_blocks else "No interpretation returned."

    record_audit(db, user.username, "interpretation_requested", {"patient_id": req.patient_id})
    return {"interpretation": narration}


# ---------- Audit ----------

@app.get("/audit/events")
def audit_events(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    events = db.query(AuditEventRecord).order_by(AuditEventRecord.timestamp.desc()).limit(500).all()
    confirmations = db.query(BundleConfirmation).all()
    high_risk = [c for c in confirmations if requires_dual_confirmation(c.item_key)]
    fully_attributed = [c for c in high_risk if c.acknowledged_by and c.confirmed_by]
    completeness = (len(fully_attributed) / len(high_risk)) if high_risk else 1.0
    return {
        "events": [
            {"event_id": e.event_id, "timestamp": e.timestamp.isoformat(), "actor": e.actor,
             "action": e.action, "detail": e.detail}
            for e in events
        ],
        "high_risk_attribution_completeness": completeness,
    }


@app.get("/health")
def health():
    return {"status": "ok"}
