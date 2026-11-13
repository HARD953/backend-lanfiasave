"""
Jeu de données de démonstration ENTIÈREMENT SYNTHÉTIQUE (cahier §22 : jamais de vraies CNI, santé ou paiements).

    python manage.py seed_demo

Le mot de passe des comptes de démonstration vient de DEMO_PASSWORD (voir .env.example).
"""
import os
import random
import uuid
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Membership, User
from apps.accounts.roles import Role
from apps.beneficiaries.models import Household, HouseholdMembership, Person
from apps.beneficiaries.services import detect_duplicates
from apps.beneficiaries.vulnerability_services import flag_from_survey
from apps.cases.models import Application
from apps.organizations.models import Organization
from apps.programs.models import EligibilityRuleVersion, Program
from apps.scoring.services import active_scoring_version, score_survey_response
from apps.surveys.definitions import QUESTIONNAIRE_V2
from apps.surveys.models import QuestionnaireVersion, SurveyResponse
from apps.territories.models import Territory

DEMO_USERS = [
    ("admin.org", "Awa", "Koné", Role.ORG_ADMIN),
    ("resp.programme", "Yao", "Kouassi", Role.PROGRAM_MANAGER),
    ("travailleur.social", "Mariam", "Traoré", Role.SOCIAL_WORKER),
    ("comite.1", "Jean", "N'Guessan", Role.COMMITTEE_MEMBER),
    ("comite.2", "Fatou", "Diabaté", Role.COMMITTEE_MEMBER),
    ("recenseur", "Ibrahim", "Ouattara", Role.ENUMERATOR),
    ("superviseur", "Aminata", "Coulibaly", Role.FIELD_SUPERVISOR),
    ("financeur", "Paul", "Aka", Role.FUNDER),
    ("auditeur", "Clarisse", "Bamba", Role.AUDITOR),
]

FIRST_F = ["Aya", "Adjoua", "Mariam", "Fatoumata", "Akissi", "Affoué", "Salimata", "Rokia", "Amenan", "Awa"]
FIRST_M = ["Koffi", "Kouamé", "Moussa", "Yao", "Seydou", "Brahima", "Konan", "Drissa", "Adama", "Lassina"]
LAST = ["Kouassi", "Koné", "Traoré", "Ouattara", "Yao", "Bamba", "Coulibaly", "Diarra", "N'Dri", "Touré"]

CHOICES = {
    "housing_type": ["precarious", "rented", "shared", "owned", "street"],
    "tenure": ["squat", "untitled", "tenant", "titled"],
    "water_access": ["none", "shared", "private"],
    "electricity": ["none", "informal", "yes"],
    "sanitation": ["none", "shared", "private"],
    "literacy": ["no", "partial", "yes"],
    "birth_certificate": ["yes", "yes", "no"],
    "id_document": ["yes", "no"],
    "children_out_of_school": ["yes", "no", "no"],
    "child_labour": ["no", "no", "no", "yes"],
    "activity_status": ["none", "informal", "stable"],
    "income_source": ["agriculture", "trade", "mining", "craft", "none"],
    "monthly_income": ["none", "lt25k", "25to60k", "60to120k"],
    "meals_per_day": ["one", "two", "two", "three"],
    "chronic_illness": ["yes", "no", "no"],
    "disability": ["none", "none", "moderate", "severe"],
    "health_access": ["no", "difficult", "easy"],
    "health_coverage": ["yes", "no"],
    "child_malnutrition": ["no", "no", "yes"],
    "protection_risk": ["no", "no", "yes"],
    "displacement_status": ["resident", "resident", "evicted", "displaced"],
    "lost_livelihood": ["no", "yes"],
    "lost_documents": ["no", "no", "yes"],
    "mining_zone": ["no", "no", "yes"],
    "support_network": ["none", "weak", "strong"],
    "lives_alone": ["no", "no", "yes"],
    "self_assessment": ["very_difficult", "difficult", "manageable", "good"],
}


class Command(BaseCommand):
    help = "Crée un jeu de démonstration synthétique (idempotent)."

    @transaction.atomic
    def handle(self, *args, **options):
        rng = random.Random(2026)
        password = os.getenv("DEMO_PASSWORD")
        if not password:
            self.stderr.write("DEMO_PASSWORD n'est pas défini (voir .env.example).")
            return

        org, _ = Organization.objects.get_or_create(
            code="demo-ong", defaults={"name": "ONG Démo Solidarité (fictive)", "kind": "ngo"})
        other_org, _ = Organization.objects.get_or_create(
            code="demo-fondation", defaults={"name": "Fondation Démo (fictive, isolée)", "kind": "foundation"})

        ci, _ = Territory.objects.get_or_create(code="CI", defaults={"name": "Côte d'Ivoire", "level": "country",
                                                                     "reliability": "official"})
        abj, _ = Territory.objects.get_or_create(code="CI-AB", defaults={"name": "District d'Abidjan",
                                                                         "level": "district", "parent": ci})
        zanzan, _ = Territory.objects.get_or_create(code="CI-ZZ", defaults={"name": "District du Zanzan",
                                                                            "level": "district", "parent": ci})
        communes = []
        for code, name, parent in [("CI-AB-ABO", "Abobo", abj), ("CI-AB-YOP", "Yopougon", abj),
                                   ("CI-AB-KOU", "Koumassi", abj), ("CI-ZZ-BDK", "Bondoukou", zanzan),
                                   ("CI-ZZ-DRP", "Doropo", zanzan)]:
            t, _ = Territory.objects.get_or_create(code=code, defaults={"name": name, "level": "commune",
                                                                        "parent": parent})
            communes.append(t)

        users = {}
        for username, first, last, role in DEMO_USERS:
            u, created = User.objects.get_or_create(username=username,
                                                    defaults={"first_name": first, "last_name": last})
            if created:
                u.set_password(password)
                u.save()
            Membership.objects.get_or_create(user=u, organization=org, role=role, program=None)
            users[role] = u
        tech, created = User.objects.get_or_create(username="admin.tech", defaults={
            "first_name": "Admin", "last_name": "Technique", "is_staff": True, "is_superuser": True})
        if created:
            tech.set_password(password)
            tech.save()
        iso, created = User.objects.get_or_create(username="autre.org", defaults={"first_name": "Agent",
                                                                                  "last_name": "Fondation"})
        if created:
            iso.set_password(password)
            iso.save()
        Membership.objects.get_or_create(user=iso, organization=other_org, role=Role.SOCIAL_WORKER, program=None)

        qv, _ = QuestionnaireVersion.objects.get_or_create(
            code=QUESTIONNAIRE_V2["code"], version=QUESTIONNAIRE_V2["version"],
            defaults={"title": QUESTIONNAIRE_V2["title"], "definition": QUESTIONNAIRE_V2, "status": "published",
                      "published_at": timezone.now()})
        active_scoring_version()

        prog, _ = Program.objects.get_or_create(organization=org, code="filets-sociaux-abidjan", defaults={
            "name": "Filets sociaux Abidjan (démo)", "sector": "safety_net", "status": "active",
            "description": "Programme fictif d'appui aux ménages vulnérables d'Abobo et Yopougon.",
            "budget_amount": 150_000_000, "target_beneficiaries": 500, "funders": "Bailleur fictif",
            "eligibility_summary": "Ménages résidant à Abobo ou Yopougon, en situation de vulnérabilité constatée "
                                   "par un travailleur social.",
            "start_date": date(2026, 9, 1), "end_date": date(2027, 8, 31)})
        prog.territories.set(communes[:2])
        if not prog.rule_versions.exists():
            EligibilityRuleVersion.objects.create(
                program=prog, version=1, effective_from=date(2026, 9, 1),
                rules={"territories": ["CI-AB-ABO", "CI-AB-YOP"]},
                description="Résidence à Abobo ou Yopougon ; avis social favorable ; décision du comité.",
                created_by=users[Role.PROGRAM_MANAGER])
        prog2, _ = Program.objects.get_or_create(organization=org, code="relance-agricole-zanzan", defaults={
            "name": "Relance agricole Zanzan (démo)", "sector": "agriculture", "status": "draft",
            "description": "Scénario de conception — aucun partenariat présumé.", "funders": "À définir"})
        prog2.territories.set(communes[3:])

        if Person.objects.filter(organization=org).count() < 30:
            for i in range(30):
                commune = rng.choice(communes)
                hh = Household.objects.create(organization=org, territory=commune,
                                              address_description=f"Secteur {rng.randint(1, 12)} (fictif)",
                                              created_by=users[Role.ENUMERATOR])
                female = rng.random() < 0.6
                p = Person.objects.create(
                    organization=org, territory=commune, sex="F" if female else "M",
                    first_name=rng.choice(FIRST_F if female else FIRST_M), last_name=rng.choice(LAST),
                    estimated_age=rng.randint(18, 70), has_identity_document=rng.random() < 0.6,
                    phone=f"07{rng.randint(10_000_000, 99_999_999)}" if rng.random() < 0.7 else "",
                    status=rng.choice(["submitted", "verified", "active", "active"]),
                    created_by=users[Role.ENUMERATOR])
                HouseholdMembership.objects.create(household=hh, person=p, relation="head",
                                                   start_date=date.today() - timedelta(days=rng.randint(10, 300)))
                answers = {k: rng.choice(v) for k, v in CHOICES.items() if rng.random() < 0.92}
                answers["household_size"] = rng.randint(1, 10)
                answers["dependents"] = rng.randint(0, 6)
                answers["days_without_food"] = rng.randint(0, 5)
                answers["shock_recent"] = "yes" if answers.get("displacement_status") in {"evicted", "displaced"} \
                    else "no"
                if answers["shock_recent"] == "yes":
                    answers["shock_type"] = "eviction" if answers["displacement_status"] == "evicted" else "flood"
                    answers["lost_housing"] = "yes"
                resp = SurveyResponse.objects.create(organization=org, person=p, household=hh, questionnaire=qv,
                                                     answers=answers, collected_at=timezone.now(),
                                                     collected_by=users[Role.ENUMERATOR], client_uuid=uuid.uuid4())
                score = score_survey_response(resp, users[Role.SOCIAL_WORKER])
                if i < 18 and commune in communes[:2]:
                    Application.objects.create(organization=org, program=prog, person=p, score=score,
                                               rule_version=prog.rule_versions.first(),
                                               submitted_by=users[Role.SOCIAL_WORKER],
                                               status=rng.choice(["submitted", "admissible", "under_review",
                                                                  "committee"]))
                detect_duplicates(p)
                flag_from_survey(resp, users[Role.ENUMERATOR])

        self._seed_operations(rng, org, communes, users)

        self.stdout.write(self.style.SUCCESS(
            f"Démo prête : {Person.objects.filter(organization=org).count()} personnes synthétiques, "
            f"{Program.objects.filter(organization=org).count()} programmes, {len(DEMO_USERS)} comptes."))

    def _seed_operations(self, rng, org, communes, users):
        """Structures, opérations et aides fictives (aucune référence à une opération réelle)."""
        from apps.assistance.models import Assistance
        from apps.operations.models import Operation, Partner
        from apps.operations.services import enroll

        if Operation.objects.filter(organization=org).exists():
            return
        sw, sup = users[Role.SOCIAL_WORKER], users[Role.FIELD_SUPERVISOR]
        gov, _ = Partner.objects.get_or_create(organization=org, name="Ministère fictif de la Solidarité",
                                               defaults={"kind": "government", "acronym": "MFS (démo)"})
        mairie, _ = Partner.objects.get_or_create(organization=org, name="Mairie fictive de Yopougon",
                                                  defaults={"kind": "local_authority"})
        mine, _ = Partner.objects.get_or_create(organization=org, name="Société minière fictive du Zanzan",
                                                defaults={"kind": "mining_company", "acronym": "SMZ (démo)"})
        ong, _ = Partner.objects.get_or_create(organization=org, name="ONG partenaire fictive",
                                               defaults={"kind": "ngo"})
        ops = [
            Operation.objects.create(
                organization=org, title="Libération d'emprise — quartier fictif « Bord-Lagune »", kind="eviction",
                status="response", initiated_by=mairie, territory=communes[1], sites="Bord-Lagune (fictif)",
                legal_reference="Arrêté municipal fictif n° 000/2026", notice_date=date(2026, 9, 1),
                start_date=date(2026, 9, 15), estimated_households=120,
                compensation_policy="Aide au relogement annoncée : 250 000 FCFA par ménage (scénario fictif).",
                compensation_amount_per_household=250000, created_by=users[Role.PROGRAM_MANAGER]),
            Operation.objects.create(
                organization=org, title="Inondations de saison des pluies — Abobo (démo)", kind="flood",
                status="ongoing", initiated_by=gov, territory=communes[0], sites="Bas-fonds (fictif)",
                start_date=date(2026, 10, 2), estimated_households=60, created_by=users[Role.PROGRAM_MANAGER]),
            Operation.objects.create(
                organization=org, title="Site minier fictif — ménages ayant perdu des terres", kind="mining_project",
                status="ongoing", initiated_by=mine, territory=communes[4], sites="Villages A et B (fictifs)",
                legal_reference="Plan de réinstallation fictif", start_date=date(2026, 6, 1),
                estimated_households=40, created_by=users[Role.PROGRAM_MANAGER]),
        ]
        ops[0].partners.set([gov, ong])
        ops[1].partners.set([ong])
        ops[2].partners.set([gov, ong])
        persons = list(Person.objects.filter(organization=org).order_by("created_at"))
        for idx, person in enumerate(persons[:18]):
            op = ops[idx % 3]
            case, _ = enroll(op, person, user=sup, lost_housing=op.kind != "mining_project",
                             lost_livelihood=rng.random() < 0.5, lost_documents=rng.random() < 0.3,
                             occupancy=rng.choice(["tenant", "owner_untitled", "hosted", "trader"]),
                             needs=rng.sample(["shelter", "food", "documents", "schooling", "livelihood",
                                               "compensation", "health"], 3),
                             current_location=rng.choice(["Chez un parent", "Site d'accueil (fictif)", "Inconnue"]))
            if idx % 2 == 0:
                category = "relocation" if op.kind == "eviction" else ("food" if op.kind == "flood" else "livelihood")
                a = Assistance.objects.create(
                    organization=org, person=person, operation_case=case, provider=gov if idx % 4 == 0 else ong,
                    category=category, amount=250000 if category == "relocation" else 35000,
                    description="Aide fictive de démonstration", recorded_by=sw)
                if idx % 4 == 0:
                    a.status, a.delivered_at, a.delivered_by = "delivered", timezone.now(), sw
                    a.delivery_proof, a.delivery_channel = f"REÇU-DEMO-{idx:03d}", "mobile_money"
                    a.save()
                    case.status = "supported"
                    case.save(update_fields=["status"])
