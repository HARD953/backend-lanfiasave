"""Formulaires Express et Standard : sous-ensembles du questionnaire complet, compatibles avec le score v2."""
import uuid

import pytest
from django.utils import timezone

from apps.beneficiaries.models import Person
from apps.surveys.models import QuestionnaireVersion

pytestmark = pytest.mark.django_db


def test_three_published_forms_with_variants(client_for, enumerator):
    data = client_for(enumerator).get("/api/v1/questionnaires/").data
    by_variant = {q["variant"]["variant"]: q for q in data}
    assert set(by_variant) == {"express", "standard", "complete"}
    assert by_variant["express"]["questions_count"] < by_variant["standard"]["questions_count"] \
        < by_variant["complete"]["questions_count"]
    assert "eviction" in by_variant["express"]["variant"]["recommended_for"]


def test_express_collect_scores_and_flags(client_for, enumerator, social_worker, org, territory):
    express = QuestionnaireVersion.objects.get(code="RECENSEMENT_EXPRESS")
    payload = {
        "client_uuid": str(uuid.uuid4()), "organization": str(org.pk), "questionnaire": str(express.pk),
        "territory": str(territory.pk), "collected_at": timezone.now().isoformat(),
        "members": [{"client_uuid": str(uuid.uuid4()), "first_name": "Koffi", "relation": "head",
                     "answers": {"housing_type": "street", "displacement_status": "evicted",
                                 "lost_housing": "yes", "meals_per_day": "one"}}],
    }
    r = client_for(enumerator).post("/api/v1/collect/submissions/", payload, format="json")
    assert r.status_code == 201, r.content
    person = Person.objects.get(first_name="Koffi")
    assert {"evicted", "homeless", "food_insecure"} <= set(person.vulnerabilities.values_list("category", flat=True))
    score = client_for(social_worker).post(
        f"/api/v1/survey-responses/{r.data['members'][0]['survey_response_id']}/score/").data
    assert score["total"] > 0 and score["is_reliable"] is False  # formulaire court : score indicatif


def test_express_rejects_question_not_in_form(client_for, enumerator, org):
    express = QuestionnaireVersion.objects.get(code="RECENSEMENT_EXPRESS")
    payload = {
        "client_uuid": str(uuid.uuid4()), "organization": str(org.pk), "questionnaire": str(express.pk),
        "collected_at": timezone.now().isoformat(),
        "members": [{"client_uuid": str(uuid.uuid4()), "first_name": "X", "relation": "head",
                     "answers": {"tenure": "squat"}}],
    }
    assert client_for(enumerator).post("/api/v1/collect/submissions/", payload, format="json").status_code == 400
