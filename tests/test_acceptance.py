"""Scénarios de recette du cahier LFS-CDC-2026-2.1 §28.2 couverts par le socle L0/L1."""
import pytest

from apps.accounts.roles import Role
from apps.beneficiaries.models import DuplicateCandidate, Person
from apps.cases.models import Application, Decision
from apps.core.models import AuditEvent
from apps.surveys.models import QuestionnaireVersion

pytestmark = pytest.mark.django_db


# TC-01 — dossier sans pièce d'identité
def test_tc01_create_person_without_id_document(client_for, social_worker, org):
    r = client_for(social_worker).post("/api/v1/persons/", {
        "organization": str(org.pk), "first_name": "Adjoua", "estimated_age": 40}, format="json")
    assert r.status_code == 201, r.content
    p = Person.objects.get(pk=r.data["id"])
    assert p.is_provisional and not p.has_identity_document
    assert p.public_code.startswith("LFS-")
    assert AuditEvent.objects.filter(action="person.created", object_id=str(p.pk)).exists()


# TC-02 — masquage des champs sensibles selon le rôle
def test_tc02_sensitive_fields_masked_for_enumerator(client_for, social_worker, enumerator, org):
    p = Person.objects.create(organization=org, first_name="Aya", phone="0707123456",
                              health_coverage_number="CMU123456")
    full = client_for(social_worker).get(f"/api/v1/persons/{p.pk}/").data
    masked = client_for(enumerator).get(f"/api/v1/persons/{p.pk}/").data
    assert full["phone"] == "0707123456"
    assert masked["phone"].endswith("56") and "0707" not in masked["phone"]
    assert "CMU123" not in masked["health_coverage_number"]


# TC-03 — doublon signalé, jamais fusionné automatiquement ; fusion logique réversible
def test_tc03_duplicates_flagged_and_merge_reversible(client_for, make_user, social_worker, org):
    c = client_for(social_worker)
    a = c.post("/api/v1/persons/", {"organization": str(org.pk), "first_name": "Moussa", "last_name": "Koné",
                                    "phone": "0505000000"}, format="json").data
    b = c.post("/api/v1/persons/", {"organization": str(org.pk), "first_name": "Moussa", "last_name": "Kone",
                                    "phone": "0505000000"}, format="json").data
    cand = DuplicateCandidate.objects.get()
    assert cand.confidence >= 90 and cand.status == "open"
    assert Person.objects.filter(merged_into__isnull=True).count() == 2  # aucune fusion automatique

    supervisor = make_user("sup", Role.FIELD_SUPERVISOR, org)
    s = client_for(supervisor)
    r = s.post(f"/api/v1/persons/{b['id']}/merge/", {"keep": a["id"], "reason": "Même personne"}, format="json")
    assert r.status_code == 200
    assert Person.objects.get(pk=b["id"]).merged_into_id is not None
    r = s.post(f"/api/v1/persons/{b['id']}/unmerge/", {"reason": "Erreur de fusion"}, format="json")
    assert r.status_code == 200 and Person.objects.get(pk=b["id"]).merged_into_id is None


# TC-04 — synchronisation hors ligne rejouée deux fois : zéro doublon
def test_tc04_offline_sync_is_idempotent(client_for, enumerator, submission):
    c = client_for(enumerator)
    payload = submission()
    first = c.post("/api/v1/collect/submissions/", payload, format="json")
    second = c.post("/api/v1/collect/submissions/", payload, format="json")
    assert first.status_code == 201, first.content
    assert second.status_code == 200
    assert first.data["members"][0]["person_id"] == second.data["members"][0]["person_id"]
    assert Person.objects.count() == 1


def test_collect_requires_exactly_one_head(client_for, enumerator, submission):
    payload = submission()
    payload["members"][0]["relation"] = "child"
    r = client_for(enumerator).post("/api/v1/collect/submissions/", payload, format="json")
    assert r.status_code == 400


def test_collect_rejects_invalid_answer(client_for, enumerator, submission):
    payload = submission()
    payload["members"][0]["answers"] = {"housing_type": "chateau"}
    r = client_for(enumerator).post("/api/v1/collect/submissions/", payload, format="json")
    assert r.status_code == 400


# TC-05 — une version publiée du questionnaire est figée
def test_tc05_published_questionnaire_is_frozen(questionnaire):
    from django.core.exceptions import ValidationError

    questionnaire.definition = {**questionnaire.definition, "title": "modifié"}
    with pytest.raises(ValidationError):
        questionnaire.save()
    assert QuestionnaireVersion.objects.get(pk=questionnaire.pk).definition["title"] != "modifié"


# TC-06 — score explicable, versionné, recalculable
def test_tc06_score_is_versioned_and_explained(client_for, enumerator, social_worker, submission):
    sync = client_for(enumerator).post("/api/v1/collect/submissions/", submission(), format="json").data
    resp_id = sync["members"][0]["survey_response_id"]
    r = client_for(social_worker).post(f"/api/v1/survey-responses/{resp_id}/score/")
    assert r.status_code == 201, r.content
    assert r.data["scoring_version_label"] == "VULN v2"
    assert len(r.data["details"]["dimensions"]) == 7
    assert r.data["details"]["missing"], "les questions non répondues doivent être listées"
    assert "consultatif" in r.data["disclaimer"]
    again = client_for(social_worker).post(f"/api/v1/survey-responses/{resp_id}/score/").data
    assert again["total"] == r.data["total"]


# TC-07 / IA-03 / DEC-01 — décision humaine motivée, jamais automatique
def _app_to_committee(c, program, person):
    app = c.post("/api/v1/applications/", {"program": str(program.pk), "person": str(person.pk)},
                 format="json").data
    for to in ["admissible", "under_review", "committee"]:
        r = c.post(f"/api/v1/applications/{app['id']}/transition/", {"to": to}, format="json")
        assert r.status_code == 200, r.content
    return app


def test_tc07_decision_requires_committee_and_motive(client_for, social_worker, committee, program, org):
    person = Person.objects.create(organization=org, first_name="Akissi")
    sw = client_for(social_worker)
    app = sw.post("/api/v1/applications/", {"program": str(program.pk), "person": str(person.pk)},
                  format="json").data
    # le travailleur social ne peut pas décider
    assert sw.post(f"/api/v1/applications/{app['id']}/decide/", {"outcome": "approved", "motive": "x" * 20},
                   format="json").status_code in (403, 404)
    # pas de décision hors comité
    cm = client_for(committee)
    r = cm.post(f"/api/v1/applications/{app['id']}/decide/", {"outcome": "approved", "motive": "x" * 20},
                format="json")
    assert r.status_code == 409
    # on ne peut pas sauter directement à « décidée » par transition
    assert sw.post(f"/api/v1/applications/{app['id']}/transition/", {"to": "decided"},
                   format="json").status_code == 409
    for to in ["admissible", "under_review", "committee"]:
        sw.post(f"/api/v1/applications/{app['id']}/transition/", {"to": to}, format="json")
    r = cm.post(f"/api/v1/applications/{app['id']}/decide/", {"outcome": "rejected", "motive": "court"},
                format="json")
    assert r.status_code == 400  # motif obligatoire
    r = cm.post(f"/api/v1/applications/{app['id']}/decide/",
                {"outcome": "approved", "motive": "Ménage très exposé, avis social favorable."}, format="json")
    assert r.status_code == 201
    assert Application.objects.get(pk=app["id"]).status == "decided"
    assert AuditEvent.objects.filter(action="decision.recorded").exists()


def test_quorum_not_met_only_allows_deferral(client_for, social_worker, committee, program, org):
    person = Person.objects.create(organization=org, first_name="Rokia")
    app = _app_to_committee(client_for(social_worker), program, person)
    r = client_for(committee).post(f"/api/v1/applications/{app['id']}/decide/",
                                   {"outcome": "approved", "motive": "Motif suffisant ici", "quorum_met": False},
                                   format="json")
    assert r.status_code == 400


def test_conflict_of_interest_author_cannot_decide(client_for, make_user, program, org):
    both = make_user("double", Role.SOCIAL_WORKER, org)
    from apps.accounts.models import Membership

    Membership.objects.create(user=both, organization=org, role=Role.COMMITTEE_MEMBER)
    person = Person.objects.create(organization=org, first_name="Salimata")
    c = client_for(both)
    app = _app_to_committee(c, program, person)
    r = c.post(f"/api/v1/applications/{app['id']}/decide/", {"outcome": "approved", "motive": "Motif valable"},
               format="json")
    assert r.status_code == 403


# TC-09 / TC-17 / SEC-01 — isolation stricte entre organisations
def test_tc17_cross_tenant_access_denied(client_for, outsider, social_worker, org, program):
    p = Person.objects.create(organization=org, first_name="Awa")
    c = client_for(outsider)
    assert c.get(f"/api/v1/persons/{p.pk}/").status_code == 404
    assert c.get("/api/v1/persons/").data["count"] == 0
    assert c.get(f"/api/v1/programs/{program.pk}/").status_code == 404
    r = c.post("/api/v1/persons/", {"organization": str(org.pk), "first_name": "Intrus"}, format="json")
    assert r.status_code == 403
    assert AuditEvent.objects.filter(action="access.denied", outcome="denied").exists()


def test_technical_superuser_has_no_implicit_social_access(client_for, make_user, org):
    Person.objects.create(organization=org, first_name="Awa")
    root = make_user("root")
    root.is_superuser = root.is_staff = True
    root.save()
    assert client_for(root).get("/api/v1/persons/").status_code == 403


def test_program_restricted_membership(client_for, make_user, org, program):
    from apps.programs.models import Program

    other_prog = Program.objects.create(organization=org, code="autre", name="Autre", status="active")
    limited = make_user("limite", Role.SOCIAL_WORKER, org, program=program)
    ids = {p["id"] for p in client_for(limited).get("/api/v1/programs/").data["results"]}
    assert str(program.pk) in ids and str(other_prog.pk) not in ids


# TC-15 — recours et réexamen
def test_tc15_appeal_reopens_committee(client_for, social_worker, committee, committee2, program, org):
    person = Person.objects.create(organization=org, first_name="Amenan")
    app = _app_to_committee(client_for(social_worker), program, person)
    d = client_for(committee).post(f"/api/v1/applications/{app['id']}/decide/",
                                   {"outcome": "rejected", "motive": "Pièces insuffisantes au dossier"},
                                   format="json").data
    appeal = client_for(social_worker).post("/api/v1/appeals/", {"decision": d["id"],
                                                                 "motive": "Nouvelles pièces fournies"},
                                            format="json")
    assert appeal.status_code == 201, appeal.content
    # l'auteur de la décision ne traite pas le recours
    assert client_for(committee).post(f"/api/v1/appeals/{appeal.data['id']}/resolve/",
                                      {"upheld": True, "resolution": "Réexamen accordé"},
                                      format="json").status_code == 403
    r = client_for(committee2).post(f"/api/v1/appeals/{appeal.data['id']}/resolve/",
                                    {"upheld": True, "resolution": "Réexamen accordé au vu des pièces"},
                                    format="json")
    assert r.status_code == 200
    assert Application.objects.get(pk=app["id"]).status == "committee"
    d2 = client_for(committee).post(f"/api/v1/applications/{app['id']}/decide/",
                                    {"outcome": "approved", "motive": "Pièces complètes, situation confirmée"},
                                    format="json").data
    assert Decision.objects.get(pk=d2["id"]).supersedes_id == Decision.objects.get(pk=d["id"]).pk


# Espace bénéficiaire : ne voit ni son score ni les notes internes
def test_beneficiary_portal_hides_internal_data(client_for, social_worker, committee, program, org):
    from rest_framework.test import APIClient

    person = Person.objects.create(organization=org, first_name="Affoué")
    sw = client_for(social_worker)
    app = _app_to_committee(sw, program, person)
    client_for(committee).post(f"/api/v1/applications/{app['id']}/decide/",
                               {"outcome": "rejected", "motive": "Critère de résidence non rempli",
                                "internal_notes": "NOTE INTERNE CONFIDENTIELLE"}, format="json")
    access = sw.post(f"/api/v1/persons/{person.pk}/beneficiary-access/").data
    anon = APIClient()
    login = anon.post("/api/v1/auth/beneficiary/login/", {"identifier": access["identifier"], "pin": access["pin"]},
                      format="json")
    assert login.status_code == 200, login.content
    anon.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
    apps = anon.get("/api/v1/me/applications/")
    body = str(apps.content)
    assert apps.status_code == 200
    assert "NOTE INTERNE" not in body and "score" not in body
    assert apps.data[0]["decision"]["motive"] == "Critère de résidence non rempli"
    # le bénéficiaire ne peut pas accéder à l'API du personnel
    assert anon.get("/api/v1/persons/").status_code == 403
    r = anon.post("/api/v1/me/appeals/", {"decision": apps.data[0]["decision"]["id"],
                                          "motive": "Je réside bien à Abobo"}, format="json")
    assert r.status_code == 201


def test_staff_login_and_failed_login_audited(db, make_user, org):
    from rest_framework.test import APIClient

    from tests.conftest import PWD

    make_user("agent", Role.SOCIAL_WORKER, org)
    c = APIClient()
    assert c.post("/api/v1/auth/login/", {"username": "agent", "password": "faux"}).status_code == 401
    assert AuditEvent.objects.filter(action="auth.login_failed").exists()
    ok = c.post("/api/v1/auth/login/", {"username": "agent", "password": PWD})
    assert ok.status_code == 200 and ok.data["user"]["memberships"][0]["role"] == "social_worker"


def test_audit_log_is_append_only(db):
    from django.core.exceptions import PermissionDenied

    ev = AuditEvent.objects.create(action="test")
    ev.reason = "réécriture"
    with pytest.raises(PermissionDenied):
        ev.save()
    with pytest.raises(PermissionDenied):
        ev.delete()


def test_program_cannot_activate_without_rule(client_for, make_user, org):
    pm = make_user("pm", Role.PROGRAM_MANAGER, org)
    c = client_for(pm)
    p = c.post("/api/v1/programs/", {"organization": str(org.pk), "code": "neuf", "name": "Neuf"},
               format="json").data
    for to in ["submitted", "approved"]:
        assert c.post(f"/api/v1/programs/{p['id']}/transition/", {"to": to}, format="json").status_code == 200
    assert c.post(f"/api/v1/programs/{p['id']}/transition/", {"to": "active"}, format="json").status_code == 409
    c.post(f"/api/v1/programs/{p['id']}/rules/", {"description": "Critères publiés", "rules": {}}, format="json")
    assert c.post(f"/api/v1/programs/{p['id']}/transition/", {"to": "active"}, format="json").status_code == 200


# RPT-01 — indicateurs calculés sur les données réelles, périmètre limité à l'organisation
def test_overview_report_counts_latest_score_per_person(client_for, social_worker, enumerator, submission, org,
                                                       outsider):
    c = client_for(enumerator)
    sync = c.post("/api/v1/collect/submissions/", submission(), format="json").data
    resp_id = sync["members"][0]["survey_response_id"]
    sw = client_for(social_worker)
    sw.post(f"/api/v1/survey-responses/{resp_id}/score/")
    sw.post(f"/api/v1/survey-responses/{resp_id}/score/")
    r = sw.get(f"/api/v1/reports/overview/?organization={org.pk}")
    assert r.status_code == 200, r.content
    assert r.data["persons"]["total"] == 1
    assert sum(r.data["score_bands"].values()) == 1  # dernier score uniquement
    assert client_for(outsider).get("/api/v1/reports/overview/").data["persons"]["total"] == 0
