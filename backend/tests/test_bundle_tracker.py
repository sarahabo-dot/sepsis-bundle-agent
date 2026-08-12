import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from bundle_tracker import (
    evaluate_relevance, build_bundle_state, elapsed_minutes,
    overdue_items, bundle_summary,
)


def test_relevance_stable_patient_normal_lactate():
    rel = evaluate_relevance(map_mmhg=80, lactate_initial=1.5)
    assert rel["fluids"][0] is False
    assert rel["pressors"][0] is False
    assert rel["lactate_repeat"][0] is False
    assert rel["cultures"][0] is True  # always required


def test_relevance_hypotensive_patient():
    rel = evaluate_relevance(map_mmhg=55, lactate_initial=1.0)
    assert rel["fluids"][0] is True
    assert rel["pressors"][0] is True


def test_relevance_high_lactate_triggers_fluids_and_repeat():
    rel = evaluate_relevance(map_mmhg=80, lactate_initial=4.5)
    assert rel["fluids"][0] is True
    assert rel["lactate_repeat"][0] is True
    assert rel["pressors"][0] is False  # MAP is fine, pressors not triggered by lactate alone


def test_overdue_detection():
    recognition = datetime.utcnow() - timedelta(minutes=75)
    states = build_bundle_state(recognition, map_mmhg=80, lactate_initial=1.0, confirmations={})
    overdue = overdue_items(states, recognition)
    overdue_keys = {s.key for s in overdue}
    assert "cultures" in overdue_keys
    assert "antibiotics" in overdue_keys
    assert "lactate" in overdue_keys


def test_completed_items_not_overdue():
    recognition = datetime.utcnow() - timedelta(minutes=75)
    confirmations = {"cultures": {"done": True, "confirmed_at": datetime.utcnow(),
                                   "acknowledged_by": "dr_a", "confirmed_by": None}}
    states = build_bundle_state(recognition, map_mmhg=80, lactate_initial=1.0, confirmations=confirmations)
    overdue = overdue_items(states, recognition)
    assert "cultures" not in {s.key for s in overdue}


def test_bundle_summary_counts_only_required_items():
    states = build_bundle_state(None, map_mmhg=80, lactate_initial=1.0, confirmations={})
    summary = bundle_summary(states)
    # only the 3 "always" items are required when patient is stable and lactate is normal
    assert summary["required_count"] == 3
    assert summary["completed_count"] == 0
    assert summary["complete"] is False


def test_elapsed_minutes_none_when_not_started():
    assert elapsed_minutes(None) is None
