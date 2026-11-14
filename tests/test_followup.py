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


def _beneficiary_client(sw_client, person):
    from django.core.cache import cache
    from rest_framework.test import APIClient

    cache.clear()  # la limite de connexions (10/min) est partagée entre les tests
    access = sw_client.post(f"/api/v1/persons/{person.pk}/beneficiary-access/").data
    c = APIClient()
    tok = c.post("/api/v1/auth/beneficiary/login/", {"identifier": access["identifier"], "pin": access["pin"]},
                 format="json").data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tok}")
    return c


def test_beneficiary_reports_problem_and_rates_answer(client_for, social_worker, org, person):
    sw = client_for(social_worker)
    me = _beneficiary_client(sw, person)
    assert me.post("/api/v1/me/grievances/", {"category": "fraud", "description": "court"},
                   format="json").status_code == 400
    r = me.post("/api/v1/me/grievances/", {"category": "fraud", "callback": True,
                                           "description": "Un agent m'a demandé 5000 F pour m'inscrire."},
                format="json")
    assert r.status_code == 201, r.content
    g = Grievance.objects.get(pk=r.data["id"])
    assert (g.channel, g.priority, g.person_id, g.organization_id) == ("app", "high", person.pk, org.pk)
    assert "rappelée" in g.description
    # Le personnel la voit dans sa file ; la personne ne voit que les siennes, sans notes internes.
    assert any(x["id"] == str(g.pk) for x in sw.get("/api/v1/grievances/").data["results"])
    # Pas d'évaluation avant la réponse.
    assert me.post(f"/api/v1/me/grievances/{g.pk}/feedback/", {"satisfaction": "satisfied"},
                   format="json").status_code == 400
    assert sw.post(f"/api/v1/grievances/{g.pk}/transition/",
                   {"status": "resolved", "note": "Agent suspendu, inscription refaite gratuitement."},
                   format="json").status_code == 200
    mine = me.get("/api/v1/me/grievances/").data
    assert mine["results"][0]["answered"] and "suspendu" in mine["results"][0]["resolution"]
    assert {c["value"] for c in mine["categories"]} >= {"sea", "fraud", "payment_delay"}
    assert me.post(f"/api/v1/me/grievances/{g.pk}/feedback/", {"satisfaction": "partly"},
                   format="json").status_code == 200
    assert Grievance.objects.get(pk=g.pk).satisfaction == "partly"
    # Personnel et autres bénéficiaires n'ont pas accès à ces routes / à cette plainte.
    assert sw.get("/api/v1/me/grievances/").status_code == 403
    other = Person.objects.create(organization=org, first_name="Yao")
    assert _beneficiary_client(sw, other).post(f"/api/v1/me/grievances/{g.pk}/feedback/",
                                               {"satisfaction": "satisfied"}, format="json").status_code == 404


def test_beneficiary_open_grievances_are_capped(client_for, social_worker, person):
    me = _beneficiary_client(client_for(social_worker), person)
    for _ in range(5):
        payload = {"category": "information", "description": "Quand aura lieu la distribution ?"}
        assert me.post("/api/v1/me/grievances/", payload, format="json").status_code == 201
    assert me.post("/api/v1/me/grievances/", {"category": "information", "description": "Encore une question ici."},
                   format="json").status_code == 400
