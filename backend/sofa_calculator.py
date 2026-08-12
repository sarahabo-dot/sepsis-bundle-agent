"""
sofa_calculator.py
Deterministic SOFA (Sequential Organ Failure Assessment) scoring.

Design principle: every threshold below is a fixed clinical rule, never
inferred by a model. The LLM layer (see api/main.py -> /interpretation)
only narrates results computed here; it never recomputes or overrides them.
"""

from datetime import datetime
from typing import Optional
from models import SofaInput, SofaResult, PressorDrug


def _f(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v >= 0 else None


def score_respiratory(pao2_fio2: Optional[float]) -> Optional[int]:
    v = _f(pao2_fio2)
    if v is None:
        return None
    if v >= 400:
        return 0
    if v >= 300:
        return 1
    if v >= 200:
        return 2
    if v >= 100:
        return 3
    return 4


def score_coagulation(platelets: Optional[float]) -> Optional[int]:
    v = _f(platelets)
    if v is None:
        return None
    if v >= 150:
        return 0
    if v >= 100:
        return 1
    if v >= 50:
        return 2
    if v >= 20:
        return 3
    return 4


def score_liver(bilirubin: Optional[float]) -> Optional[int]:
    v = _f(bilirubin)
    if v is None:
        return None
    if v < 1.2:
        return 0
    if v < 2.0:
        return 1
    if v < 6.0:
        return 2
    if v < 12.0:
        return 3
    return 4


def score_cardiovascular(map_mmhg: Optional[float], pressor_drug: PressorDrug,
                          pressor_dose: Optional[float]) -> Optional[int]:
    if pressor_drug and pressor_drug != PressorDrug.NONE:
        dose = _f(pressor_dose) or 0.0
        if pressor_drug == PressorDrug.DOBUTAMINE:
            return 2
        if pressor_drug == PressorDrug.DOPAMINE:
            if dose > 15:
                return 4
            if dose > 5:
                return 3
            if dose > 0:
                return 2
        if pressor_drug in (PressorDrug.NOREPINEPHRINE, PressorDrug.EPINEPHRINE):
            if dose > 0.1:
                return 4
            if dose > 0:
                return 3
    v = _f(map_mmhg)
    if v is None:
        return None
    return 0 if v >= 70 else 1


def score_cns(gcs: Optional[float]) -> Optional[int]:
    v = _f(gcs)
    if v is None:
        return None
    if v >= 15:
        return 0
    if v >= 13:
        return 1
    if v >= 10:
        return 2
    if v >= 6:
        return 3
    return 4


def score_renal(creatinine: Optional[float], urine_output_24h: Optional[float]) -> Optional[int]:
    c = _f(creatinine)
    u = _f(urine_output_24h)

    from_creat = None
    if c is not None:
        if c < 1.2:
            from_creat = 0
        elif c < 2.0:
            from_creat = 1
        elif c < 3.5:
            from_creat = 2
        elif c < 5.0:
            from_creat = 3
        else:
            from_creat = 4

    from_uo = None
    if u is not None:
        if u < 200:
            from_uo = 4
        elif u < 500:
            from_uo = 3
        else:
            from_uo = 0

    if from_creat is None and from_uo is None:
        return None
    if from_creat is None:
        return from_uo
    if from_uo is None:
        return from_creat
    return max(from_creat, from_uo)  # worst-of-two, per Sepsis-3 convention


DOMAIN_LABELS = ["respiratory", "coagulation", "liver", "cardiovascular", "cns", "renal"]


def calculate_sofa(data: SofaInput) -> SofaResult:
    """Compute SOFA total + per-domain breakdown. Never raises on missing data;
    missing domains are excluded from the total and reported via completeness."""
    components = {
        "respiratory": score_respiratory(data.pao2_fio2.value if data.pao2_fio2 else None),
        "coagulation": score_coagulation(data.platelets.value if data.platelets else None),
        "liver": score_liver(data.bilirubin.value if data.bilirubin else None),
        "cardiovascular": score_cardiovascular(
            data.map_mmhg.value if data.map_mmhg else None,
            data.pressor.drug,
            data.pressor.dose_mcg_kg_min,
        ),
        "cns": score_cns(data.gcs.value if data.gcs else None),
        "renal": score_renal(
            data.creatinine.value if data.creatinine else None,
            data.urine_output_24h.value if data.urine_output_24h else None,
        ),
    }
    present = [v for v in components.values() if v is not None]
    missing = [k for k, v in components.items() if v is None]
    total = sum(present)
    completeness = len(present) / len(DOMAIN_LABELS)

    return SofaResult(
        components=components,
        total=total,
        completeness=completeness,
        missing_domains=missing,
        timestamp=datetime.utcnow(),
    )


def delta_sofa(current: SofaResult, baseline_total: int) -> int:
    """Sepsis-3: acute change in SOFA >= 2 indicates organ dysfunction consistent
    with sepsis, in the presence of suspected infection."""
    return current.total - baseline_total


def meets_sepsis3_criteria(current: SofaResult, baseline_total: int) -> bool:
    return delta_sofa(current, baseline_total) >= 2
