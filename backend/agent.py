"""
agent.py
The actual agentic layer, as distinct from the plain narration in
/interpretation. The difference that matters for "is this really an
agent": here Claude is given tools and decides for itself which ones to
call and in what order, based on what it currently knows and what gaps
it identifies -- it is not handed a pre-assembled summary.

Boundary that is NOT crossed: every tool below is read-only. The agent
can look at data and propose actions; it cannot write a SOFA value,
confirm a bundle item, or take any action that changes patient state.
That boundary is enforced here in code, not by asking the model nicely.
"""

import os
import json
from datetime import datetime
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from database import PatientSession, ClinicalValueRecord, BundleConfirmation, AuditEventRecord
from models import SofaInput, ClinicalValue, PressorState, PressorDrug, DataStatus
from sofa_calculator import calculate_sofa, delta_sofa, meets_sepsis3_criteria
from bundle_tracker import build_bundle_state, elapsed_minutes, overdue_items

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AGENT_MODEL = "claude-sonnet-4-6"
MAX_TOOL_ITERATIONS = 6

SOFA_DOMAINS = ["pao2_fio2", "platelets", "bilirubin", "map_mmhg", "gcs", "creatinine", "urine_output_24h"]


# ---------- Tool definitions (Anthropic tool-use schema) ----------

TOOLS = [
    {
        "name": "get_current_sofa",
        "description": (
            "Get the patient's current SOFA score, computed deterministically from "
            "the most recent CONFIRMED value in each organ-system domain. Use this "
            "first to establish the current severity picture."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_domain_trend",
        "description": (
            "Get the recent history of values for a single organ-system domain "
            "(e.g. how creatinine has changed over the last several readings). "
            "Use this when the current SOFA score alone doesn't explain whether "
            "the patient is improving or deteriorating -- a single snapshot can't "
            "show a trend."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "enum": SOFA_DOMAINS,
                    "description": "Which domain's history to retrieve.",
                }
            },
            "required": ["domain"],
        },
    },
    {
        "name": "get_bundle_status",
        "description": (
            "Get the Hour-1 Bundle status: which items are required for this "
            "patient's current hemodynamics, which are done, which are overdue. "
            "Use this to check whether protocol adherence itself might explain a "
            "worsening picture (e.g. antibiotics still not given)."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_draft_flags",
        "description": (
            "Get any clinical values currently sitting in DRAFT or STALE status "
            "for this patient -- values the deterministic validation layer flagged "
            "as physiologically implausible, stale, or an unexplained jump from the "
            "prior reading. Use this if the SOFA picture seems inconsistent with "
            "what you'd clinically expect -- a flagged data quality issue is a more "
            "likely explanation than a genuine organ-system change."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_deterioration_timeline",
        "description": (
            "Get a single chronological timeline combining every clinical value "
            "entered, every Hour-1 Bundle item confirmed, and every system event "
            "logged for this patient, in time order. Use this when you need the "
            "full sequence of what happened and when -- e.g. to check whether "
            "antibiotics were given before or after cultures, or how much time "
            "elapsed between deterioration and the first intervention."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "submit_assessment",
        "description": (
            "Call this exactly once, as your final step, to submit your completed "
            "assessment. Do not call any other tool after this one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "One-paragraph clinical summary of what is driving the current picture.",
                },
                "probable_source": {
                    "type": "string",
                    "description": (
                        "Best-supported probable source of infection given the organ "
                        "pattern and any suspected source provided (e.g. 'pulmonary', "
                        "'urinary', 'abdominal', 'line-associated', 'undetermined'). "
                        "State your confidence briefly."
                    ),
                },
                "priority_actions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "2-4 concrete next actions for the physician, ordered by urgency.",
                },
                "data_quality_concerns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Any DRAFT/STALE flags or overdue bundle items found during "
                        "investigation that should be called out explicitly. Empty "
                        "array if none were found."
                    ),
                },
            },
            "required": ["summary", "probable_source", "priority_actions", "data_quality_concerns"],
        },
    },
]


# ---------- Tool execution (all read-only against the database) ----------

def _get_or_none_session(db: Session, patient_id: str) -> Optional[PatientSession]:
    return db.query(PatientSession).filter(PatientSession.patient_id == patient_id).order_by(
        PatientSession.created_at.desc()).first()


def _last_confirmed(db: Session, session_id: int, domain: str):
    return db.query(ClinicalValueRecord).filter(
        ClinicalValueRecord.session_id == session_id,
        ClinicalValueRecord.domain == domain,
        ClinicalValueRecord.status == DataStatus.CONFIRMED.value,
    ).order_by(ClinicalValueRecord.timestamp.desc()).first()


def tool_get_current_sofa(db: Session, patient_id: str) -> dict:
    session = _get_or_none_session(db, patient_id)
    if session is None:
        return {"error": "No session found for this patient_id yet."}

    confirmed = {}
    for domain in SOFA_DOMAINS:
        rec = _last_confirmed(db, session.id, domain)
        confirmed[domain] = rec.value if rec else None

    # Pressor state lives on the session record (it isn't a per-domain
    # value), so it must be read back explicitly here -- forgetting this
    # silently under-scores the cardiovascular domain for any patient on
    # vasopressors, which is exactly the population this tool matters most
    # for. Caught via live testing of the triage endpoint.
    pressor_drug = PressorDrug(session.last_pressor_drug or "none")
    pressor_dose = session.last_pressor_dose

    sofa_input = SofaInput(
        pao2_fio2=ClinicalValue(confirmed["pao2_fio2"], "", "", datetime.utcnow()) if confirmed["pao2_fio2"] is not None else None,
        platelets=ClinicalValue(confirmed["platelets"], "", "", datetime.utcnow()) if confirmed["platelets"] is not None else None,
        bilirubin=ClinicalValue(confirmed["bilirubin"], "", "", datetime.utcnow()) if confirmed["bilirubin"] is not None else None,
        map_mmhg=ClinicalValue(confirmed["map_mmhg"], "", "", datetime.utcnow()) if confirmed["map_mmhg"] is not None else None,
        pressor=PressorState(drug=pressor_drug, dose_mcg_kg_min=pressor_dose),
        gcs=ClinicalValue(confirmed["gcs"], "", "", datetime.utcnow()) if confirmed["gcs"] is not None else None,
        creatinine=ClinicalValue(confirmed["creatinine"], "", "", datetime.utcnow()) if confirmed["creatinine"] is not None else None,
        urine_output_24h=ClinicalValue(confirmed["urine_output_24h"], "", "", datetime.utcnow()) if confirmed["urine_output_24h"] is not None else None,
    )
    result = calculate_sofa(sofa_input)
    baseline = session.baseline_sofa
    return {
        "total": result.total,
        "components": result.components,
        "completeness": result.completeness,
        "missing_domains": result.missing_domains,
        "baseline": baseline,
        "delta_from_baseline": delta_sofa(result, baseline) if baseline is not None else None,
        "meets_sepsis3_criteria": meets_sepsis3_criteria(result, baseline) if baseline is not None else None,
    }


def tool_get_domain_trend(db: Session, patient_id: str, domain: str) -> dict:
    session = _get_or_none_session(db, patient_id)
    if session is None:
        return {"error": "No session found for this patient_id yet."}
    records = db.query(ClinicalValueRecord).filter(
        ClinicalValueRecord.session_id == session.id,
        ClinicalValueRecord.domain == domain,
    ).order_by(ClinicalValueRecord.timestamp.asc()).limit(20).all()
    return {
        "domain": domain,
        "history": [
            {"value": r.value, "status": r.status, "timestamp": r.timestamp.isoformat(), "flag_reason": r.flag_reason}
            for r in records
        ],
    }


def tool_get_bundle_status(db: Session, patient_id: str) -> dict:
    session = _get_or_none_session(db, patient_id)
    if session is None:
        return {"error": "No session found for this patient_id yet."}
    confs = db.query(BundleConfirmation).filter(BundleConfirmation.session_id == session.id).all()
    confirmations = {c.item_key: {"done": c.done} for c in confs}
    states = build_bundle_state(session.recognition_time, None, None, confirmations)
    overdue = overdue_items(states, session.recognition_time)
    return {
        "recognition_time": session.recognition_time.isoformat() if session.recognition_time else None,
        "elapsed_minutes": elapsed_minutes(session.recognition_time),
        "items": [{"key": s.key, "label": s.label, "required": s.required, "done": s.done} for s in states],
        "overdue": [s.key for s in overdue],
    }


def tool_get_draft_flags(db: Session, patient_id: str) -> dict:
    session = _get_or_none_session(db, patient_id)
    if session is None:
        return {"error": "No session found for this patient_id yet."}
    flagged = db.query(ClinicalValueRecord).filter(
        ClinicalValueRecord.session_id == session.id,
        ClinicalValueRecord.status != DataStatus.CONFIRMED.value,
    ).order_by(ClinicalValueRecord.timestamp.desc()).limit(20).all()
    return {
        "flagged_values": [
            {"domain": r.domain, "value": r.value, "status": r.status,
             "flag_reason": r.flag_reason, "timestamp": r.timestamp.isoformat()}
            for r in flagged
        ]
    }


def tool_get_deterioration_timeline(db: Session, patient_id: str) -> dict:
    """Deterministically assembles a chronological timeline from three
    separate tables. The agent narrates this timeline; it does not
    construct it -- ordering and content come entirely from stored,
    timestamped records."""
    session = _get_or_none_session(db, patient_id)
    if session is None:
        return {"error": "No session found for this patient_id yet."}

    events = []

    for rec in db.query(ClinicalValueRecord).filter(
        ClinicalValueRecord.session_id == session.id
    ).order_by(ClinicalValueRecord.timestamp.asc()).all():
        events.append({
            "timestamp": rec.timestamp.isoformat(),
            "type": "clinical_value",
            "detail": f"{rec.domain} = {rec.value} ({rec.status})"
                      + (f" -- {rec.flag_reason}" if rec.flag_reason else ""),
        })

    for conf in db.query(BundleConfirmation).filter(
        BundleConfirmation.session_id == session.id
    ).order_by(BundleConfirmation.confirmed_at.asc()).all():
        events.append({
            "timestamp": conf.confirmed_at.isoformat() if conf.confirmed_at else None,
            "type": "bundle_confirmation",
            "detail": f"'{conf.item_key}' confirmed by {conf.acknowledged_by}"
                      + (f", co-signed by {conf.confirmed_by}" if conf.confirmed_by else ""),
        })

    # Audit events store patient_id inside a JSON detail blob rather than a
    # foreign key, so filtering happens in Python after a bounded fetch.
    for evt in db.query(AuditEventRecord).order_by(AuditEventRecord.timestamp.desc()).limit(300).all():
        if (evt.detail or {}).get("patient_id") == patient_id:
            events.append({
                "timestamp": evt.timestamp.isoformat(),
                "type": "system_event",
                "detail": f"{evt.action} by {evt.actor}",
            })

    events.sort(key=lambda e: e["timestamp"] or "")
    return {"timeline": events}


TOOL_DISPATCH = {
    "get_current_sofa": lambda db, patient_id, args: tool_get_current_sofa(db, patient_id),
    "get_domain_trend": lambda db, patient_id, args: tool_get_domain_trend(db, patient_id, args["domain"]),
    "get_bundle_status": lambda db, patient_id, args: tool_get_bundle_status(db, patient_id),
    "get_draft_flags": lambda db, patient_id, args: tool_get_draft_flags(db, patient_id),
    "get_deterioration_timeline": lambda db, patient_id, args: tool_get_deterioration_timeline(db, patient_id),
}


AGENT_SYSTEM_PROMPT = (
    "You are the Sepsis Bundle Specialist agent inside a multi-agent ICU clinical "
    "decision support system. You have read-only tools to look up the patient's "
    "current SOFA score, domain-level trends, Hour-1 Bundle status, flagged/DRAFT "
    "data, and a full chronological timeline. Decide for yourself which tools you "
    "need and in what order -- do not assume you need all of them, but investigate "
    "enough to give a well-supported answer. You cannot write any value or confirm "
    "any action; you can only observe and propose.\n\n"
    "When you have enough information, call submit_assessment exactly once as your "
    "final step. Do not write a free-text final answer instead of calling it. If "
    "you found any DRAFT/STALE flags or overdue bundle items during your "
    "investigation, list them in data_quality_concerns -- do not let clean-looking "
    "numbers hide a known data quality problem."
)


async def run_agent_consult(db: Session, patient_id: str, suspected_source: Optional[str] = None) -> dict:
    """Runs the tool-calling loop. Returns the final proposal plus a trace of
    which tools the agent chose to call, in order -- the trace is what makes
    the agentic behavior demonstrable rather than just asserted."""
    if not CLAUDE_API_KEY:
        return {"error": "ANTHROPIC_API_KEY not configured."}

    user_context = f"Patient ID: {patient_id}."
    if suspected_source:
        user_context += f" Suspected source of infection: {suspected_source}."

    messages = [{"role": "user", "content": user_context}]
    trace = []
    force_submission = False

    async with httpx.AsyncClient(timeout=30) as client:
        for _ in range(MAX_TOOL_ITERATIONS):
            resp = await client.post(
                CLAUDE_API_URL,
                headers={
                    "x-api-key": CLAUDE_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": AGENT_MODEL,
                    "max_tokens": 800,
                    "system": AGENT_SYSTEM_PROMPT,
                    "tools": TOOLS,                   
                    "tool_choice": (
                        {"type": "tool", "name": "submit_assessment"}
                        if force_submission else {"type": "any"}
                    ),
                    "messages": messages,
                },
            )
            if resp.is_error:
                return {
                    "error": "Agent provider request failed.",
                    "raw": resp.text[:1000],
                    "trace": trace,
                }
            data = resp.json()

            if "content" not in data:
                return {"error": "Agent call failed.", "raw": data, "trace": trace}

            stop_reason = data.get("stop_reason")
            messages.append({"role": "assistant", "content": data["content"]})

            if stop_reason != "tool_use":
                # A normal-text reply is not sufficient. Retry once while
                # explicitly forcing the schema-backed submission tool.
                if force_submission:
                    fallback_text = "\n".join(
                        b["text"] for b in data["content"] if b.get("type") == "text"
                    )
                    return {
                        "error": "Agent did not return a structured assessment.",
                        "fallback_text": fallback_text,
                        "trace": trace,
                    }
                messages.append({
                    "role": "user",
                    "content": (
                        "Submit the completed assessment now by calling "
                        "submit_assessment. Do not return free text."
                    ),
                })
                force_submission = True
                continue

            tool_results = []
            submitted_assessment = None
            for block in data["content"]:
                if block.get("type") != "tool_use":
                    continue
                tool_name = block["name"]
                tool_input = block.get("input", {})

                if tool_name == "submit_assessment":
                    submitted_assessment = tool_input
                    continue

                handler = TOOL_DISPATCH.get(tool_name)
                result = handler(db, patient_id, tool_input) if handler else {"error": f"Unknown tool {tool_name}"}
                trace.append({"tool": tool_name, "input": tool_input, "result": result})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": json.dumps(result),
                })

            if submitted_assessment is not None:
                return {"assessment": submitted_assessment, "trace": trace}

            messages.append({"role": "user", "content": tool_results})

            # Reserve the final loop pass for the required structured result.
            if _ == MAX_TOOL_ITERATIONS - 2:
                force_submission = True

    return {"error": "Agent did not converge within iteration limit.", "trace": trace}
