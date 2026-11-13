from django.db import models

from apps.core.models import BaseModel


class Organization(BaseModel):
    """Organisation cliente (ONG, fondation, collectivité, programme RSE). Unité d'isolation des données."""

    class Kind(models.TextChoices):
        NGO = "ngo", "ONG"
        FOUNDATION = "foundation", "Fondation"
        LOCAL_AUTHORITY = "local_authority", "Collectivité"
        PUBLIC_INSTITUTION = "public_institution", "Institution publique"
        CSR = "csr", "Programme RSE"
        OPERATOR = "operator", "Opérateur de la plateforme"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspendue"
        ARCHIVED = "archived", "Archivée"

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=30, choices=Kind.choices)
    country = models.CharField(max_length=2, default="CI", help_text="Code pays ISO 3166-1 alpha-2")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    health_coverage_label = models.CharField(max_length=40, default="CMU",
                                             help_text="Dénomination de la couverture maladie (paramétrable par pays)")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
