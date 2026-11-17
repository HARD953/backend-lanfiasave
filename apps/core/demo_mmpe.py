"""
Démonstration du pilote MMPE à Doropo (Bounkani) — SCÉNARIO ENTIÈREMENT FICTIF.

Villages, exploitant, ménages et montants sont inventés ; aucune référence à une entreprise réelle.
Les sous-préfectures du département servent seulement de cadre géographique.
Les demandes parcourent le vrai circuit (services du dispositif) puis sont datées sur les 4 derniers mois.
"""
from datetime import datetime, time, timedelta

from django.utils import timezone

from apps.accounts.models import Membership, User
from apps.accounts.roles import Role
from apps.assistance.models import Assistance
from apps.claims import services
from apps.claims.models import Claim, ClaimAppeal, ClaimDecision, ClaimEvent, ProtectedReport, UrgentMeasure
from apps.operations.models import Operation, Partner
from apps.territories.models import Territory

FIRST_F = ["Tiéné", "Bilé", "Kalé", "Mariam", "Awa", "Sié", "Ini", "Fatou", "Korotoum", "Salimata", "Djénéba", "Tenin"]
FIRST_M = ["Sami", "Pindé", "Kouakou", "Moussa", "Siaka", "Dari", "Yacouba", "Bakary", "Lassina", "Hien", "Issa"]
LAST = ["Kambiré", "Hien", "Palé", "Somé", "Da", "Kam", "Dah", "Sib", "Ouattara", "Traoré", "Koné", "Dabiré",
        "Kpodar", "Méda", "Youl"]
RELAY_OF = {"SP-DRP": "doropo", "SP-DAN": "danoa", "SP-KAL": "kalamon", "SP-NIA": "niamoue"}
LANGUAGES = ["lobiri", "koulango", "dioula", "lobiri", "koulango"]

STAFF = [
    ("relais.doropo", "Sié", "Kambiré", Role.RELAY, "SP-DRP"),
    ("relais.danoa", "Ini", "Palé", Role.RELAY, "SP-DAN"),
    ("relais.kalamon", "Dari", "Somé", Role.RELAY, "SP-KAL"),
    ("relais.niamoue", "Kalé", "Hien", Role.RELAY, "SP-NIA"),
    ("cellule.doropo", "Adjoua", "Koffi", Role.SOCIAL_WORKER, "DEP"),
    ("prefet.doropo", "Préfet", "(fictif)", Role.PREFECT, "DEP"),
    ("agri.bouna", "Service", "Agriculture (fictif)", Role.TECHNICAL_SERVICE, "DEP"),
    ("hydraulique.bouna", "Service", "Hydraulique (fictif)", Role.TECHNICAL_SERVICE, "DEP"),
    ("payeur", "Opérateur", "de paiement (fictif)", Role.PAYER, None),
    ("exploitant.doropo", "Responsable", "relations communautaires (fictif)", Role.OPERATOR, None),
    ("ministere", "Point focal", "Ministère (fictif)", Role.MINISTRY, None),
    ("independant", "Me Aïcha", "Diallo (fictive)", Role.APPEAL_INDEPENDENT, None),
    ("evaluateur", "Cabinet", "d'évaluation (fictif)", Role.EVALUATOR, None),
]

IMPACT_SETS = [
    (["land_loss", "crop_loss"], "weakened", "agriculture", 70000, 20000),
    (["access_cut", "job_loss"], "indirect", "agriculture", 45000, 10000),
    (["water_access"], "vulnerable", "hydraulics", 25000, 20000),
    (["artisanal_site_closed"], "indirect", "mines", 90000, 15000),
    (["market_moved", "job_loss"], "indirect", "social", 55000, 25000),
    (["rent_increase"], "vulnerable", "social", 30000, 22000),
    (["housing_damage"], "weakened", "mines", 40000, 40000),
    (["land_loss", "water_access"], "weakened", "agriculture", 60000, 10000),
]


def _at(day, hour=10):
    return timezone.make_aware(datetime.combine(day, time(hour, 0)))


class Pilot:
    def __init__(self, rng, org, users, password, log):
        self.rng, self.org, self.users, self.password, self.log = rng, org, users, password, log
        self.today = timezone.localdate()
        self.later = {}

    def territories(self):
        ci = Territory.objects.get(code="CI")
        zz, _ = Territory.objects.get_or_create(code="CI-ZZ", defaults={"name": "District du Zanzan",
                                                                        "level": "district",
                                                                        "parent": ci})
        region, _ = Territory.objects.get_or_create(code="CI-ZZ-BKN", defaults={
            "name": "Région du Bounkani", "level": "region", "parent": zz})
        dep, _ = Territory.objects.get_or_create(code="CI-ZZ-BKN-DRP", defaults={
            "name": "Département de Doropo", "level": "department", "parent": region})
        self.zones = {"DEP": dep}
        self.villages = []
        for code, name in [("SP-DRP", "Doropo"), ("SP-DAN", "Danoa"), ("SP-KAL", "Kalamon"), ("SP-NIA", "Niamoué")]:
            sp, _ = Territory.objects.get_or_create(code=f"CI-ZZ-BKN-{code}", defaults={
                "name": f"Sous-préfecture de {name}", "level": "subprefecture", "parent": dep})
            self.zones[code] = sp
            for k in range(1, 4):
                v, _ = Territory.objects.get_or_create(code=f"CI-ZZ-BKN-{code}-V{k}", defaults={
                    "name": f"Village {name[0]}{k} (fictif)", "level": "village", "parent": sp})
                self.villages.append(v)

    def staff(self):
        self.s = {}
        for username, first, last, role, zone in STAFF:
            u, created = User.objects.get_or_create(username=username,
                                                    defaults={"first_name": first, "last_name": last})
            if created:
                if self.password:
                    u.set_password(self.password)
                else:
                    u.set_unusable_password()
                u.save()
            Membership.objects.get_or_create(user=u, organization=self.org, role=role, program=None,
                                             defaults={"territory": self.zones.get(zone) if zone else None})
            self.s[username] = u
        self.operator = Partner.objects.get_or_create(
            organization=self.org, name="Exploitant aurifère (fictif)",
            defaults={"kind": "mining_company", "acronym": "EAF (démo)"})[0]
        self.hydro = Partner.objects.get_or_create(
            organization=self.org, name="Producteur d'énergie (fictif)", defaults={"kind": "energy_company"})[0]
        Membership.objects.filter(user=self.s["exploitant.doropo"], organization=self.org).update(partner=self.operator)
        self.mine = Operation.objects.get_or_create(
            organization=self.org, title="Projet aurifère de Doropo (scénario fictif)", defaults={
                "kind": "mining_project", "status": "ongoing", "initiated_by": self.operator,
                "territory": self.zones["DEP"], "sites": "Emprise du permis (fictive)",
                "start_date": self.today - timedelta(days=150),
                "description": "Scénario de démonstration du pilote : construction de la mine, extension des emprises "
                               "et décapage."})[0]
        self.dam = Operation.objects.get_or_create(
            organization=self.org, title="Retenue hydroélectrique (scénario fictif)", defaults={
                "kind": "energy", "status": "announced", "initiated_by": self.hydro, "territory": self.zones["DEP"],
                "start_date": self.today - timedelta(days=60)})[0]

    def claim(self, i):
        r = self.rng
        village = r.choice(self.villages)
        sp_code = next(k for k, z in self.zones.items() if z.pk == village.parent_id)
        relay = self.s["relais." + RELAY_OF[sp_code]]
        female = r.random() < 0.55
        impacts, profile, service, before, after = r.choice(IMPACT_SETS)
        energy = i % 12 == 0
        if energy:
            impacts, profile, service, before, after = ["land_flooded", "downstream_changes"], "weakened", \
                "agriculture", 50000, 15000
        day = self.today - timedelta(days=int(r.triangular(0, 120, 12)))
        claim = Claim(
            organization=self.org, operation=self.dam if energy else self.mine,
            operator=self.hydro if energy else self.operator, territory=village,
            claimant_name=f"{r.choice(FIRST_F if female else FIRST_M)} {r.choice(LAST)}",
            claimant_sex="F" if female else "M", claimant_birth_year=r.randint(1950, 2002),
            claimant_phone=f"07{r.randint(10_000_000, 99_999_999)}" if r.random() < 0.55 else "",
            language=r.choice(LANGUAGES), village=village.name, household_size=r.randint(2, 11),
            women_head=female and r.random() < 0.5, has_dependent_person=r.random() < 0.18,
            channel=r.choices(["relay", "permanence", "phone", "sms", "agent", "paper"], [50, 15, 10, 8, 12, 5])[0],
            sector="energy" if energy else "mining", impacts=impacts, profile=profile,
            sources=r.sample(["self", "relay", "operator_list", "community"], 2),
            impact_date=day - timedelta(days=r.randint(5, 60)),
            description="Situation décrite par le ménage (texte fictif de démonstration).",
            assets_lost=r.choice(["Parcelle de 0,5 ha", "Champ d'igname", "Accès au puits", "Place au marché",
                                  "Site d'orpaillage", "Verger d'anacardiers", ""]),
            income_before=before + r.randint(-10, 10) * 1000, income_after=max(0, after + r.randint(-5, 5) * 1000),
            aid_received=r.choice(["", "", "Indemnité de culture de l'exploitant", ""]),
            no_identity_document=r.random() < 0.3, consent_operator_contact=r.random() < 0.5,
            urgent=profile == "vulnerable" and "water_access" in impacts and r.random() < 0.7,
            urgency_reason="water" if "water_access" in impacts else "", recorded_by=relay)
        claim.community_validated = claim.no_identity_document
        claim.save()
        services.register(claim, relay)
        return claim, day, service

    def advance(self, claim, day, service):
        """Fait avancer la demande selon son ancienneté, puis date chaque étape."""
        r, s = self.rng, self.s
        age = (self.today - day).days
        dates = {"registered": _at(day)}
        cell, prefect, committee = s["cellule.doropo"], s["prefet.doropo"], self.users[Role.COMMITTEE_MEMBER]
        tech = s["hydraulique.bouna"] if service == "hydraulics" else s["agri.bouna"]

        def step(key, after_days):
            prev = max(dates.values())
            d = prev + timedelta(days=after_days, hours=r.randint(0, 6))
            if d.date() > self.today:
                return None
            dates[key] = d
            return d

        if claim.urgent:
            m = services.order_urgent(claim, prefect, reason="water", measure="Livraison d'eau par citerne (fictive)")
            ex = dates["registered"] + timedelta(hours=r.choice([20, 40, 60, 90]))
            UrgentMeasure.objects.filter(pk=m.pk).update(created_at=dates["registered"] + timedelta(hours=6))
            if ex.date() <= self.today:
                services.execute_urgent(m, cell, "Citerne livrée (fictif)")
                UrgentMeasure.objects.filter(pk=m.pk).update(executed_at=ex)
        if age < 3 or not step("triaged", r.choice([1, 2, 3, 4, 8])):
            return dates
        services.triage(claim, cell, technical_service=service)
        if claim.operator_id and r.random() < 0.6 and step("transmitted", r.randint(1, 6)):
            services.transmit_to_operator(claim, cell)
            if r.random() < 0.75:
                resp_after = r.choice([7, 15, 25, 40])
                d = dates["transmitted"] + timedelta(days=resp_after)
                if d.date() <= self.today:
                    services.operator_respond(claim, s["exploitant.doropo"], "Réponse de l'exploitant (fictive) : "
                                              "vérification de la parcelle et indemnité de culture proposée.")
                    self.later[claim.pk] = {"operator_responded_at": d, "transmitted_to_operator_at":
                                            dates["transmitted"]}
                else:
                    self.later[claim.pk] = {"transmitted_to_operator_at": dates["transmitted"]}
            else:
                self.later[claim.pk] = {"transmitted_to_operator_at": dates["transmitted"]}
            dates.pop("transmitted")
        if r.random() < 0.08 or not step("inquiry", r.randint(4, 14)):
            return dates
        services.record_inquiry(claim, cell, "Visite à domicile (fictive) : situation constatée, revenu réduit.")
        services.technical_opinion(claim, tech, service=service, opinion="Avis fictif du service technique.",
                                   link_confirmed=r.choices(["yes", "uncertain", "no"], [7, 2, 1])[0])
        if not step("proposed", r.randint(3, 10)):
            return dates
        link = Claim.objects.get(pk=claim.pk).link_confirmed
        outcome = r.choices(["support", "support_orientation", "orientation", "rejected", "deferred"],
                            [28, 28, 14, 22 if link != "no" else 60, 5])[0]
        amount = r.choice([50000, 75000, 100000, 150000]) if outcome in {"support", "support_orientation"} else None
        services.propose(claim, committee, outcome=outcome, amount=amount, measures=["Appui transitoire"] if amount
                         else [], committee_members=["Comité local de Doropo (fictif)"],
                         motive={"rejected": "Lien avec l'activité non établi par le service technique.",
                                 "deferred": "Compléments demandés : attestation du chef de village.",
                                 }.get(outcome, "Perte de revenu liée à l'emprise du projet, vérifiée sur place."))
        if not step("decided", r.randint(1, 7)):
            return dates
        services.validate(claim, prefect, approve=True)
        if outcome == "deferred":
            return dates
        if outcome == "rejected":
            if r.random() < 0.85 and step("appeal", r.randint(3, 15)):
                appeal = services.file_appeal(claim, cell, "Le ménage conteste : la parcelle est dans l'emprise.")
                ClaimAppeal.objects.filter(pk=appeal.pk).update(created_at=dates["appeal"])
                if step("appeal_resolved", r.randint(5, 18)):
                    upheld = r.random() < 0.4
                    services.resolve_appeal(appeal, s["independant"],
                                            upheld=upheld, independent_member="Me Aïcha Diallo (fictive)",
                                            resolution="Commission de recours (fictive) : " + (
                                                "pièces nouvelles, nouvel examen." if upheld else "rejet confirmé."))
                    ClaimAppeal.objects.filter(pk=appeal.pk).update(resolved_at=dates["appeal_resolved"])
            elif age > 70 and step("closed", r.randint(31, 40)):
                services.close(claim, cell, "Décision de rejet notifiée, délai de contestation expiré.")
            return dates
        if not step("support", r.randint(1, 5)):
            return dates
        services.start_support(claim, cell, services=["rsu"] if r.random() < 0.3 else [])
        pay_day = dates["support"] + timedelta(days=r.choice([3, 6, 9, 14, 20]))
        for a in Assistance.objects.filter(claim=claim, status="planned"):
            if pay_day.date() <= self.today and r.random() < 0.9:
                services.confirm_payment(a, s["payeur"], proof=f"MM-DEMO-{r.randint(100000, 999999)}")
                Assistance.objects.filter(pk=a.pk).update(delivered_at=pay_day)
        if not step("followup", r.randint(25, 40)):
            return dates
        services.followup(claim, cell, "Visite à 1 mois (fictive) : activité reprise partiellement.")
        if step("closed", r.randint(30, 60)):
            services.close(claim, cell, "Appui reçu et situation suivie pendant 3 mois (fictif).")
        return dates

    def backdate(self, claim, dates):
        fields = {"registered_at": dates["registered"], "receipt_issued_at": dates["registered"]}
        mapping = {"triaged": "triaged_at", "inquiry": "verification_started_at", "proposed": "proposed_at",
                   "decided": "notified_at", "support": "support_started_at", "closed": "closed_at"}
        c = Claim.objects.get(pk=claim.pk)
        for k, f in mapping.items():
            if getattr(c, f) is not None and k in dates:
                fields[f] = dates[k]
        if c.transmitted_to_operator_at:
            fields.update(self.later.get(c.pk, {}))
        Claim.objects.filter(pk=c.pk).update(**fields)
        kind_day = {"registered": "registered", "urgent_flagged": "registered", "triaged": "triaged",
                    "inquiry": "inquiry", "technical_opinion": "inquiry", "proposed": "proposed",
                    "decided": "decided", "deferred": "decided", "support": "support", "paid": "support",
                    "followup": "followup", "closed": "closed", "appeal_filed": "appeal",
                    "appeal_upheld": "appeal_resolved", "appeal_dismissed": "appeal_resolved"}
        for e in ClaimEvent.objects.filter(claim=c):
            d = dates.get(kind_day.get(e.kind, ""))
            if d:
                ClaimEvent.objects.filter(pk=e.pk).update(created_at=d)
        for d in ClaimDecision.objects.filter(claim=c):
            ClaimDecision.objects.filter(pk=d.pk).update(created_at=dates.get("proposed", dates["registered"]),
                                                         validated_at=dates.get("decided"))
        if "support" in dates:
            Assistance.objects.filter(claim=c).update(created_at=dates["support"], planned_date=dates["support"].date())

    def protected(self):
        rows = [
            ("reprisal", "Menaces d'un chef d'équipe après le dépôt d'une demande (fictif).", "investigating", 3),
            ("reprisal", "Pression pour retirer une demande (fictif).", "resolved", 40),
            ("aid_conditioned", "Aide proposée contre le retrait d'une plainte (fictif).", "investigating", 2),
            ("operator", "Poussière et fissures non reconnues par l'exploitant (fictif).", "resolved", 25),
            ("subcontractor", "Sous-traitant refusant de payer des journaliers (fictif).", "received", 1),
            ("relay", "Relais demandant de l'argent pour enregistrer (fictif).", "resolved", 60),
        ]
        for kind, text, status, days in rows:
            rep = ProtectedReport(organization=self.org, kind=kind, description=text, channel="phone",
                                  territory=self.rng.choice(self.villages), reporter_name="Plaignant (fictif)",
                                  reporter_phone="0700000000")
            services.file_protected(rep, self.s["relais.doropo"])
            created = timezone.now() - timedelta(days=days)
            upd = {"created_at": created, "due_at": created + (timedelta(hours=72) if rep.is_priority
                                                                else timedelta(days=15)), "status": status}
            if status == "resolved":
                upd.update(resolved_at=created + timedelta(hours=50), resolution="Traitée par le membre indépendant "
                                                                                 "(fictif).",
                           handled_by=self.s["independant"])
                if kind == "reprisal":
                    upd["followup_1m_done_at"] = created + timedelta(days=30)
            ProtectedReport.objects.filter(pk=rep.pk).update(**upd)

    def operator_list(self, claims):
        rows = [{"nom": c.claimant_name, "village": c.village, "biens": c.assets_lost}
                for c in claims if c.sector == "mining" and self.rng.random() < 0.55]
        rows += [{"nom": f"{self.rng.choice(FIRST_M)} {self.rng.choice(LAST)}", "village": self.rng.choice(
            self.villages).name, "biens": "Parcelle recensée"} for _ in range(12)]
        services.import_operator_list(self.org, self.s["cellule.doropo"], rows, operation=self.mine,
                                      operator=self.operator, batch="liste-exploitant-demo")

    def run(self):
        self.territories()
        self.staff()
        claims = []
        for i in range(72):
            claim, day, service = self.claim(i)
            dates = self.advance(claim, day, service)
            self.backdate(claim, dates)
            claims.append(Claim.objects.get(pk=claim.pk))
        self.protected()
        self.operator_list(claims)
        services.draw_sample(self.org, self.s["evaluateur"], 12, "Échantillon de démonstration")
        return len(claims)


def seed_pilot(rng, org, users, password, log):
    if Claim.objects.filter(organization=org).exists():
        return 0
    n = Pilot(rng, org, users, password, log).run()
    log(f"Pilote MMPE (Doropo, fictif) : {n} demandes, relais, préfet, payeur, exploitant, ministère.")
    return n

