import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sofa_calculator import (
    score_respiratory, score_coagulation, score_liver,
    score_cardiovascular, score_cns, score_renal,
    calculate_sofa, delta_sofa, meets_sepsis3_criteria,
)
from models import SofaInput, ClinicalValue, PressorState, PressorDrug
from datetime import datetime


def _cv(v):
    return ClinicalValue(value=v, unit="u", source="test", timestamp=datetime.utcnow()) if v is not None else None


def test_respiratory_thresholds():
    assert score_respiratory(450) == 0
    assert score_respiratory(350) == 1
    assert score_respiratory(250) == 2
    assert score_respiratory(150) == 3
    assert score_respiratory(80) == 4
    assert score_respiratory(None) is None


def test_coagulation_thresholds():
    assert score_coagulation(200) == 0
    assert score_coagulation(120) == 1
    assert score_coagulation(70) == 2
    assert score_coagulation(30) == 3
    assert score_coagulation(10) == 4


def test_liver_thresholds():
    assert score_liver(1.0) == 0
    assert score_liver(1.5) == 1
    assert score_liver(3.0) == 2
    assert score_liver(8.0) == 3
    assert score_liver(15.0) == 4


def test_cv_map_only():
    assert score_cardiovascular(75, PressorDrug.NONE, None) == 0
    assert score_cardiovascular(65, PressorDrug.NONE, None) == 1


def test_cv_pressor_overrides_map():
    # even with a "normal" MAP, an active pressor drives the score
    assert score_cardiovascular(80, PressorDrug.NOREPINEPHRINE, 0.15) == 4
    assert score_cardiovascular(80, PressorDrug.NOREPINEPHRINE, 0.05) == 3
    assert score_cardiovascular(80, PressorDrug.DOPAMINE, 3) == 2
    assert score_cardiovascular(80, PressorDrug.DOBUTAMINE, 1) == 2


def test_cns_thresholds():
    assert score_cns(15) == 0
    assert score_cns(14) == 1
    assert score_cns(11) == 2
    assert score_cns(7) == 3
    assert score_cns(4) == 4


def test_renal_worst_of_creatinine_and_uo():
    # creatinine says mild (1), urine output says severe (4) -> worst wins
    assert score_renal(1.5, 150) == 4
    assert score_renal(1.5, None) == 1
    assert score_renal(None, 150) == 4


def test_calculate_sofa_partial_data_reports_completeness():
    data = SofaInput(
        pao2_fio2=_cv(250),
        platelets=_cv(120),
        bilirubin=None,
        map_mmhg=None,
        pressor=PressorState(),
        gcs=_cv(15),
        creatinine=None,
        urine_output_24h=None,
    )
    result = calculate_sofa(data)
    assert result.total == 2 + 1 + 0  # resp=2, coag=1, cns=0
    assert result.completeness == 3 / 6
    assert set(result.missing_domains) == {"liver", "cardiovascular", "renal"}


def test_delta_and_sepsis3_criteria():
    data = SofaInput(pao2_fio2=_cv(150), pressor=PressorState())
    result = calculate_sofa(data)  # resp=3, rest missing -> total 3
    assert delta_sofa(result, baseline_total=1) == 2
    assert meets_sepsis3_criteria(result, baseline_total=1) is True
    assert meets_sepsis3_criteria(result, baseline_total=2) is False


def test_negative_and_invalid_values_are_ignored():
    assert score_respiratory(-10) is None
    assert score_coagulation("not_a_number") is None
