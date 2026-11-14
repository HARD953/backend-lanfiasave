"""Lutte contre la pauvreté : indice multidimensionnel, besoins non couverts, trajectoires et cadre de suivi."""
import uuid
from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.accounts.roles import Role
from apps.assistance.models import Assistance
from apps.beneficiaries.models import Household, HouseholdMembership, Person
from apps.poverty.index import assess
from apps.surveys.models import SurveyResponse

pytestmark = pytest.mark.django_db

POOR = {"household_size": 6, "monthly_income": "lt25k", "activity_status": "none", "meals_per_day": "one",
        "housing_type": "precarious", "water_access": "none", "sanitation": "none", "electricity": "none",
        "children_out_of_school": "yes", "literacy": "no", "health_coverage": "no", "health_access": "difficult",
        "child_malnutrition": "no", "birth_certificate": "no", "coping_strategy": "reduced"}
NOT_POOR = {"household_size": 3, "monthly_income": "gt120k", "activity_status": "stable", "meals_per_day": "three",
            "housing_type": "rented", "water_access": "private", "sanitation": "private", "electricity": "yes",
            "children_out_of_school": "no", "literacy": "yes", "health_coverage": "yes", "health_access": "easy",
            "child_malnutrition": "no", "birth_certificate": "yes", "coping_strategy": "none"}


def test_index_classifies_households():
    poor, ok = assess(POOR), assess(NOT_POOR)
    assert poor.sufficient and poor.poor and poor.severe
    assert ok.sufficient and not ok.poor and ok.score == 0
    # Trop peu de réponses : le ménage est « à compléter », jamais compté pauvre.
    partial = assess({"monthly_income": "none"})
    assert not partial.sufficient and not partial.poor
    # Les valeurs spéciales (« ne sait pas ») ne sont pas des privations.
    assert assess({**NOT_POOR, "literacy": "__unknown__"}).deprivations["literacy"] is None


def _household(org, questionnaire, territory, answers, *, days_ago=0, name="Awa"):
    hh = Household.objects.create(organization=org, territory=territory)
    p = Person.objects.create(organization=org, first_name=name, last_name="Test", sex="F", territory=territory)
    HouseholdMembership.objects.create(household=hh, person=p, relation="head", start_date=date(2026, 1, 1))
    SurveyResponse.objects.create(organization=org, person=p, household=hh, questionnaire=questionnaire,
                                  answers=answers, client_uuid=uuid.uuid4(),
                                  collected_at=timezone.now() - timedelta(days=days_ago))
    return hh, p


def test_poverty_report_measures_gaps_and_trajectories(client_for, make_user, org, questionnaire, territory):
    h1, p1 = _household(org, questionnaire, territory, POOR, name="Aya")
    _household(org, questionnaire, territory, POOR, name="Mariam")
    h3, p3 = _household(org, questionnaire, territory, POOR, days_ago=200, name="Fatou")
    # Fatou est réévaluée aujourd'hui : elle est sortie de la pauvreté.
    SurveyResponse.objects.create(organization=org, person=p3, household=h3, questionnaire=questionnaire,
                                  answers=NOT_POOR, client_uuid=uuid.uuid4(), collected_at=timezone.now())
    # Aya a reçu des vivres : sa faim est prise en charge.
    Assistance.objects.create(organization=org, person=p1, household=h1, category="food", status="delivered",
                              delivered_at=timezone.now())

    manager = make_user("resp", Role.PROGRAM_MANAGER, org)
    data = client_for(manager).get(f"/api/v1/reports/poverty/?organization={org.pk}").data
    s = data["summary"]
    assert (s["assessed"], s["poor"], s["poverty_rate"]) == (3, 2, 66.7)
    assert s["poor_coverage_rate"] == 50.0 and s["targeting_rate"] == 100.0
    gaps = {g["code"]: g["count"] for g in data["gaps"]}
    assert gaps["hunger"] == 1 and gaps["poor_without_aid"] == 1
    assert data["trajectories"]["followed"] == 1 and data["trajectories"]["exited"] == 1
    assert data["trajectories"]["exit_rate"] == 100.0
    # Série trimestrielle : avant la réévaluation, seule Fatou (alors pauvre) était évaluée.
    assert data["series"][-1]["rate"] == 66.7
    assert data["series"][-2]["assessed"] == 1 and data["series"][-2]["rate"] == 100.0
    assert sum(d["contribution"] for d in data["dimensions"]) == pytest.approx(100, abs=0.5)
    assert data["priority"][0]["head"]["name"] == "Mariam Test"  # sans aucune aide : en tête de liste
    abobo = next(t for t in data["territories"] if t["name"] == "Abobo")
    assert abobo["suppressed"] is False and abobo["rate"] == 66.7

    # Un bailleur voit les agrégats, jamais la liste nominative.
    funder = make_user("bailleur", Role.FUNDER, org)
    data = client_for(funder).get(f"/api/v1/reports/poverty/?organization={org.pk}").data
    assert data["summary"]["poor"] == 2 and "priority" not in data


def test_indicator_targets(client_for, make_user, org, questionnaire, territory):
    _household(org, questionnaire, territory, POOR)
    _household(org, questionnaire, territory, NOT_POOR, name="Koffi")
    manager = make_user("resp", Role.PROGRAM_MANAGER, org)
    funder = make_user("bailleur", Role.FUNDER, org)
    url = f"/api/v1/reports/indicators/?organization={org.pk}"

    rows = {r["code"]: r for r in client_for(manager).get(url).data["indicators"]}
    assert rows["poverty_rate"]["value"] == 50.0 and rows["poverty_rate"]["status"] == "no_target"

    body = {"organization": str(org.pk), "code": "poverty_rate", "baseline": 60, "target": 40}
    assert client_for(funder).post("/api/v1/reports/indicators/targets/", body, format="json").status_code == 403
    r = client_for(manager).post("/api/v1/reports/indicators/targets/", body, format="json")
    assert r.status_code == 200, r.content
    data = client_for(manager).get(url).data
    row = next(r for r in data["indicators"] if r["code"] == "poverty_rate")
    assert data["can_edit_targets"] is True
    assert row["status"] == "in_progress" and row["progress"] == 50

    r = client_for(manager).post("/api/v1/reports/indicators/targets/",
                                 {"organization": str(org.pk), "code": "inconnu", "target": 1}, format="json")
    assert r.status_code == 400
    r = client_for(manager).post("/api/v1/reports/indicators/targets/",
                                 {"organization": str(org.pk), "code": "poverty_rate", "target": None},
                                 format="json")
    assert r.status_code == 200
    row = next(r for r in client_for(manager).get(url).data["indicators"] if r["code"] == "poverty_rate")
    assert row["target"] is None
