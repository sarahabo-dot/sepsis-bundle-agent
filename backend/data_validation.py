"""
data_validation.py
Implements mitigations derived from the ICU Agent FMEA:

  FM-01 (contaminated/mismatched/mishandled sample) -> plausibility_check()
  FM-02 (manual data entry error)                    -> range_check()
  FM-03 (data transfer error)                        -> staleness_check()
  FM-05 (no double-checking before high-risk action)  -> requires_dual_confirmation()

None of these functions call an LLM. They are deterministic gates that
decide whether a ClinicalValue is CONFIRMED, DRAFT, or STALE before it is
allowed into a SOFA calculation or a bundle action.
"""

from datetime import datetime, timedelta
from typing import Optional
from models import ClinicalValue, DataStatus

# Physiologically plausible ranges. Values outside these are not rejected
# outright (a true extreme is possible in a critically ill patient) but are
# flagged DRAFT and require physician confirmation before use -- this is the
# double-grounding principle applied to lab/monitor intake.
PLAUSIBLE_RANGES = {
    "pao2_fio2": (40, 650),
    "platelets": (0, 700),
    "bilirubin": (0, 40),
    "map_mmhg": (20, 160),
    "gcs": (3, 15),
    "creatinine": (0, 20),
    "urine_output_24h": (0, 6000),
    "lactate": (0, 25),
}

# Maximum age before a value is considered stale and excluded from live
# scoring. Mitigates FM-03 (data transfer / sync error).
FRESHNESS_WINDOW = {
    "pao2_fio2": timedelta(minutes=30),
    "platelets": timedelta(hours=12),
    "bilirubin": timedelta(hours=12),
    "map_mmhg": timedelta(minutes=15),
    "gcs": timedelta(hours=4),
    "creatinine": timedelta(hours=12),
    "urine_output_24h": timedelta(hours=2),
    "lactate": timedelta(hours=2),
}

# Rate-of-change ceilings: a jump larger than this between consecutive
# readings for the same domain is treated as suspicious rather than a true
# clinical swing, and is routed to DRAFT for manual reconciliation.
# Mitigates FM-01 (sample mix-up) more directly than a static range does.
MAX_PLAUSIBLE_DELTA = {
    "platelets": 150,      # x10^3/uL within a short interval
    "bilirubin": 8,        # mg/dL
    "creatinine": 3,       # mg/dL
}


def range_check(domain: str, value: Optional[float]) -> bool:
    """Returns True if value is within the physiologically plausible range.
    Mitigates FM-02 (unit confusion / transcription slips surface as
    out-of-range values)."""
    if value is None or domain not in PLAUSIBLE_RANGES:
        return True
    lo, hi = PLAUSIBLE_RANGES[domain]
    return lo <= value <= hi


def staleness_check(domain: str, timestamp: datetime, now: Optional[datetime] = None) -> bool:
    """Returns True if the value is still fresh enough to use live.
    Mitigates FM-03 (data transfer / sync failures silently reusing old data)."""
    now = now or datetime.utcnow()
    window = FRESHNESS_WINDOW.get(domain, timedelta(hours=24))
    return (now - timestamp) <= window


def plausibility_check(domain: str, value: Optional[float], previous_value: Optional[float]) -> bool:
    """Returns True if the jump from previous_value to value is within a
    clinically plausible rate of change. Mitigates FM-01 (contaminated,
    mislabeled, or swapped specimens tend to produce implausible jumps)."""
    if value is None or previous_value is None:
        return True
    ceiling = MAX_PLAUSIBLE_DELTA.get(domain)
    if ceiling is None:
        return True
    return abs(value - previous_value) <= ceiling


def evaluate_clinical_value(domain: str, cv: ClinicalValue,
                             previous_value: Optional[float] = None,
                             now: Optional[datetime] = None) -> ClinicalValue:
    """Applies all three gates and returns an updated ClinicalValue with the
    resulting status. Does not mutate the input in place -- callers should
    use the returned object, preserving the append-only audit principle."""
    if cv.value is None:
        return cv

    if not staleness_check(domain, cv.timestamp, now):
        return ClinicalValue(cv.value, cv.unit, cv.source, cv.timestamp,
                              status=DataStatus.STALE,
                              flag_reason=f"Value older than freshness window for {domain}")

    if not range_check(domain, cv.value):
        return ClinicalValue(cv.value, cv.unit, cv.source, cv.timestamp,
                              status=DataStatus.DRAFT,
                              flag_reason=f"Value outside physiologic range for {domain}")

    if not plausibility_check(domain, cv.value, previous_value):
        return ClinicalValue(cv.value, cv.unit, cv.source, cv.timestamp,
                              status=DataStatus.DRAFT,
                              flag_reason=f"Implausible change from prior {domain} value; "
                                          f"verify specimen / re-measure before use")

    return ClinicalValue(cv.value, cv.unit, cv.source, cv.timestamp, status=DataStatus.CONFIRMED)


# ---- FM-05 mitigation: dual confirmation for high-risk actions ----

HIGH_RISK_BUNDLE_ITEMS = {"pressors", "fluids", "antibiotics"}


def requires_dual_confirmation(bundle_item_key: str) -> bool:
    """High-risk bundle actions require two distinct clinician sign-offs
    (acknowledged_by + confirmed_by) rather than a single quick-confirm,
    directly addressing FM-05 from the FMEA."""
    return bundle_item_key in HIGH_RISK_BUNDLE_ITEMS
