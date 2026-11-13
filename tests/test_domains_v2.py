"""Administration des comptes, opérations (déguerpissements, catastrophes, mines), vulnérabilités et aides."""
import uuid

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.accounts.roles import Role
from apps.assistance.models import Assistance
from apps.beneficiaries.models import Person, PersonVulnerability
from apps.operations.models import OperationCase
from apps.scoring.engine import SCORING_V2, compute_score

pytestmark = pytest.mark.django_db


# --- Administration des comptes ---------------------------------------------------------------------------------

@pytest.fixture
def org_admin(make_user, org):
    return make_user("chef", Role.ORG_ADMIN, org)


def test_org_admin_creates_account_with_roles_and_temp_password(client_for, org_admin, org):
    r = client_for(org_admin).post("/api/v1/admin/users/", {
        "username": "agent.yop", "first_name": "Koffi", "last_name": "Yao", "organization": str(org.pk),
        "roles": [Role.ENUMERATOR, Role.FIELD_SUPERVISOR]}, format="json")
    assert r.status_code == 201, r.content
    temp = r.data["temporary_password"]
    assert {m["role"] for m in r.data["memberships"]} == {"enumerator", "field_supervisor"}

    c = APIClient()
    login = c.post("/api/v1/auth/login/", {"username": "agent.yop", "password": temp}, format="json")
    assert login.status_code == 200 and login.data["user"]["must_change_password"] is True
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
    r = c.post("/api/v1/auth/change-password/", {"old_password": temp, "new_password": "Nouveau-Mot-2026!"},
               format="json")
    assert r.status_code == 200
    assert User.objects.get(username="agent.yop").must_change_password is False


def test_org_admin_cannot_manage_another_organization(client_for, org_admin, other_org, outsider):
    c = client_for(org_admin)
    r = c.post("/api/v1/admin/users/", {"username": "intrus", "first_name": "X", "organization": str(other_org.pk),
                                        "roles": [Role.SOCIAL_WORKER]}, format="json")
    assert r.status_code == 403
    assert c.get(f"/api/v1/admin/users/{outsider.pk}/").status_code == 404


def test_roles_update_reset_and_deactivate(client_for, org_admin, enumerator, org):
    c = client_for(org_admin)
    r = c.post(f"/api/v1/admin/users/{enumerator.pk}/roles/", {"organization": str(org.pk),
                                                              "roles": [Role.SOCIAL_WORKER]}, format="json")
    assert r.status_code == 200 and [m["role"] for m in r.data["memberships"]] == ["social_worker"]
    temp = c.post(f"/api/v1/admin/users/{enumerator.pk}/reset-password/").data["temporary_password"]
    assert APIClient().post("/api/v1/auth/login/", {"username": enumerator.username, "password": temp},
                            format="json").status_code == 200
    assert c.patch(f"/api/v1/admin/users/{enumerator.pk}/", {"is_active": False}, format="json").status_code == 200
    assert APIClient().post("/api/v1/auth/login/", {"username": enumerator.username, "password": temp},
                            format="json").status_code == 401
    # pas d'auto-désactivation ni de retrait de son propre rôle d'administrateur
    assert c.patch(f"/api/v1/admin/users/{org_admin.pk}/", {"is_active": False}, format="json").status_code == 400


def test_non_admin_cannot_access_user_admin(client_for, social_worker):
    assert client_for(social_worker).get("/api/v1/admin/users/").status_code == 403


def test_platform_admin_creates_organization_and_first_admin(client_for, make_user):
    root = make_user("plateforme")
    root.is_superuser = root.is_staff = True
    root.save()
    c = client_for(root)
    org = c.post("/api/v1/admin/organizations/", {"code": "mairie-demo", "name": "Mairie démo",
                                                  "kind": "local_authority"}, format="json")
    assert org.status_code == 201
    r = c.post("/api/v1/admin/users/", {"username": "maire.admin", "first_name": "Aya", "organization": org.data["id"],
                                        "roles": [Role.ORG_ADMIN]}, format="json")
    assert r.status_code == 201


# --- Opérations et dossiers de personnes affectées ----------------------------------------------------------------

@pytest.fixture
def manager(make_user, org):
    return make_user("pm", Role.PROGRAM_MANAGER, org)


@pytest.fixture
def eviction(client_for, manager, org, territory):
    c = client_for(manager)
    gov = c.post("/api/v1/partners/", {"organization": str(org.pk), "name": "Ministère test",
                                       "kind": "government"}, format="json").data
    op = c.post("/api/v1/operations/", {"organization": str(org.pk), "title": "Déguerpissement test",
                                        "kind": "eviction", "initiated_by": gov["id"], "territory": str(territory.pk),
                                        "compensation_amount_per_household": 250000}, format="json")
    assert op.status_code == 201, op.content
    return op.data


def test_enroll_person_opens_case_and_flags_vulnerability(client_for, manager, social_worker, eviction, org):
    p = Person.objects.create(organization=org, first_name="Adjoua")
    r = client_for(social_worker).post(f"/api/v1/operations/{eviction['id']}/enroll/",
                                       {"person": str(p.pk), "lost_housing": True, "needs": ["shelter", "bogus"]},
                                       format="json")
    assert r.status_code == 201, r.content
    case = OperationCase.objects.get(person=p)
    assert case.lost_housing and case.needs == ["shelter"]
    v = PersonVulnerability.objects.get(person=p, category="evicted")
    assert v.status == "confirmed" and v.source == "operation"
    again = client_for(social_worker).post(f"/api/v1/operations/{eviction['id']}/enroll/", {"person": str(p.pk)},
                                           format="json")
    assert again.status_code == 200 and again.data["already_enrolled"] == [p.public_code]
    stats = client_for(manager).get(f"/api/v1/operations/{eviction['id']}/stats/").data
    assert stats["persons"] == 1 and stats["lost_housing"] == 1 and stats["persons_without_aid"] == 1


def test_operation_isolated_between_organizations(client_for, outsider, eviction):
    assert client_for(outsider).get(f"/api/v1/operations/{eviction['id']}/").status_code == 404


def test_collect_attaches_household_to_operation(client_for, enumerator, eviction, submission):
    payload = submission(operation=eviction["id"])
    payload["members"][0]["answers"].update({"birth_certificate": "no", "lost_housing": "yes"})
    payload["members"][0]["vulnerabilities"] = ["disability"]
    payload["members"].append({"client_uuid": str(uuid.uuid4()), "first_name": "Petit", "relation": "child",
                               "vulnerabilities": ["out_of_school"]})
    r = client_for(enumerator).post("/api/v1/collect/submissions/", payload, format="json")
    assert r.status_code == 201, r.content
    head = Person.objects.get(first_name="Aya")
    assert OperationCase.objects.filter(operation_id=eviction["id"]).count() == 2
    assert OperationCase.objects.get(person=head).lost_housing is True
    cats = set(head.vulnerabilities.values_list("category", flat=True))
    assert {"evicted", "disability", "no_birth_certificate"} <= cats


# --- Vulnérabilités ---------------------------------------------------------------------------------------------

def test_sensitive_vulnerability_hidden_from_enumerator(client_for, social_worker, enumerator, org):
    p = Person.objects.create(organization=org, first_name="Rokia")
    sw = client_for(social_worker)
    assert sw.post("/api/v1/vulnerabilities/", {"person": str(p.pk), "category": "gbv_survivor"},
                   format="json").status_code == 201
    assert sw.post("/api/v1/vulnerabilities/", {"person": str(p.pk), "category": "disability"},
                   format="json").status_code == 201
    listing = client_for(enumerator).get(f"/api/v1/vulnerabilities/?person={p.pk}").data["results"]
    seen = {v["category"] for v in listing}
    assert seen == {"disability"}
    assert client_for(enumerator).post("/api/v1/vulnerabilities/", {"person": str(p.pk), "category": "widow"},
                                       format="json").status_code == 403


def test_vulnerability_resolve_keeps_history(client_for, social_worker, org):
    p = Person.objects.create(organization=org, first_name="Awa")
    c = client_for(social_worker)
    v = c.post("/api/v1/vulnerabilities/", {"person": str(p.pk), "category": "homeless"}, format="json").data
    c.post(f"/api/v1/vulnerabilities/{v['id']}/resolve/", {"notes": "Relogée"}, format="json")
    again = c.post("/api/v1/vulnerabilities/", {"person": str(p.pk), "category": "homeless"}, format="json")
    assert again.status_code == 201
    assert PersonVulnerability.objects.filter(person=p, category="homeless").count() == 2


# --- Aides ------------------------------------------------------------------------------------------------------

def test_aid_lifecycle_with_proof_and_cancellation(client_for, social_worker, enumerator, eviction, org):
    p = Person.objects.create(organization=org, first_name="Mariam")
    sw = client_for(social_worker)
    case = sw.post(f"/api/v1/operations/{eviction['id']}/enroll/", {"person": str(p.pk)}, format="json").data
    case_id = case["created"][0]["id"]
    assert client_for(enumerator).post("/api/v1/assistances/", {"person": str(p.pk), "category": "food"},
                                       format="json").status_code == 403
    a = sw.post("/api/v1/assistances/", {"person": str(p.pk), "operation_case": case_id, "category": "relocation",
                                         "amount": 250000}, format="json")
    assert a.status_code == 201, a.content
    aid = a.data["id"]
    assert sw.post(f"/api/v1/assistances/{aid}/deliver/", {}, format="json").status_code == 400  # preuve exigée
    r = sw.post(f"/api/v1/assistances/{aid}/deliver/", {"delivery_proof": "OM-TX-001",
                                                        "delivery_channel": "mobile_money"}, format="json")
    assert r.status_code == 200 and r.data["status"] == "delivered"
    assert OperationCase.objects.get(pk=case_id).status == "supported"
    assert sw.patch(f"/api/v1/assistances/{aid}/", {"amount": 1}, format="json").status_code == 409
    assert sw.post(f"/api/v1/assistances/{aid}/cancel/", {"reason": ""}, format="json").status_code == 400
    assert sw.post(f"/api/v1/assistances/{aid}/cancel/", {"reason": "Doublon de paiement"},
                   format="json").status_code == 200
    assert Assistance.objects.get(pk=aid).status == "cancelled"
    summary = sw.get(f"/api/v1/assistances/summary/?person={p.pk}").data
    assert summary["delivered"] == 0 and summary["cancelled"] == 1


def test_beneficiary_sees_aids_and_cases(client_for, social_worker, eviction, org):
    p = Person.objects.create(organization=org, first_name="Akissi")
    sw = client_for(social_worker)
    sw.post(f"/api/v1/operations/{eviction['id']}/enroll/", {"person": str(p.pk), "needs": ["shelter"]}, format="json")
    sw.post("/api/v1/assistances/", {"person": str(p.pk), "category": "food", "amount": 30000, "deliver_now": True,
                                     "delivery_proof": "LISTE-12"}, format="json")
    access = sw.post(f"/api/v1/persons/{p.pk}/beneficiary-access/").data
    c = APIClient()
    tok = c.post("/api/v1/auth/beneficiary/login/", {"identifier": access["identifier"], "pin": access["pin"]},
                 format="json").data["access"]
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {tok}")
    aids = c.get("/api/v1/me/assistances/").data
    assert aids[0]["status"] == "delivered" and aids[0]["amount"] == 30000
    cases = c.get("/api/v1/me/cases/").data
    assert cases[0]["needs"] == ["Abri / relogement"]


def test_overview_includes_operations_and_aids(client_for, social_worker, eviction, org):
    p = Person.objects.create(organization=org, first_name="Aminata")
    client_for(social_worker).post(f"/api/v1/operations/{eviction['id']}/enroll/", {"person": str(p.pk)},
                                   format="json")
    data = client_for(social_worker).get("/api/v1/reports/overview/").data
    assert data["operations"]["affected_persons"] == 1 and data["operations"]["affected_without_aid"] == 1
    assert any(v["code"] == "evicted" for v in data["vulnerabilities"])


def test_catalogs_hide_sensitive_categories_from_enumerator(client_for, enumerator, social_worker):
    enum_codes = {c["code"] for c in client_for(enumerator).get("/api/v1/catalogs/").data["vulnerabilities"]}
    sw_codes = {c["code"] for c in client_for(social_worker).get("/api/v1/catalogs/").data["vulnerabilities"]}
    assert "gbv_survivor" not in enum_codes and "gbv_survivor" in sw_codes


# --- Score v2 ---------------------------------------------------------------------------------------------------

def test_scoring_v2_max_profile_is_100_and_gbv_not_scored():
    worst = {"housing_type": "street", "tenure": "squat", "water_access": "none", "electricity": "none",
             "sanitation": "none", "chronic_illness": "yes", "disability": "severe", "health_access": "no",
             "health_coverage": "no", "child_malnutrition": "yes", "monthly_income": "none", "activity_status": "none",
             "dependents": 6, "meals_per_day": "none", "days_without_food": 5, "literacy": "no",
             "children_out_of_school": "yes", "child_labour": "yes", "birth_certificate": "no",
             "protection_risk": "yes", "lost_documents": "yes", "support_network": "none", "lives_alone": "yes",
             "lost_housing": "yes", "lost_livelihood": "yes", "self_assessment": "very_difficult"}
    out = compute_score(worst, SCORING_V2)
    assert out.total == 100 and out.completeness == 1.0
    assert compute_score({**worst, "gbv_disclosed": "yes", "risk_zone_declared": "yes"}, SCORING_V2).total == 100


def test_survey_v2_answers_derive_suspected_vulnerabilities(client_for, social_worker, org, questionnaire):
    p = Person.objects.create(organization=org, first_name="Brahima", estimated_age=72)
    r = client_for(social_worker).post("/api/v1/survey-responses/", {
        "organization": str(org.pk), "person": str(p.pk), "questionnaire": str(questionnaire.pk),
        "collected_at": timezone.now().isoformat(),
        "answers": {"lives_alone": "yes", "meals_per_day": "one", "mining_zone": "yes", "child_labour": "yes"}},
        format="json")
    assert r.status_code == 201, r.content
    cats = dict(p.vulnerabilities.values_list("category", "status"))
    assert cats == {"elderly_isolated": "suspected", "food_insecure": "suspected", "mining_affected": "suspected",
                    "child_labour": "suspected"}
