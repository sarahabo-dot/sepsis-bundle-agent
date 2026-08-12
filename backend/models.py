"""
models.py
Shared data structures for the Sepsis Bundle Agent.

Governance principle (inherited from Shura): deterministic computation is
strictly separated from LLM narration. Nothing in this module or in
sofa_calculator.py / bundle_tracker.py ever calls an LLM. These are plain
data containers and pure functions only.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class DataStatus(str, Enum):
    """Provenance status for any clinical value entering the system."""
    CONFIRMED = "confirmed"      # passed double-grounding and plausibility checks
    DRAFT = "draft"              # flagged, requires physician confirmation before use
    STALE = "stale"              # exceeded the freshness window, excluded from live scoring


class PressorDrug(str, Enum):
    NONE = "none"
    DOPAMINE = "dopamine"
    DOBUTAMINE = "dobutamine"
    NOREPINEPHRINE = "norepinephrine"
    EPINEPHRINE = "epinephrine"


@dataclass
class ClinicalValue:
    """A single clinical data point with full provenance."""
    value: Optional[float]
    unit: str
    source: str                      # e.g. "lab_interface", "bedside_monitor", "manual_entry"
    timestamp: datetime
    status: DataStatus = DataStatus.CONFIRMED
    flag_reason: Optional[str] = None


@dataclass
class PressorState:
    drug: PressorDrug = PressorDrug.NONE
    dose_mcg_kg_min: Optional[float] = None


@dataclass
class SofaInput:
    """Raw grouped inputs for one SOFA calculation pass."""
    pao2_fio2: Optional[ClinicalValue] = None
    platelets: Optional[ClinicalValue] = None
    bilirubin: Optional[ClinicalValue] = None
    map_mmhg: Optional[ClinicalValue] = None
    pressor: PressorState = field(default_factory=PressorState)
    gcs: Optional[ClinicalValue] = None
    creatinine: Optional[ClinicalValue] = None
    urine_output_24h: Optional[ClinicalValue] = None


@dataclass
class SofaResult:
    components: dict            # domain -> int | None
    total: int
    completeness: float         # 0.0 - 1.0
    missing_domains: list
    timestamp: datetime


@dataclass
class BundleItemState:
    key: str
    label: str
    required: bool               # whether currently applicable given patient state
    reason_if_not_required: Optional[str]
    done: bool = False
    confirmed_at: Optional[datetime] = None
    acknowledged_by: Optional[str] = None   # first clinician sign-off
    confirmed_by: Optional[str] = None      # second clinician sign-off (high-risk items only)


@dataclass
class AuditEvent:
    """Append-only provenance log entry. Never mutated or deleted."""
    event_id: str
    timestamp: datetime
    actor: str
    action: str
    detail: dict
