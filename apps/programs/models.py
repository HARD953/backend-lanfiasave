from django.db import models

from apps.core.models import BaseModel


class Program(BaseModel):
    """Programme social (cahier §6). Les aides d'un programme ne sont jamais utilisables dans un autre."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        SUBMITTED = "submitted", "Soumis"
        APPROVED = "approved", "Approuvé"
        ACTIVE = "active", "Actif"
        SUSPENDED = "suspended", "Suspendu"
        CLOSED = "closed", "Clôturé"
        ARCHIVED = "archived", "Archivé"

    TRANSITIONS = {
        "draft": {"submitted"},
        "submitted": {"approved", "draft"},
        "approved": {"active"},
        "active": {"suspended", "closed"},
        "suspended": {"active", "closed"},
        "closed": {"archived"},
        "archived": set(),
    }

    class Sector(models.TextChoices):
        SOCIAL_SAFETY_NET = "safety_net", "Filets sociaux"
        EMERGENCY = "emergency", "Urgence / post-crise"
        AGRICULTURE = "agriculture", "Agriculture"
        DIGITAL_INCLUSION = "digital", "Inclusion numérique"
        TRAINING = "training", "Formation et insertion"
        CSR = "csr", "RSE"
        OTHER = "other", "Autre"

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT,
                                     related_name="programs")
    code = models.SlugField(max_length=40)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    sector = models.CharField(max_length=20, choices=Sector.choices, default=Sector.OTHER)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    budget_amount = models.DecimalField(max_digits=14, decimal_places=0, null=True, blank=True)
    currency = models.CharField(max_length=3, default="XOF")
    target_beneficiaries = models.PositiveIntegerField(null=True, blank=True, help_text="Objectif indicatif")
    territories = models.ManyToManyField("territories.Territory", blank=True, related_name="programs")
    eligibility_summary = models.TextField(blank=True, help_text="Critères publiés, en langage clair")
    funders = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["organization", "code"], name="uniq_program_code")]

    def __str__(self):
        return self.name


class EligibilityRuleVersion(BaseModel):
    """Règle d'éligibilité versionnée : un dossier est toujours relu avec la règle en vigueur à sa date (GOV-02)."""

    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="rule_versions")
    version = models.PositiveIntegerField()
    effective_from = models.DateField()
    rules = models.JSONField(default=dict, help_text="Critères structurés (ex. territoires, âge, situation)")
    description = models.TextField()
    created_by = models.ForeignKey("accounts.User", null=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["program", "-version"]
        constraints = [models.UniqueConstraint(fields=["program", "version"], name="uniq_rule_version")]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Une version de règle est immuable : créez une nouvelle version.")
        super().save(*args, **kwargs)
