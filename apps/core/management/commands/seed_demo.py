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
from apps.cases.models import Application
from apps.organizations.models import Organization
from apps.programs.models import EligibilityRuleVersion, Program
from apps.scoring.services import active_scoring_version, score_survey_response
from apps.surveys.definitions import QUESTIONNAIRE_V1
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
    "housing_type": ["precarious", "rented", "shared", "owned"],
    "water_access": ["none", "shared", "private"],
    "electricity": ["none", "informal", "yes"],
    "literacy": ["no", "partial", "yes"],
    "children_out_of_school": ["yes", "no"],
    "activity_status": ["none", "informal", "stable"],
    "monthly_income": ["none", "lt25k", "25to60k", "gt60k"],
    "chronic_illness": ["yes", "no"],
    "disability": ["none", "none", "moderate", "severe"],
    "health_access": ["no", "difficult", "easy"],
    "birth_certificate": ["yes", "no"],
    "protection_risk": ["no", "no", "yes"],
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
            code=QUESTIONNAIRE_V1["code"], version=QUESTIONNAIRE_V1["version"],
            defaults={"title": QUESTIONNAIRE_V1["title"], "definition": QUESTIONNAIRE_V1, "status": "published",
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

        self.stdout.write(self.style.SUCCESS(
            f"Démo prête : {Person.objects.filter(organization=org).count()} personnes synthétiques, "
            f"{Program.objects.filter(organization=org).count()} programmes, {len(DEMO_USERS)} comptes."))
