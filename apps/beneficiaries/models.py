"""
Dossiers personnes et ménages (cahier §4).

- Identifiant public opaque et non signifiant (`public_code`).
- Enrôlement provisoire possible sans pièce d'identité ni téléphone (REC-01, URG-01).
- Appartenance au ménage datée (REC-03).
- Doublons : signalés, jamais fusionnés automatiquement ; fusion logique réversible (REC-05).
"""
import secrets
import unicodedata

from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel

_ALPHABET = "ACDEFGHJKMNPQRTUVWXY34679"  # sans caractères ambigus (0/O, 1/I/L, 2/Z, 5/S, 8/B)


def new_public_code(prefix: str) -> str:
    body = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    return f"{prefix}-{body[:4]}-{body[4:]}"


def normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return " ".join(value.lower().replace("-", " ").split())


class RecordStatus(models.TextChoices):
    DRAFT = "draft", "Brouillon"
    SUBMITTED = "submitted", "Soumis"
    QUALITY_CHECK = "quality_check", "Contrôle qualité"
    TO_CORRECT = "to_correct", "À corriger"
    VERIFIED = "verified", "Vérifié"
    ACTIVE = "active", "Actif"
    ARCHIVED = "archived", "Archivé"


RECORD_TRANSITIONS = {
    RecordStatus.DRAFT: {RecordStatus.SUBMITTED},
    RecordStatus.SUBMITTED: {RecordStatus.QUALITY_CHECK},
    RecordStatus.QUALITY_CHECK: {RecordStatus.VERIFIED, RecordStatus.TO_CORRECT},
    RecordStatus.TO_CORRECT: {RecordStatus.SUBMITTED},
    RecordStatus.VERIFIED: {RecordStatus.ACTIVE, RecordStatus.TO_CORRECT},
    RecordStatus.ACTIVE: {RecordStatus.ARCHIVED, RecordStatus.TO_CORRECT},
    RecordStatus.ARCHIVED: set(),
}


class Household(BaseModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT,
                                     related_name="households")
    public_code = models.CharField(max_length=20, unique=True, editable=False)
    territory = models.ForeignKey("territories.Territory", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="households")
    address_description = models.CharField(max_length=255, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    gps_consent = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=RecordStatus.choices, default=RecordStatus.SUBMITTED)
    created_by = models.ForeignKey("accounts.User", null=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "status"]), models.Index(fields=["territory"])]

    def save(self, *args, **kwargs):
        if not self.public_code:
            self.public_code = new_public_code("MEN")
        if not self.gps_consent:
            self.latitude = self.longitude = None
        super().save(*args, **kwargs)

    def __str__(self):
        return self.public_code


class Person(BaseModel):
    class Sex(models.TextChoices):
        FEMALE = "F", "Femme"
        MALE = "M", "Homme"
        UNDISCLOSED = "U", "Non déclaré"

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT,
                                     related_name="persons")
    public_code = models.CharField(max_length=20, unique=True, editable=False)
    is_provisional = models.BooleanField(default=True,
                                         help_text="Identité non encore réconciliée avec une pièce officielle")
    enrollment_context = models.CharField(max_length=20, default="standard",
                                          choices=[("standard", "Standard"), ("emergency", "Urgence")])
    first_name = models.CharField("Prénom(s) déclaré(s)", max_length=120)
    last_name = models.CharField("Nom déclaré", max_length=120, blank=True)
    usual_name = models.CharField("Nom d'usage", max_length=120, blank=True)
    sex = models.CharField(max_length=1, choices=Sex.choices, default=Sex.UNDISCLOSED)
    birth_date = models.DateField(null=True, blank=True)
    estimated_age = models.PositiveSmallIntegerField(null=True, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    language = models.CharField(max_length=40, blank=True)
    territory = models.ForeignKey("territories.Territory", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="persons")
    residence_status = models.CharField(max_length=20, blank=True,
                                        choices=[("resident", "Résident"), ("displaced", "Déplacé"),
                                                 ("returnee", "Retourné"), ("refugee", "Réfugié"),
                                                 ("unknown", "Inconnu")])
    health_coverage_number = models.CharField("Numéro CMU (facultatif)", max_length=40, blank=True)
    has_identity_document = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=RecordStatus.choices, default=RecordStatus.SUBMITTED)
    merged_into = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="merged_aliases",
                                    help_text="Fusion logique réversible : le dossier reste consultable")
    search_name = models.CharField(max_length=255, editable=False, db_index=True)
    created_by = models.ForeignKey("accounts.User", null=True, on_delete=models.PROTECT, related_name="+")

    SENSITIVE_FIELDS = ("phone", "health_coverage_number", "birth_date")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "status"]), models.Index(fields=["territory"])]
        constraints = [
            models.CheckConstraint(condition=Q(estimated_age__isnull=True) | Q(estimated_age__lte=120),
                                   name="person_age_plausible"),
        ]

    def save(self, *args, **kwargs):
        if not self.public_code:
            self.public_code = new_public_code("LFS")
        self.search_name = normalize_name(f"{self.first_name} {self.last_name} {self.usual_name}")
        super().save(*args, **kwargs)

    @property
    def display_name(self):
        return " ".join(p for p in [self.first_name, self.last_name] if p) or self.usual_name

    @property
    def age(self):
        if self.birth_date:
            from datetime import date

            t = date.today()
            return t.year - self.birth_date.year - ((t.month, t.day) < (self.birth_date.month, self.birth_date.day))
        return self.estimated_age

    def current_household_membership(self):
        return self.household_memberships.filter(end_date__isnull=True).select_related("household").first()

    def __str__(self):
        return f"{self.public_code} {self.display_name}"


class HouseholdMembership(BaseModel):
    """Appartenance datée d'une personne à un ménage (une personne peut changer de ménage)."""

    class Relation(models.TextChoices):
        HEAD = "head", "Chef de ménage"
        SPOUSE = "spouse", "Conjoint(e)"
        CHILD = "child", "Enfant"
        PARENT = "parent", "Parent"
        RELATIVE = "relative", "Autre parent"
        OTHER = "other", "Autre"

    household = models.ForeignKey(Household, on_delete=models.PROTECT, related_name="memberships")
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="household_memberships")
    relation = models.CharField(max_length=20, choices=Relation.choices)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    end_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["start_date"]
        constraints = [
            models.UniqueConstraint(fields=["person"], condition=Q(end_date__isnull=True),
                                    name="one_current_household_per_person"),
        ]


class DuplicateCandidate(BaseModel):
    """Indice de doublon gradué, soumis à revue humaine (jamais de fusion automatique)."""

    class Status(models.TextChoices):
        OPEN = "open", "À examiner"
        CONFIRMED = "confirmed", "Doublon confirmé"
        DISMISSED = "dismissed", "Écarté"

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="+")
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="duplicate_candidates")
    other = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="+")
    confidence = models.PositiveSmallIntegerField(help_text="Indice 0–100")
    reasons = models.JSONField(default=list)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    reviewed_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-confidence"]
        constraints = [models.UniqueConstraint(fields=["person", "other"], name="uniq_duplicate_pair")]
