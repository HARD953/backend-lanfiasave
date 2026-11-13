"""
Opérations et dossiers de personnes affectées.

Une « opération » est un événement qui touche des personnes et qu'une structure déclenche ou subit :
déguerpissement / libération d'emprise décidée par l'État ou une mairie, inondation, éboulement, incendie,
projet minier et réinstallation, démantèlement d'un site d'orpaillage, conflit et déplacement, épidémie…

Pour chaque personne touchée, un « dossier d'accompagnement » (OperationCase) est ouvert : il trace les pertes,
les besoins, la situation actuelle (relogée ? indemnisée ?) et toutes les aides reçues, pour qu'aucune personne
affectée ne soit perdue de vue.
"""
import secrets

from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel


def _code(prefix: str) -> str:
    alphabet = "ACDEFGHJKMNPQRTUVWXY34679"
    return f"{prefix}-" + "".join(secrets.choice(alphabet) for _ in range(6))


class Partner(BaseModel):
    """Structure intervenante : gouvernement, collectivité, entreprise minière, ONG, agence des Nations unies…"""

    class Kind(models.TextChoices):
        GOVERNMENT = "government", "État / ministère / préfecture"
        LOCAL_AUTHORITY = "local_authority", "Collectivité (mairie, conseil régional, district)"
        PUBLIC_AGENCY = "public_agency", "Agence ou établissement public"
        MINING_COMPANY = "mining_company", "Entreprise minière"
        PRIVATE_COMPANY = "private_company", "Entreprise privée (BTP, agro-industrie…)"
        NGO = "ngo", "ONG / association"
        UN_AGENCY = "un_agency", "Agence des Nations unies / coopération"
        COMMUNITY = "community", "Chefferie / organisation communautaire"
        RELIGIOUS = "religious", "Organisation confessionnelle"
        OTHER = "other", "Autre"

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="partners")
    name = models.CharField(max_length=200)
    acronym = models.CharField(max_length=40, blank=True)
    kind = models.CharField(max_length=30, choices=Kind.choices)
    contact_name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["organization", "name"], name="uniq_partner_name")]

    def __str__(self):
        return self.acronym or self.name


class Operation(BaseModel):
    class Kind(models.TextChoices):
        EVICTION = "eviction", "Déguerpissement / libération d'emprise"
        FLOOD = "flood", "Inondation"
        LANDSLIDE = "landslide", "Éboulement / glissement de terrain"
        FIRE = "fire", "Incendie"
        MINING_PROJECT = "mining_project", "Projet minier (réinstallation, perte de terres)"
        ILLEGAL_MINING = "illegal_mining", "Orpaillage illégal / démantèlement de site"
        INFRASTRUCTURE = "infrastructure", "Projet d'infrastructure (route, barrage…)"
        CONFLICT = "conflict", "Conflit / déplacement de population"
        EPIDEMIC = "epidemic", "Épidémie"
        FOOD_CRISIS = "food_crisis", "Crise alimentaire / sécheresse"
        OTHER = "other", "Autre"

    class Status(models.TextChoices):
        ANNOUNCED = "announced", "Annoncée (préavis)"
        ONGOING = "ongoing", "En cours"
        RESPONSE = "response", "Phase d'accompagnement"
        CLOSED = "closed", "Clôturée"

    TRANSITIONS = {
        "announced": {"ongoing", "closed"},
        "ongoing": {"response", "closed"},
        "response": {"closed", "ongoing"},
        "closed": {"response"},
    }

    # Vulnérabilité attribuée automatiquement aux personnes affectées, selon la nature de l'opération.
    VULNERABILITY_BY_KIND = {
        "eviction": "evicted",
        "infrastructure": "evicted",
        "flood": "disaster_victim",
        "landslide": "disaster_victim",
        "fire": "disaster_victim",
        "mining_project": "mining_affected",
        "illegal_mining": "mining_affected",
        "conflict": "idp",
        "food_crisis": "food_insecure",
    }

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT,
                                     related_name="operations")
    code = models.CharField(max_length=20, unique=True, editable=False)
    title = models.CharField(max_length=200)
    kind = models.CharField(max_length=30, choices=Kind.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ONGOING)
    description = models.TextField(blank=True)
    initiated_by = models.ForeignKey(Partner, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="initiated_operations",
                                     help_text="Structure à l'origine de l'opération (ex. ministère, mairie, mine)")
    partners = models.ManyToManyField(Partner, blank=True, related_name="operations",
                                      help_text="Structures qui interviennent dans l'accompagnement")
    territory = models.ForeignKey("territories.Territory", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="operations")
    sites = models.TextField(blank=True, help_text="Quartiers, villages ou sites concernés")
    legal_reference = models.CharField(max_length=255, blank=True,
                                       help_text="Arrêté, décision, convention, plan de réinstallation…")
    notice_date = models.DateField(null=True, blank=True, help_text="Date du préavis donné aux habitants")
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    estimated_households = models.PositiveIntegerField(null=True, blank=True)
    compensation_policy = models.TextField(blank=True,
                                           help_text="Barème annoncé (ex. aide au relogement de 250 000 FCFA)")
    compensation_amount_per_household = models.DecimalField(max_digits=12, decimal_places=0, null=True,
                                                             blank=True)
    created_by = models.ForeignKey("accounts.User", null=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-start_date", "-created_at"]
        indexes = [models.Index(fields=["organization", "status", "kind"])]

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = _code("OPS")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} {self.title}"


class OperationCase(BaseModel):
    """Dossier d'accompagnement d'une personne affectée par une opération."""

    class Status(models.TextChoices):
        IDENTIFIED = "identified", "Identifiée"
        ASSESSED = "assessed", "Besoins évalués"
        SUPPORTED = "supported", "En accompagnement"
        RELOCATED = "relocated", "Relogée / réinstallée"
        CLOSED = "closed", "Dossier clôturé"
        LOST_CONTACT = "lost_contact", "Perdue de vue"

    TRANSITIONS = {
        "identified": {"assessed", "supported", "lost_contact", "closed"},
        "assessed": {"supported", "relocated", "lost_contact", "closed"},
        "supported": {"relocated", "lost_contact", "closed", "assessed"},
        "relocated": {"supported", "closed", "lost_contact"},
        "lost_contact": {"identified", "assessed", "supported", "closed"},
        "closed": {"supported"},
    }

    class Occupancy(models.TextChoices):
        OWNER_TITLED = "owner_titled", "Propriétaire avec titre"
        OWNER_UNTITLED = "owner_untitled", "Propriétaire sans titre (coutumier, attestation…)"
        TENANT = "tenant", "Locataire"
        HOSTED = "hosted", "Hébergé(e) gratuitement"
        OCCUPANT = "occupant", "Occupant sans droit"
        TRADER = "trader", "Commerçant / exploitant (activité sur le site)"
        UNKNOWN = "unknown", "Inconnu"

    NEEDS = [
        ("shelter", "Abri / relogement"),
        ("food", "Alimentation"),
        ("health", "Santé"),
        ("documents", "Pièces d'état civil / identité"),
        ("schooling", "Scolarité des enfants"),
        ("livelihood", "Reprise d'activité / revenus"),
        ("compensation", "Indemnisation"),
        ("legal", "Assistance juridique"),
        ("psychosocial", "Soutien psychosocial"),
        ("protection", "Protection (enfant, VBG)"),
        ("water_hygiene", "Eau, hygiène"),
    ]

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT,
                                     related_name="operation_cases")
    operation = models.ForeignKey(Operation, on_delete=models.PROTECT, related_name="cases")
    person = models.ForeignKey("beneficiaries.Person", on_delete=models.PROTECT, related_name="operation_cases")
    household = models.ForeignKey("beneficiaries.Household", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="operation_cases")
    case_number = models.CharField(max_length=20, unique=True, editable=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IDENTIFIED)
    occupancy = models.CharField(max_length=20, choices=Occupancy.choices, default=Occupancy.UNKNOWN)
    lost_housing = models.BooleanField(default=False)
    lost_livelihood = models.BooleanField(default=False)
    lost_documents = models.BooleanField(default=False)
    children_schooling_disrupted = models.BooleanField(default=False)
    lost_assets = models.TextField(blank=True, help_text="Biens perdus (maison, boutique, champ, bétail…)")
    needs = models.JSONField(default=list, blank=True)
    current_location = models.CharField(max_length=255, blank=True, help_text="Où vit la personne aujourd'hui")
    relocation_site = models.CharField(max_length=255, blank=True)
    compensation_due = models.DecimalField(max_digits=12, decimal_places=0, null=True, blank=True)
    notes = models.TextField(blank=True)
    assigned_to = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="assigned_operation_cases")
    identified_by = models.ForeignKey("accounts.User", null=True, on_delete=models.PROTECT, related_name="+")
    last_contact_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["operation", "person"], name="uniq_case_per_operation")]
        indexes = [models.Index(fields=["operation", "status"])]

    def save(self, *args, **kwargs):
        if not self.case_number:
            self.case_number = _code("DOS")
        valid = {code for code, _ in self.NEEDS}
        self.needs = sorted({n for n in (self.needs or []) if n in valid})
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.case_number} {self.person}"


OPEN_CASE_STATUSES = Q(status__in=["identified", "assessed", "supported", "relocated", "lost_contact"])
