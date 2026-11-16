"""
Enrichissement du jeu de démonstration : ~180 ménages SYNTHÉTIQUES répartis dans 10 communes de Côte d'Ivoire,
avec des profils cohérents, un historique sur 18 mois, des aides, orientations, visites, plaintes et décisions.

Aucune donnée réelle : noms tirés au hasard, numéros fictifs, coordonnées approximatives du centre de la commune.
Appelé par `python manage.py seed_demo` (idempotent : ne s'exécute qu'une fois par organisation).
"""
import uuid
from datetime import date, datetime, time, timedelta

from django.utils import timezone

from apps.accounts.models import Membership, User
from apps.accounts.roles import Role
from apps.assistance.models import Assistance
from apps.beneficiaries.models import Household, HouseholdMembership, Person
from apps.beneficiaries.vulnerability_services import flag_from_survey
from apps.cases.models import Application, Decision
from apps.cases.services import record_decision, transition_application
from apps.followup.models import Grievance, Referral, Visit
from apps.poverty.index import assess
from apps.programs.models import EligibilityRuleVersion, Program
from apps.scoring.services import score_survey_response
from apps.surveys.models import SurveyResponse
from apps.territories.models import Territory

MARKER = "(démo+)"

FIRST_F = ["Aya", "Adjoua", "Mariam", "Fatoumata", "Akissi", "Affoué", "Salimata", "Rokia", "Amenan", "Awa",
           "Mawa", "Kadidja", "Nafissatou", "Ahou", "Amoin", "Tenin", "Massandjé", "Djénéba", "Bintou", "Ama",
           "Yvette", "Christelle", "Edwige", "Clémentine", "Sita", "Korotoum"]
FIRST_M = ["Koffi", "Kouamé", "Moussa", "Yao", "Seydou", "Brahima", "Konan", "Drissa", "Adama", "Lassina",
           "Siaka", "Zié", "Gnamien", "N'Golo", "Bakary", "Souleymane", "Kouadio", "Ange", "Serge", "Hervé",
           "Gbato", "Dosso", "Issouf", "Tiémoko", "Mamadou", "Fulgence"]
LAST = ["Kouassi", "Koné", "Traoré", "Ouattara", "Yao", "Bamba", "Coulibaly", "Diarra", "N'Dri", "Touré",
        "Soro", "Silué", "Yéo", "Tuo", "Kouakou", "Gbagbo", "Zadi", "Guéi", "Dosso", "Cissé", "Fofana",
        "Konaté", "Sanogo", "Diabaté", "Kra", "Aké", "Brou", "Gnahoré", "Tapé", "Sery"]

# Communes : (code, nom, district (code, nom), lat, lon, milieu, nombre de ménages, profil de pauvreté 0..1)
COMMUNES = [
    ("CI-AB-ABO", "Abobo", ("CI-AB", "District d'Abidjan"), 5.4189, -4.0204, "urbain", 26, 0.55),
    ("CI-AB-YOP", "Yopougon", ("CI-AB", "District d'Abidjan"), 5.3364, -4.0756, "urbain", 22, 0.45),
    ("CI-AB-KOU", "Koumassi", ("CI-AB", "District d'Abidjan"), 5.2970, -3.9500, "urbain", 12, 0.45),
    ("CI-VB-BKE", "Bouaké", ("CI-VB", "District de la Vallée du Bandama"), 7.6906, -5.0300, "urbain", 18, 0.55),
    ("CI-SV-KHG", "Korhogo", ("CI-SV", "District des Savanes"), 9.4580, -5.6296, "rural", 22, 0.75),
    ("CI-ZZ-BDK", "Bondoukou", ("CI-ZZ", "District du Zanzan"), 8.0402, -2.8000, "rural", 16, 0.7),
    ("CI-ZZ-DRP", "Doropo", ("CI-ZZ", "District du Zanzan"), 9.8106, -3.3472, "rural", 14, 0.85),
    ("CI-MN-MAN", "Man", ("CI-MN", "District des Montagnes"), 7.4125, -7.5538, "rural", 18, 0.7),
    ("CI-BS-SPD", "San-Pédro", ("CI-BS", "District du Bas-Sassandra"), 4.7485, -6.6363, "urbain", 16, 0.5),
    ("CI-SM-DAL", "Daloa", ("CI-SM", "District du Sassandra-Marahoué"), 6.8774, -6.4502, "rural", 16, 0.6),
]

EXTRA_STAFF = [
    ("recenseur.korhogo", "Siaka", "Soro", Role.ENUMERATOR),
    ("recenseur.man", "Edwige", "Gueï", Role.ENUMERATOR),
    ("recenseur.bouake", "Kouadio", "Brou", Role.ENUMERATOR),
    ("ts.zanzan", "Mawa", "Ouattara", Role.SOCIAL_WORKER),
]

# Niveaux de privation : chaque profil oriente les réponses sans les figer.
LEVELS = {
    "extreme": 0.85, "poor": 0.62, "vulnerable": 0.4, "near": 0.18,
}


def _at(day: date, hour: int = 10) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(hour, 0)))


def _backdate(obj, when: datetime):
    type(obj).objects.filter(pk=obj.pk).update(created_at=when)


class Enricher:
    def __init__(self, rng, org, qv, users, log):
        self.rng, self.org, self.qv, self.users, self.log = rng, org, qv, users, log
        self.today = timezone.localdate()

    # ------------------------------------------------------------------ réponses
    def answers(self, level: float, milieu: str) -> dict:
        r = self.rng

        def bad(p_shift=0.0):
            return r.random() < min(0.97, max(0.02, level + p_shift))

        rural = milieu == "rural"
        size = r.randint(2, 6) + (r.randint(0, 4) if level > 0.5 else 0)
        a = {
            "household_size": size,
            "dependents": max(0, size - r.randint(1, 3)),
            "monthly_income": r.choice(["none", "lt25k"]) if bad() else r.choice(
                ["25to60k", "60to120k", "gt120k"] if level < 0.3 else ["25to60k", "60to120k"]),
            "activity_status": "none" if bad(-0.25) else ("informal" if bad(0.2) else "stable"),
            "income_source": r.choice(["agriculture", "agriculture", "craft", "trade"] if rural
                                      else ["trade", "trade", "craft", "none"]),
            "meals_per_day": "one" if bad(-0.3) else ("two" if bad(0.15) else "three"),
            "days_without_food": r.randint(2, 6) if bad(-0.35) else r.randint(0, 1),
            "coping_strategy": r.choice(["reduced", "skipped", "sold_assets"]) if bad(-0.2) else "none",
            "housing_type": ("precarious" if bad(-0.1) else r.choice(["rented", "shared"])) if not rural
            else ("precarious" if bad(-0.2) else "owned"),
            "tenure": r.choice(["squat", "untitled"]) if bad() else r.choice(["tenant", "titled"]),
            "water_access": "none" if bad(-0.2 if rural else -0.35) else ("shared" if bad(0.2) else "private"),
            "sanitation": "none" if bad(0.0 if rural else -0.3) else ("shared" if bad(0.2) else "private"),
            "electricity": "none" if bad(0.05 if rural else -0.4) else ("informal" if bad(0.0) else "yes"),
            "literacy": "no" if bad(0.05) else ("partial" if bad(0.15) else "yes"),
            "children_out_of_school": "yes" if bad(-0.3) else "no",
            "child_labour": "yes" if bad(-0.55) else "no",
            "health_coverage": "no" if bad(0.15) else "yes",
            "health_access": "no" if bad(-0.4) else ("difficult" if bad(0.1) else "easy"),
            "child_malnutrition": "yes" if bad(-0.55) else "no",
            "birth_certificate": "no" if bad(-0.35) else "yes",
            "id_document": "no" if bad(-0.2) else "yes",
            "chronic_illness": "yes" if r.random() < 0.18 else "no",
            "disability": r.choice(["moderate", "severe"]) if r.random() < 0.08 else "none",
            "protection_risk": "yes" if r.random() < 0.06 else "no",
            "support_network": "none" if bad(-0.2) else ("weak" if bad(0.1) else "strong"),
            "lives_alone": "yes" if r.random() < 0.05 else "no",
            "displacement_status": "resident",
            "lost_livelihood": "no", "lost_documents": "no",
            "mining_zone": "yes" if rural and r.random() < 0.12 else "no",
            "self_assessment": ("very_difficult" if bad(-0.1) else "difficult") if level > 0.45
            else r.choice(["difficult", "manageable", "good"]),
            "shock_recent": "no",
        }
        if r.random() < 0.08 + 0.08 * level:
            a["displacement_status"] = r.choice(["evicted", "displaced"])
            a["shock_recent"], a["lost_housing"] = "yes", "yes"
            a["shock_type"] = "eviction" if a["displacement_status"] == "evicted" else "flood"
            a["lost_livelihood"] = r.choice(["yes", "no"])
        # Les ménages non pauvres sur les autres dimensions ont en général des revenus au-dessus du seuil.
        if not assess(a).poor and r.random() < 0.8:
            a["monthly_income"] = r.choice(["60to120k", "gt120k", "gt120k"])
            a["household_size"] = min(a["household_size"], r.randint(2, 5))
            a["dependents"] = min(a["dependents"], a["household_size"] - 1)
        # Quelques questions non posées (formulaire express) : réaliste, sans fausser l'indice.
        for k in r.sample(["tenure", "child_labour", "support_network", "self_assessment", "id_document"], 2):
            a.pop(k, None)
        return a

    def improve(self, before: dict, strength: float) -> dict:
        """Réévaluation : la situation s'améliore d'autant plus que l'accompagnement a été fort."""
        r, a = self.rng, dict(before)
        upgrades = {
            "monthly_income": {"none": "lt25k", "lt25k": "25to60k", "25to60k": "60to120k"},
            "meals_per_day": {"one": "two", "two": "three"},
            "activity_status": {"none": "informal", "informal": "stable"},
            "children_out_of_school": {"yes": "no"},
            "health_coverage": {"no": "yes"},
            "birth_certificate": {"no": "yes"},
            "coping_strategy": {"reduced": "none", "skipped": "none", "sold_assets": "reduced"},
            "child_malnutrition": {"yes": "no"},
            "electricity": {"none": "informal"},
            "self_assessment": {"very_difficult": "difficult", "difficult": "manageable"},
        }
        for k, m in upgrades.items():
            if a.get(k) in m and r.random() < strength:
                a[k] = m[a[k]]
        if "days_without_food" in a and a.get("meals_per_day") != "one":
            a["days_without_food"] = min(a["days_without_food"], 1)
        # Chocs : une partie des ménages se dégrade malgré tout.
        if r.random() < 0.12:
            a["meals_per_day"], a["monthly_income"] = "one", "none"
            a["self_assessment"] = "very_difficult"
        return a

    # ------------------------------------------------------------------ structure
    def setup(self):
        ci = Territory.objects.get(code="CI")
        self.communes = []
        for code, name, (dcode, dname), lat, lon, milieu, n, level in COMMUNES:
            district, _ = Territory.objects.get_or_create(code=dcode, defaults={"name": dname, "level": "district",
                                                                               "parent": ci})
            t, _ = Territory.objects.get_or_create(code=code, defaults={"name": name, "level": "commune",
                                                                        "parent": district})
            if t.latitude is None:
                Territory.objects.filter(pk=t.pk).update(latitude=lat, longitude=lon)
            self.communes.append((t, lat, lon, milieu, n, level))

        password = self.users["_password"]
        self.enumerators = [self.users[Role.ENUMERATOR]]
        self.social_workers = [self.users[Role.SOCIAL_WORKER]]
        for username, first, last, role in EXTRA_STAFF:
            u, created = User.objects.get_or_create(username=username,
                                                    defaults={"first_name": first, "last_name": last})
            if created:
                if password:
                    u.set_password(password)
                else:
                    u.set_unusable_password()  # compte fictif : auteur des données, ne peut pas se connecter
                u.save()
            Membership.objects.get_or_create(user=u, organization=self.org, role=role, program=None)
            (self.enumerators if role == Role.ENUMERATOR else self.social_workers).append(u)

        pm = self.users[Role.PROGRAM_MANAGER]
        by = {t.code: t for t, *_ in self.communes}
        self.cash, _ = Program.objects.get_or_create(organization=self.org, code="transferts-nord-demo", defaults={
            "name": "Transferts monétaires Savanes-Zanzan (démo)", "sector": "safety_net", "status": "active",
            "description": "Programme fictif : transferts trimestriels de 36 000 FCFA aux ménages en pauvreté "
                           "sévère, avec mesures d'accompagnement (CMU, état civil, scolarisation).",
            "budget_amount": 420_000_000, "target_beneficiaries": 1200, "funders": "Bailleur fictif",
            "eligibility_summary": "Ménages des communes de Korhogo, Bondoukou et Doropo en pauvreté sévère.",
            "start_date": self.today - timedelta(days=420), "end_date": self.today + timedelta(days=680)})
        self.cash.territories.set([by["CI-SV-KHG"], by["CI-ZZ-BDK"], by["CI-ZZ-DRP"]])
        self.agr, _ = Program.objects.get_or_create(organization=self.org, code="agr-jeunes-demo", defaults={
            "name": "Activités génératrices de revenus — jeunes et femmes (démo)", "sector": "training",
            "status": "active",
            "description": "Programme fictif : formation, kit de démarrage (150 000 FCFA) et mentorat pendant 6 mois.",
            "budget_amount": 180_000_000, "target_beneficiaries": 600, "funders": "Fonds fictif d'insertion",
            "eligibility_summary": "Ménages pauvres sans activité stable à Bouaké, Man, Daloa et San-Pédro.",
            "start_date": self.today - timedelta(days=300), "end_date": self.today + timedelta(days=430)})
        self.agr.territories.set([by["CI-VB-BKE"], by["CI-MN-MAN"], by["CI-SM-DAL"], by["CI-BS-SPD"]])
        for prog, codes in ((self.cash, ["CI-SV-KHG", "CI-ZZ-BDK", "CI-ZZ-DRP"]),
                            (self.agr, ["CI-VB-BKE", "CI-MN-MAN", "CI-SM-DAL", "CI-BS-SPD"])):
            if not prog.rule_versions.exists():
                EligibilityRuleVersion.objects.create(
                    program=prog, version=1, effective_from=prog.start_date, rules={"territories": codes},
                    description=prog.eligibility_summary + " Avis social et décision du comité.", created_by=pm)
        self.programs_by_commune = {c: self.cash for c in ["CI-SV-KHG", "CI-ZZ-BDK", "CI-ZZ-DRP"]}
        self.programs_by_commune.update({c: self.agr for c in ["CI-VB-BKE", "CI-MN-MAN", "CI-SM-DAL", "CI-BS-SPD"]})
        abidjan = Program.objects.filter(organization=self.org, code="filets-sociaux-abidjan").first()
        if abidjan:
            self.programs_by_commune.update({"CI-AB-ABO": abidjan, "CI-AB-YOP": abidjan})

    # ------------------------------------------------------------------ ménages
    def person(self, commune, *, female, age, created_by, status="active"):
        r = self.rng
        birth = self.today - timedelta(days=int(age * 365.25) + r.randint(0, 364)) if r.random() < 0.6 else None
        return Person.objects.create(
            organization=self.org, territory=commune, sex="F" if female else "M",
            first_name=r.choice(FIRST_F if female else FIRST_M), last_name=r.choice(LAST),
            birth_date=birth, estimated_age=None if birth else age,
            has_identity_document=age >= 18 and r.random() < 0.55,
            phone=f"07{r.randint(10_000_000, 99_999_999)}" if age >= 18 and r.random() < 0.65 else "",
            status=status, created_by=created_by)

    def household(self, commune, lat, lon, milieu, level):
        r = self.rng
        enum = r.choice(self.enumerators)
        baseline_day = self.today - timedelta(days=r.randint(20, 540))
        hh = Household.objects.create(
            organization=self.org, territory=commune, created_by=enum,
            address_description=f"{'Village' if milieu == 'rural' else 'Quartier'} {r.randint(1, 14)} {MARKER}",
            latitude=round(lat + r.uniform(-0.03, 0.03), 6), longitude=round(lon + r.uniform(-0.03, 0.03), 6),
            gps_consent=True)
        female_head = r.random() < (0.38 + 0.15 * level)
        head = self.person(commune, female=female_head, age=r.randint(22, 68), created_by=enum,
                           status=r.choice(["verified", "active", "active", "active", "submitted"]))
        HouseholdMembership.objects.create(household=hh, person=head, relation="head", start_date=baseline_day)
        answers = self.answers(level, milieu)
        size = answers["household_size"]
        members = [head]
        if not female_head or r.random() < 0.4:
            spouse = self.person(commune, female=not female_head, age=r.randint(20, 60), created_by=enum)
            HouseholdMembership.objects.create(household=hh, person=spouse, relation="spouse",
                                               start_date=baseline_day)
            members.append(spouse)
        for _ in range(min(size - len(members), 5)):
            child = self.person(commune, female=r.random() < 0.5, age=r.randint(0, 16), created_by=enum)
            HouseholdMembership.objects.create(household=hh, person=child, relation="child",
                                               start_date=baseline_day)
            members.append(child)
        for p in members:
            _backdate(p, _at(baseline_day, 9))
        _backdate(hh, _at(baseline_day, 9))

        resp = SurveyResponse.objects.create(
            organization=self.org, person=head, household=hh, questionnaire=self.qv, answers=answers,
            collected_at=_at(baseline_day), collected_by=enum, client_uuid=uuid.uuid4())
        _backdate(resp, _at(baseline_day))
        return hh, head, resp, baseline_day

    # ------------------------------------------------------------------ accompagnement
    def support(self, hh, head, answers, baseline_day, commune_code):
        """Aides, orientations et visites cohérentes avec les privations constatées. Retourne l'intensité."""
        r = self.rng
        sw = r.choice(self.social_workers)
        result = assess(answers)
        strength = 0.15
        if not result.poor and r.random() < 0.7:
            return strength, None
        program = self.programs_by_commune.get(commune_code)
        # 25 % des ménages pauvres restent sans aucune aide : c'est l'alerte « pauvres sans aide ».
        if r.random() < 0.25:
            return strength, program
        start = baseline_day + timedelta(days=r.randint(10, 45))

        def give(category, amount, when, *, channel="hand", proof=True, desc="", status="delivered", qty=None,
                 unit=""):
            if when > self.today:
                status = "planned"
            a = Assistance.objects.create(
                organization=self.org, person=head, household=hh,
                program=program if program and program.start_date and when >= program.start_date else None,
                category=category,
                amount=amount, quantity=qty, unit=unit, description=desc or "Aide fictive de démonstration",
                recorded_by=sw, status=status, planned_date=when,
                delivered_at=_at(when, 11) if status == "delivered" else None,
                delivered_by=sw if status == "delivered" else None, delivery_channel=channel,
                delivery_proof=(f"MM-DEMO-{r.randint(100000, 999999)}" if channel == "mobile_money"
                                else f"REÇU-DEMO-{r.randint(1000, 9999)}") if status == "delivered" and proof else "")
            _backdate(a, _at(min(when, self.today) - timedelta(days=3)))
            return a

        if program == self.cash or (program is None and r.random() < 0.4) or result.severe:
            for q in range(r.randint(1, 5)):
                give("cash", 36000, start + timedelta(days=91 * q), channel="mobile_money",
                     desc=f"Transfert trimestriel n° {q + 1} (fictif)")
                strength += 0.1
        if result.deprived("food_insecurity") and r.random() < 0.7:
            give("food", 25000, start + timedelta(days=r.randint(0, 20)), channel="voucher", qty=1, unit="kit",
                 desc="Kit vivres (riz, huile, haricot) — fictif")
            strength += 0.05
        if result.deprived("malnutrition"):
            give("nutrition", None, start + timedelta(days=5), channel="service", desc="Prise en charge "
                 "nutritionnelle ambulatoire (fictive)")
        if result.deprived("schooling") and r.random() < 0.6:
            give("education", 20000, start + timedelta(days=r.randint(15, 60)), desc="Kit scolaire et frais "
                 "d'inscription (fictif)")
            strength += 0.1
        if result.deprived("health_coverage") and r.random() < 0.55:
            give("health_coverage", 12000, start + timedelta(days=r.randint(5, 40)), channel="service",
                 desc="Cotisation CMU prise en charge pour 12 mois (fictif)")
            strength += 0.1
        if program == self.agr and result.deprived("no_activity") and r.random() < 0.7:
            give("training", None, start + timedelta(days=20), channel="service", desc="Formation en gestion "
                 "d'activité (fictive)")
            give("livelihood", 150000, start + timedelta(days=60), channel="mobile_money",
                 desc="Kit de démarrage d'activité (fictif)")
            strength += 0.25
        if r.random() < 0.05:
            a = give("cash", 36000, start + timedelta(days=200), status="planned")
            Assistance.objects.filter(pk=a.pk).update(status="cancelled", cancel_reason="Ménage introuvable à "
                                                      "l'adresse lors de la distribution (fictif).")

        # Orientations vers les services de base.
        routes = [("birth_certificate", "civil_registry", "Établissement d'un jugement supplétif d'acte de naissance"),
                  ("health_coverage", "cmu", "Enrôlement à la CMU"),
                  ("schooling", "school", "Réinscription des enfants à l'école"),
                  ("malnutrition", "nutrition", "Dépistage et suivi nutritionnel"),
                  ("no_activity", "employment", "Inscription à une agence d'emploi (fictive)")]
        for code, service, reason in routes:
            dep_code = "civil_status" if code == "birth_certificate" else code
            if result.deprived(dep_code) and r.random() < 0.65:
                when = start + timedelta(days=r.randint(0, 50))
                if when > self.today:
                    continue
                age = (self.today - when).days
                status = r.choices(["completed", "accepted", "sent", "no_show", "refused"],
                                   weights=[5 if age > 45 else 1, 3, 2 if age < 60 else 1, 1, 0.5])[0]
                ref = Referral.objects.create(
                    organization=self.org, person=head, service=service, service_name="Service public (fictif)",
                    reason=reason, urgent=code == "malnutrition", referred_by=sw, status=status,
                    appointment_date=when + timedelta(days=r.randint(3, 20)),
                    feedback="Service rendu, document remis (fictif)." if status == "completed" else "",
                    closed_at=_at(when + timedelta(days=r.randint(10, 40))) if status == "completed" else None)
                _backdate(ref, _at(when))
                if status == "completed":
                    strength += 0.1

        # Visites de suivi.
        day = start
        trend_pool = ["stable", "improved", "stable", "worsened"]
        for _ in range(r.randint(1, 4)):
            day = day + timedelta(days=r.randint(30, 100))
            if day > self.today:
                break
            trend = r.choice(trend_pool + (["improved", "improved"] if strength > 0.4 else ["critical"]))
            v = Visit.objects.create(
                organization=self.org, person=head, household=hh, kind=r.choice(["home", "home", "phone", "group"]),
                visited_at=day, trend=trend, visited_by=sw,
                summary={"improved": "Revenus plus réguliers, enfants scolarisés (fictif).",
                         "stable": "Situation inchangée, suivi à poursuivre (fictif).",
                         "worsened": "Perte d'activité, repas réduits (fictif).",
                         "critical": "Ménage en grande difficulté, aide d'urgence demandée (fictif)."}[trend],
                next_visit_date=day + timedelta(days=r.randint(30, 90)))
            _backdate(v, _at(day))
        return min(strength, 0.85), program

    def reassess(self, hh, head, before, baseline_day, strength):
        r = self.rng
        day = baseline_day + timedelta(days=r.randint(150, 330))
        if day >= self.today:
            return None
        after = self.improve(before, strength)
        resp = SurveyResponse.objects.create(
            organization=self.org, person=head, household=hh, questionnaire=self.qv, answers=after,
            collected_at=_at(day), collected_by=r.choice(self.enumerators), client_uuid=uuid.uuid4())
        _backdate(resp, _at(day))
        return resp

    def apply(self, head, resp, program, baseline_day):
        """Candidature et, pour une partie, décision motivée du comité."""
        r = self.rng
        if program is None or r.random() < 0.35:
            return
        sw = r.choice(self.social_workers)
        score = score_survey_response(resp, sw)
        app = Application.objects.create(organization=self.org, program=program, person=head, score=score,
                                         rule_version=program.rule_versions.first(), submitted_by=sw,
                                         status="submitted",
                                         social_opinion="Avis social fictif : situation vérifiée lors de la visite.")
        _backdate(app, _at(max(baseline_day + timedelta(days=7), program.start_date or baseline_day)))
        stage = r.random()
        try:
            if stage < 0.2:
                return
            transition_application(app, "admissible", sw, "Pièces vérifiées (fictif)")
            if stage < 0.35:
                return
            transition_application(app, "under_review", sw)
            if stage < 0.45:
                return
            transition_application(app, "committee", sw)
            if stage < 0.55:
                return
            members = [{"username": u.username, "name": u.get_full_name()}
                       for u in User.objects.filter(memberships__role=Role.COMMITTEE_MEMBER,
                                                    memberships__organization=self.org).distinct()]
            assessment = assess(resp.answers)
            outcome = (Decision.Outcome.APPROVED if assessment.poor and r.random() < 0.85
                       else r.choice([Decision.Outcome.REJECTED, Decision.Outcome.DEFERRED]))
            motive = {
                Decision.Outcome.APPROVED: "Ménage en situation de pauvreté constatée, critères du programme remplis.",
                Decision.Outcome.REJECTED: "Revenus et conditions de vie au-dessus des critères du programme.",
                Decision.Outcome.DEFERRED: "Informations à compléter : visite de vérification demandée.",
            }[outcome]
            record_decision(app, self.users[Role.COMMITTEE_MEMBER], outcome=outcome, motive=motive,
                            committee_members=members)
        except Exception as exc:  # une transition refusée n'empêche pas le reste de la démo
            self.log(f"  candidature ignorée : {exc}")

    # ------------------------------------------------------------------ plaintes
    def grievances(self, heads):
        r = self.rng
        sw = self.social_workers
        samples = [
            ("payment_delay", "high", "phone", "Transfert du trimestre non reçu"),
            ("payment_amount", "normal", "in_person", "Montant reçu inférieur au montant annoncé"),
            ("exclusion", "normal", "community", "Ménage du quartier non recensé"),
            ("data_correction", "normal", "field_agent", "Date de naissance erronée"),
            ("information", "normal", "sms", "Date de la prochaine distribution"),
            ("service_quality", "normal", "suggestion_box", "Attente trop longue au point de paiement"),
            ("staff_conduct", "high", "phone", "Agent impoli lors de la visite"),
            ("fraud", "high", "phone", "Demande d'argent pour être inscrit sur la liste"),
            ("inclusion_error", "normal", "community", "Ménage aisé inscrit sur la liste"),
            ("relocation", "urgent", "in_person", "Site d'accueil sans latrines"),
            ("other", "normal", "app", "Changement de numéro de téléphone"),
        ]
        statuses = ["resolved", "resolved", "closed", "investigating", "acknowledged", "received", "escalated",
                    "rejected"]
        for i in range(40):
            cat, prio, channel, subject = samples[i % len(samples)]
            person = None if cat in {"fraud", "exclusion"} else r.choice(heads)
            day = self.today - timedelta(days=r.randint(1, 330))
            status = r.choice(statuses)
            g = Grievance(
                organization=self.org, person=person, anonymous=cat == "fraud",
                territory=person.territory if person else r.choice(self.communes)[0],
                channel=channel,
                category=cat, priority=prio, subject=subject, description="Plainte fictive de démonstration.",
                recorded_by=r.choice(sw), status=status,
                due_date=day + timedelta(days=Grievance.SLA_DAYS[prio]))
            if status in {"resolved", "closed"}:
                late = r.random() < 0.2
                g.resolved_at = _at(min(self.today, day + timedelta(
                    days=Grievance.SLA_DAYS[prio] + (r.randint(2, 15) if late else -r.randint(0, 2)))))
                g.resolution = "Réponse fictive apportée au plaignant."
                g.satisfaction = r.choices(["satisfied", "partly", "unsatisfied", ""], weights=[5, 2, 1, 2])[0]
            if status == "rejected":
                g.resolution = "Hors du champ du programme : orientation vers la mairie (fictif)."
            g.save()
            _backdate(g, _at(day))

    # ------------------------------------------------------------------ point d'entrée
    def run(self):
        self.setup()
        heads, created = [], 0
        for commune, lat, lon, milieu, n, level in self.communes:
            for _ in range(n):
                lvl = min(0.95, max(0.05, self.rng.gauss(level, 0.18)))
                hh, head, resp, baseline_day = self.household(commune, lat, lon, milieu, lvl)
                flag_from_survey(resp, resp.collected_by)
                strength, program = self.support(hh, head, resp.answers, baseline_day, commune.code)
                latest = resp
                if self.rng.random() < 0.5:
                    latest = self.reassess(hh, head, resp.answers, baseline_day, strength) or resp
                self.apply(head, latest, program, baseline_day)
                heads.append(head)
                created += 1
        self.grievances(heads)
        return created


def enrich(rng, org, qv, users, password, log):
    if Household.objects.filter(organization=org, address_description__endswith=MARKER).exists():
        return 0
    users = {**users, "_password": password}
    return Enricher(rng, org, qv, users, log).run()
