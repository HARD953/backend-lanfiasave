"""IA-01, IA-02 : score à 7 dimensions, déterministe, sans 0 implicite pour les données manquantes."""
from apps.scoring.engine import SCORING_V1, compute_score

FULL = {
    "housing_type": "precarious", "water_access": "none", "electricity": "none",
    "chronic_illness": "yes", "disability": "severe", "health_access": "no",
    "monthly_income": "none", "activity_status": "none", "dependents": 6,
    "literacy": "no", "children_out_of_school": "yes",
    "birth_certificate": "no", "protection_risk": "yes",
    "support_network": "none", "lives_alone": "yes",
    "self_assessment": "very_difficult",
}


def test_caps_sum_to_100():
    assert sum(d["cap"] for d in SCORING_V1["dimensions"]) == 100


def test_maximum_profile_scores_100_and_very_high():
    out = compute_score(FULL)
    assert out.total == 100
    assert out.band["code"] == "very_high"
    assert out.completeness == 1.0 and out.is_reliable


def test_deterministic_recompute():
    assert compute_score(FULL).as_dict() == compute_score(dict(FULL)).as_dict()


def test_missing_answers_are_reported_not_scored_as_zero():
    out = compute_score({"housing_type": "owned"})
    assert out.total == 0
    assert out.completeness < 0.1
    assert not out.is_reliable  # un score sur données incomplètes est signalé comme non fiable
    assert any(m["question"] == "water_access" and m["reason"] == "Non renseigné" for m in out.missing)


def test_special_values_are_distinguished():
    out = compute_score({"housing_type": "__refused__", "water_access": "__unknown__"})
    reasons = {m["question"]: m["reason"] for m in out.missing}
    assert reasons["housing_type"] == "Refus de répondre"
    assert reasons["water_access"] == "Ne sait pas"


def test_bands_thresholds():
    assert compute_score({}).band["code"] == "low"
    out = compute_score({"housing_type": "precarious", "water_access": "none", "electricity": "none",
                         "chronic_illness": "yes", "disability": "severe", "health_access": "no"})
    assert out.total == 40 and out.band["code"] == "medium"


def test_territorial_risk_does_not_change_individual_score():
    base = compute_score(FULL).total
    assert compute_score({**FULL, "risk_zone_declared": "yes"}).total == base
