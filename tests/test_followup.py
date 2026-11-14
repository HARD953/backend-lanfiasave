"""Suivi social : plaintes (MGP), orientations avec contre-référence, visites, agenda et carte par territoire."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.roles import Role
from apps.beneficiaries.models import Person
from apps.followup.models import Grievance
from apps.operations.models import Partner

pytestmark = pytest.mark.django_db


@pytest.fixture
def person(org):
    return Person.objects.create(organization=org, first_name="Awa", last_name="Koné")


def test_grievance_lifecycle_with_sla_and_mandatory_answer(client_for, social_worker, org, person):
    c = client_for(social_worker)
    r = c.post("/api/v1/grievances/", {"organization": str(org.pk), "person": str(person.pk), "channel": "phone",
                                       "category": "payment_delay", "priority": "urgent",
                                       "subject": "Aide non reçue", "description": "Kit promis non remis."},
               format="json")
    assert r.status_code == 201, r.content
    gid = r.data["id"]
    assert r.data["reference"].startswith("PL-")
    assert r.data["due_date"] == str(timezone.localdate() + timedelta(days=3))

    r = c.post(f"/api/v1/grievances/{gid}/transition/", {"status": "resolved", "note": "ok"}, format="json")
    assert r.status_code == 400  # réponse trop courte
    r = c.post(f"/api/v1/grievances/{gid}/transition/", {"status": "escalated"}, format="json")
    assert r.status_code == 400  # instance manquante
    r = c.post(f"/api/v1/grievances/{gid}/transition/",
               {"status": "resolved", "note": "Kit remis le 12, reçu signé."}, format="json")
    assert r.status_code == 200 and r.data["resolution"]
    r = c.post(f"/api/v1/grievances/{gid}/transition/", {"status": "received"}, format="json")
    assert r.status_code in (400, 409)

    s = c.get(f"/api/v1/grievances/summary/?organization={org.pk}").data
    assert s["resolved"] == 1 and s["resolved_on_time_rate"] == 100


def test_anonymous_and_confidential_grievances_are_protected(client_for, social_worker, committee, org, person):
    r = client_for(social_worker).post("/api/v1/grievances/", {
        "organization": str(org.pk), "person": str(person.pk), "anonymous": True, "complainant_name": "X",
        "category": "sea", "subject": "Signalement", "description": "Détails sensibles."}, format="json")
    assert r.status_code == 201, r.content
    g = Grievance.objects.get(pk=r.data["id"])
    assert g.person is None and g.complainant_name == ""
    # Le comité voit la plainte mais pas son contenu (pas d'accès aux données sensibles).
    seen = client_for(committee).get(f"/api/v1/grievances/{g.pk}/").data
    assert seen["description"] == "Confidentiel"
    assert client_for(committee).post("/api/v1/grievances/", {
        "organization": str(org.pk), "category": "other", "subject": "x", "description": "y"},
        format="json").status_code == 403


def test_grievances_are_isolated_between_organizations(client_for, social_worker, outsider, org):
    r = client_for(social_worker).post("/api/v1/grievances/", {
        "organization": str(org.pk), "category": "information", "subject": "Info", "description": "Question"},
        format="json")
    assert client_for(outsider).get(f"/api/v1/grievances/{r.data['id']}/").status_code == 404
    assert client_for(outsider).post("/api/v1/grievances/", {
        "organization": str(org.pk), "category": "other", "subject": "x", "description": "y"},
        format="json").status_code == 403


def test_referral_requires_counter_referral_feedback(client_for, social_worker, org, person):
    partner = Partner.objects.create(organization=org, name="Centre social d'Abobo", kind="government")
    c = client_for(social_worker)
    r = c.post("/api/v1/referrals/", {"person": str(person.pk), "service": "civil_registry",
                                      "reason": "Enfant sans acte de naissance"}, format="json")
    assert r.status_code == 400  # structure manquante
    r = c.post("/api/v1/referrals/", {"person": str(person.pk), "service": "civil_registry",
                                      "partner": str(partner.pk), "reason": "Enfant sans acte de naissance"},
               format="json")
    assert r.status_code == 201, r.content
    rid = r.data["id"]
    assert c.post(f"/api/v1/referrals/{rid}/transition/", {"status": "completed"},
                  format="json").status_code == 400
    r = c.post(f"/api/v1/referrals/{rid}/transition/",
               {"status": "completed", "feedback": "Jugement supplétif obtenu"}, format="json")
    assert r.status_code == 200 and r.data["closed_at"]
    assert c.get(f"/api/v1/referrals/summary/?organization={org.pk}").data["completion_rate"] == 100


def test_visits_feed_the_agenda(client_for, social_worker, enumerator, org, person):
    today = timezone.localdate()
    c = client_for(social_worker)
    r = c.post("/api/v1/visits/", {"person": str(person.pk), "visited_at": str(today + timedelta(days=1)),
                                   "summary": "x"}, format="json")
    assert r.status_code == 400  # pas de visite dans le futur
    r = c.post("/api/v1/visits/", {"person": str(person.pk), "visited_at": str(today - timedelta(days=20)),
                                   "trend": "worsened", "summary": "Toit effondré",
                                   "next_visit_date": str(today - timedelta(days=2))}, format="json")
    assert r.status_code == 201, r.content
    agenda = client_for(enumerator).get(f"/api/v1/visits/agenda/?organization={org.pk}").data
    assert [str(v["person"]) for v in agenda["visits_due"]] == [str(person.pk)]
    assert agenda["visits_due"][0]["overdue"] is True
    # Une nouvelle visite sans date de prochaine visite retire la personne de l'agenda.
    c.post("/api/v1/visits/", {"person": str(person.pk), "visited_at": str(today), "summary": "Réparé"},
           format="json")
    assert client_for(social_worker).get(f"/api/v1/visits/agenda/?organization={org.pk}").data["visits_due"] == []


def test_territory_report_masks_small_cells(client_for, make_user, enumerator, org, submission):
    r = client_for(enumerator).post("/api/v1/collect/submissions/", submission(), format="json")
    assert r.status_code == 201, r.content
    manager = make_user("resp", Role.PROGRAM_MANAGER, org)
    data = client_for(manager).get(f"/api/v1/reports/territories/?organization={org.pk}").data
    abobo = next(t for t in data["territories"] if t["name"] == "Abobo")
    assert abobo["households"] == 1
    assert abobo["suppressed"] is True and abobo["persons"] is None


def test_catalogs_expose_followup_choices(client_for, social_worker):
    data = client_for(social_worker).get("/api/v1/catalogs/").data
    assert {"grievance_categories", "referral_services", "visit_kinds"} <= set(data)
