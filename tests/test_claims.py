"""Dispositif MMPE : circuit des demandes, séparation des fonctions, confidentialité et tableau du ministère."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import Membership
from apps.accounts.roles import Role
from apps.claims.models import Claim, ProtectedReport
from apps.operations.models import Operation, Partner
from apps.territories.models import Territory

pytestmark = pytest.mark.django_db
API = "/api/v1"


@pytest.fixture
def zone(territory):
    sp = Territory.objects.create(code="SP-DRP", name="Doropo SP", level="subprefecture", parent=territory)
    other = Territory.objects.create(code="SP-KAL", name="Kalamon", level="subprefecture", parent=territory)
    village = Territory.objects.create(code="V-1", name="Village 1", level="village", parent=sp)
    return {"sp": sp, "other": other, "village": village}


@pytest.fixture
def site(org):
    exploitant = Partner.objects.create(organization=org, name="Exploitant fictif", kind="mining_company")
    op = Operation.objects.create(organization=org, title="Projet aurifère fictif", kind="mining_project",
                                  status="ongoing", initiated_by=exploitant)
    return op, exploitant


@pytest.fixture
def people(make_user, org, site):
    _, exploitant = site
    users = {
        "relay": make_user("relais", Role.RELAY, org),
        "cell": make_user("cellule", Role.SOCIAL_WORKER, org),
        "committee": make_user("comite", Role.COMMITTEE_MEMBER, org),
        "prefect": make_user("prefet", Role.PREFECT, org),
        "tech": make_user("agri", Role.TECHNICAL_SERVICE, org),
        "payer": make_user("payeur", Role.PAYER, org),
        "operator": make_user("exploitant", Role.OPERATOR, org),
        "ministry": make_user("ministere", Role.MINISTRY, org),
        "independent": make_user("independant", Role.APPEAL_INDEPENDENT, org),
        "evaluator": make_user("evaluateur", Role.EVALUATOR, org),
    }
    Membership.objects.filter(user=users["operator"]).update(partner=exploitant)
    return users


def _claim(client_for, user, org, zone, site, **extra):
    op, exploitant = site
    body = {"organization": str(org.pk), "claimant_name": "Awa Koné", "claimant_sex": "F",
            "claimant_birth_year": 1980, "claimant_phone": "0700000001", "village": "Village 1",
            "territory": str(zone["village"].pk), "household_size": 6, "channel": "relay", "sector": "mining",
            "impacts": ["land_loss", "water_access"], "profile": "indirect", "income_before": 60000,
            "income_after": 15000, "operation": str(op.pk), "operator": str(exploitant.pk), "sources": ["relay"],
            "no_identity_document": True, "community_validated": True, **extra}
    r = client_for(user).post(f"{API}/claims/", body, format="json")
    assert r.status_code == 201, r.content
    return r.data


def test_full_circuit_with_separation_of_duties(client_for, people, org, zone, site):
    data = _claim(client_for, people["relay"], org, zone, site)
    cid = data["id"]
    assert data["reference"].startswith("DM-") and data["status"] == "registered"
    assert data["priority_score"] > 0 and data["priority_factors"]
    # Le relais voit sa demande jusqu'à la transmission, et le récépissé.
    relay = client_for(people["relay"])
    assert relay.get(f"{API}/claims/{cid}/receipt/").data["reference"] == data["reference"]

    cell, committee, prefect = (client_for(people[k]) for k in ("cell", "committee", "prefect"))
    assert cell.post(f"{API}/claims/{cid}/triage/", {"technical_service": "agriculture"}, format="json") \
        .status_code == 200
    assert relay.get(f"{API}/claims/{cid}/").status_code == 404  # transmise à la cellule : plus visible
    assert cell.post(f"{API}/claims/{cid}/inquiry/", {"social_inquiry": "Visite faite : parcelle louée perdue."},
                     format="json").status_code == 200
    r = client_for(people["tech"]).post(f"{API}/claims/{cid}/technical-opinion/",
                                        {"service": "agriculture", "opinion": "Parcelle dans l'emprise.",
                                         "link_confirmed": "yes"}, format="json")
    assert r.status_code == 200, r.content
    # La cellule ne propose pas ; le comité propose ; le préfet valide.
    body = {"outcome": "support_orientation", "motive": "Perte de revenu liée à l'emprise confirmée.",
            "amount": 75000, "measures": ["Transfert transitoire", "Orientation RSU"]}
    assert cell.post(f"{API}/claims/{cid}/propose/", body, format="json").status_code == 403
    assert committee.post(f"{API}/claims/{cid}/propose/", body, format="json").status_code == 200
    assert committee.post(f"{API}/claims/{cid}/validate/", {"approve": True}, format="json").status_code == 403
    r = prefect.post(f"{API}/claims/{cid}/validate/", {"approve": True}, format="json")
    assert r.status_code == 200 and r.data["status"] == "decided" and r.data["decision_days"] == 0

    r = cell.post(f"{API}/claims/{cid}/support/", {"services": ["rsu"]}, format="json")
    assert r.status_code == 200 and r.data["status"] == "support" and r.data["rsu_status"] == "referred"
    # Le payeur voit l'identité, le montant et le moyen de paiement, puis confirme avec une preuve.
    payer = client_for(people["payer"])
    pays = payer.get(f"{API}/claim-payments/").data["results"]
    assert len(pays) == 1 and float(pays[0]["amount"]) == 75000 and "description" not in pays[0]
    assert payer.post(f"{API}/claim-payments/{pays[0]['id']}/confirm/", {}, format="json").status_code == 400
    assert payer.post(f"{API}/claim-payments/{pays[0]['id']}/confirm/", {"proof": "MM-123456"},
                      format="json").status_code == 200
    assert payer.get(f"{API}/claims/{cid}/").status_code == 404  # le payeur ne voit pas le dossier

    assert cell.post(f"{API}/claims/{cid}/followup/", {"note": "Visite à 1 mois"}, format="json").status_code == 200
    r = cell.post(f"{API}/claims/{cid}/close/", {"motive": "Appui reçu, revenu partiellement rétabli."},
                  format="json")
    assert r.status_code == 200 and r.data["status"] == "closed"
    kinds = [e["kind"] for e in cell.get(f"{API}/claims/{cid}/").data["events"]]
    assert kinds[:3] == ["registered", "triaged", "inquiry"] and "paid" in kinds

    # Le ministère n'accède à aucun dossier, seulement aux agrégats.
    ministry = client_for(people["ministry"])
    assert ministry.get(f"{API}/claims/").data["count"] == 0
    dash = ministry.get(f"{API}/reports/ministry/?organization={org.pk}").data
    key = {k["code"]: k for k in dash["key_indicators"]}
    assert key["accompanied_rate"]["value"] == 100.0 and key["decision_delay"]["value"] == 0
    assert dash["territories"][0]["name"] == "Autres zones"  # moins de 10 ménages : fusionné
    assert "claimant_name" not in str(dash)


def test_urgent_operator_and_appeal(client_for, people, org, zone, site):
    cid = _claim(client_for, people["relay"], org, zone, site)["id"]
    cell, prefect = client_for(people["cell"]), client_for(people["prefect"])
    cell.post(f"{API}/claims/{cid}/triage/", {"urgent": True, "urgency_reason": "water"}, format="json")
    r = prefect.post(f"{API}/claims/{cid}/urgent/", {"reason": "water", "measure": "Citerne d'eau sous 48 h"},
                     format="json")
    mid = r.data["urgent_measures"][0]["id"]
    r = cell.post(f"{API}/claims/{cid}/urgent-executed/", {"measure": mid, "note": "Citerne livrée"}, format="json")
    assert r.data["urgent_measures"][0]["executed_in_time"] is True

    # Transmission à l'exploitant : il voit la réclamation, sans les coordonnées (pas d'accord du ménage).
    assert cell.post(f"{API}/claims/{cid}/transmit/", {}, format="json").status_code == 200
    operator = client_for(people["operator"])
    seen = operator.get(f"{API}/claims/{cid}/").data
    assert seen["access_mode"] == "operator" and "Awa" not in seen["claimant_name"]
    assert "social_inquiry" not in seen and "events" not in seen
    assert operator.post(f"{API}/claims/{cid}/operator-response/", {"response": "Indemnité de culture versée."},
                         format="json").status_code == 200

    # Décision de rejet, contestation, commission avec membre indépendant obligatoire.
    cell.post(f"{API}/claims/{cid}/inquiry/", {"social_inquiry": "Visite : situation vérifiée sur place."},
              format="json")
    client_for(people["committee"]).post(f"{API}/claims/{cid}/propose/", {
        "outcome": "rejected", "motive": "Lien avec l'activité non établi à ce stade."}, format="json")
    prefect.post(f"{API}/claims/{cid}/validate/", {"approve": True}, format="json")
    r = cell.post(f"{API}/claims/{cid}/appeal/", {"motive": "La parcelle est bien dans l'emprise du projet."},
                  format="json")
    assert r.status_code == 200
    appeal = r.data["appeals"][0]
    ind = client_for(people["independent"])
    r = ind.post(f"{API}/claim-appeals/{appeal['id']}/resolve/", {"upheld": True, "resolution": "Nouvel examen."},
                 format="json")
    assert r.status_code == 400  # membre indépendant obligatoire
    r = ind.post(f"{API}/claim-appeals/{appeal['id']}/resolve/", {
        "upheld": True, "resolution": "Pièces nouvelles : nouvel examen.", "independent_member": "Me Diallo"},
        format="json")
    assert r.status_code == 200 and r.data["decision_modified"] is True
    assert Claim.objects.get(pk=cid).status == "verifying"
    dash = client_for(people["ministry"]).get(f"{API}/reports/ministry/?organization={org.pk}").data
    comp = {k["code"]: k for k in dash["complementary_indicators"]}
    assert comp["urgent_in_time"]["value"] == 100.0 and comp["operator_on_time"]["value"] == 100.0
    assert comp["contested_modified"]["value"] == 1


def test_zone_restriction_and_protected_channel(client_for, people, make_user, org, zone, site):
    _claim(client_for, people["relay"], org, zone, site)
    local = make_user("cellule.kalamon", Role.SOCIAL_WORKER, org)
    Membership.objects.filter(user=local).update(territory=zone["other"])
    assert client_for(local).get(f"{API}/claims/").data["count"] == 0  # hors de sa zone
    assert client_for(people["cell"]).get(f"{API}/claims/").data["count"] == 1

    relay = client_for(people["relay"])
    r = relay.post(f"{API}/protected-reports/", {"organization": str(org.pk), "kind": "reprisal",
                                                 "description": "Menaces après la demande.", "reporter_name": "Awa",
                                                 "reporter_phone": "0700000001"}, format="json")
    assert r.status_code == 201 and r.data["is_priority"] is True
    assert relay.get(f"{API}/protected-reports/").data["count"] == 0
    assert client_for(people["operator"]).get(f"{API}/protected-reports/").data["count"] == 0
    seen = client_for(people["independent"]).get(f"{API}/protected-reports/").data["results"][0]
    assert seen["reporter_name"] == "Identité masquée" and seen["identity_on_file"] is True
    report = ProtectedReport.objects.get()
    assert report.due_at - report.created_at <= timedelta(hours=72, seconds=5)


def test_operator_list_matching_and_sms(client_for, people, org, zone, site, monkeypatch):
    op, _ = site
    cid = _claim(client_for, people["relay"], org, zone, site)["id"]
    _claim(client_for, people["relay"], org, zone, site, claimant_name="Moussa Traoré", claimant_sex="M")
    r = client_for(people["cell"]).post(f"{API}/operator-lists/", {
        "organization": str(org.pk), "operation": str(op.pk),
        "csv": "nom;village;biens\nKONÉ Awa;Village 1;Parcelle 0,5 ha\nYao Kouassi;Village 2;Verger"},
        format="json")
    assert r.status_code == 200, r.content
    assert (r.data["matched"], r.data["absent_count"], r.data["listed_without_claim"]) == (1, 1, 1)
    assert Claim.objects.get(pk=cid).in_operator_list is True

    sms = "/api/v1/integrations/sms/inbound/"
    assert client_for(people["relay"]).post(sms, {"from": "0700000001", "text": "SUIVI X"}).status_code == 503
    monkeypatch.setenv("LANFIA_SMS_INBOUND_TOKEN", "secret-test")
    from rest_framework.test import APIClient

    c = APIClient()
    ref = Claim.objects.get(pk=cid).reference
    r = c.post(sms, {"from": "+2250700000001", "text": f"SUIVI {ref}", "organization": org.code}, format="json",
               HTTP_X_LANFIA_SMS_TOKEN="secret-test")
    assert ref in r.data["reply"]
    r = c.post(sms, {"from": "0799999999", "text": "SIGNALER notre puits est sec depuis le chantier",
                     "organization": org.code}, format="json", HTTP_X_LANFIA_SMS_TOKEN="secret-test")
    assert r.status_code == 201 and Claim.objects.filter(channel="sms").count() == 1


def test_fairness_and_evaluation_sample(client_for, people, org, zone, site):
    for i in range(4):
        _claim(client_for, people["relay"], org, zone, site, claimant_name=f"Personne {i}")
    ministry = client_for(people["ministry"])
    data = ministry.get(f"{API}/reports/ministry/fairness/?organization={org.pk}").data
    assert data["claims"] == 4 and {d["dimension"] for d in data["dimensions"]} == {"Sexe", "Âge", "Localité"}
    Claim.objects.update(status="closed", closed_at=timezone.now())
    ev = client_for(people["evaluator"])
    r = ev.post(f"{API}/evaluation/samples/", {"organization": str(org.pk), "size": 2}, format="json")
    assert r.status_code == 201 and r.data["size"] == 2
    claims = ev.get(f"{API}/claims/").data
    assert claims["count"] == 2


def test_offline_claim_keeps_receipt_number_and_is_idempotent(client_for, people, org, zone, site):
    body = {"client_uuid": "6f1c2a1e-1111-4a2b-9c3d-123456789abc", "reference": "DM-ACDE-FGHJ"}
    first = _claim(client_for, people["relay"], org, zone, site, **body)
    assert first["reference"] == "DM-ACDE-FGHJ"
    r = client_for(people["relay"]).post(f"{API}/claims/", {"organization": str(org.pk), "claimant_name": "Awa Koné",
                                                            "impacts": ["land_loss"], **body}, format="json")
    assert r.status_code == 200 and r.data["id"] == first["id"]
    assert Claim.objects.count() == 1
