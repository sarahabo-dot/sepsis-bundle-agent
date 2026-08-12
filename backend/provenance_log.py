"""
provenance_log.py
Append-only audit trail, consistent with Shura's logging principle: every
clinical or system event is written once and never edited or deleted.
This in-memory version is for the prototype; swap the store for a durable
append-only table (e.g. Postgres with no UPDATE/DELETE grants) in production.
"""

import uuid
from datetime import datetime
from typing import List
from models import AuditEvent


class ProvenanceLog:
    def __init__(self):
        self._events: List[AuditEvent] = []

    def record(self, actor: str, action: str, detail: dict) -> AuditEvent:
        event = AuditEvent(
            event_id=str(uuid.uuid4()),
            timestamp=datetime.utcnow(),
            actor=actor,
            action=action,
            detail=detail,
        )
        self._events.append(event)
        return event

    def all_events(self) -> List[AuditEvent]:
        return list(self._events)  # defensive copy; callers cannot mutate history

    def events_for(self, action: str) -> List[AuditEvent]:
        return [e for e in self._events if e.action == action]

    def completeness_rate(self) -> float:
        """Fraction of high-risk actions that have both acknowledged_by and
        confirmed_by attribution -- a KPI tracked per the FMEA (FM-05) and
        the project's governance/safety KPI set."""
        confirmations = [e for e in self._events if e.action == "bundle_item_confirmed"]
        if not confirmations:
            return 1.0
        fully_attributed = [
            e for e in confirmations
            if e.detail.get("acknowledged_by") and e.detail.get("confirmed_by")
            or not e.detail.get("dual_confirmation_required", False)
        ]
        return len(fully_attributed) / len(confirmations)
