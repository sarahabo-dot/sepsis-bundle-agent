import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from data_validation import (
    range_check, staleness_check, plausibility_check,
    evaluate_clinical_value, requires_dual_confirmation,
)
from models import ClinicalValue, DataStatus


def test_range_check_within_and_outside_bounds():
    assert range_check("gcs", 12) is True
    assert range_check("gcs", 20) is False   # GCS cannot exceed 15
    assert range_check("platelets", 300) is True
    assert range_check("platelets", 9000) is False


def test_staleness_check():
    now = datetime.utcnow()
    fresh = now - timedelta(minutes=5)
    old = now - timedelta(hours=2)
    assert staleness_check("map_mmhg", fresh, now=now) is True   # 15 min window
    assert staleness_check("map_mmhg", old, now=now) is False


def test_plausibility_check_flags_implausible_jump():
    # platelets ceiling is 150 x10^3/uL change
    assert plausibility_check("platelets", 250, 240) is True
    assert plausibility_check("platelets", 250, 20) is False  # implausible swing -> possible sample swap


def test_evaluate_clinical_value_confirms_normal_value():
    cv = ClinicalValue(value=90, unit="mmHg", source="monitor", timestamp=datetime.utcnow())
    result = evaluate_clinical_value("map_mmhg", cv)
    assert result.status == DataStatus.CONFIRMED


def test_evaluate_clinical_value_flags_stale():
    old_ts = datetime.utcnow() - timedelta(hours=5)
    cv = ClinicalValue(value=90, unit="mmHg", source="monitor", timestamp=old_ts)
    result = evaluate_clinical_value("map_mmhg", cv)
    assert result.status == DataStatus.STALE


def test_evaluate_clinical_value_flags_out_of_range_as_draft():
    cv = ClinicalValue(value=999, unit="mmHg", source="manual_entry", timestamp=datetime.utcnow())
    result = evaluate_clinical_value("map_mmhg", cv)
    assert result.status == DataStatus.DRAFT


def test_evaluate_clinical_value_flags_implausible_jump_as_draft_fm01():
    cv = ClinicalValue(value=20, unit="x10^3/uL", source="lab_interface", timestamp=datetime.utcnow())
    result = evaluate_clinical_value("platelets", cv, previous_value=250)
    assert result.status == DataStatus.DRAFT
    assert "Implausible" in result.flag_reason


def test_dual_confirmation_required_for_high_risk_items_fm05():
    assert requires_dual_confirmation("pressors") is True
    assert requires_dual_confirmation("fluids") is True
    assert requires_dual_confirmation("cultures") is False
