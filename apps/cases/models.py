"""
Instruction, décisions et recours (cahier §7).

Pipeline : candidature → recevabilité → vérification → scoring consultatif → avis social → comité
→ décision motivée → notification. Une décision est toujours humaine, motivée, horodatée et immuable :
une révision crée une nouvelle décision qui remplace la précédente (DEC-01, DEC-02, IA-03).
"""
from datetime import timedelta

from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel


class Application(BaseModel):
    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Déposée"
        ADMISSIBLE = "admissible", "Recevable"
        INADMISSIBLE = "inadmissible", "Non recevable"
        ON_HOLD = "on_hold", "En attente de pièces"
        UNDER_REVIEW = "under_review", "Avis social en cours"
        COMMITTEE = "committee", "Au comité"
        DECIDED = "decided", "Décidée"
        WITHDRAWN = "withdrawn", "Retirée"

    TRANSITIONS = {
        "submitted": {"admissible", "inadmissible", "on_hold", "withdrawn"},
        "on_hold": {"admissible", "inadmissible", "withdrawn"},
        "admissible": {"under_review", "withdrawn"},
        "inadmissible": {"submitted"},  # réexamen possible
        "under_review": {"committee", "on_hold", "withdrawn"},
        "committee": {"decided", "under_review"},
        "decided": {"committee"},  # uniquement via recours accueilli
        "withdrawn": set(),
    }

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT,
                                     related_name="applications")
    program = models.ForeignKey("programs.Program", on_delete=models.PROTECT, related_name="applications")
    person = models.ForeignKey("beneficiaries.Person", on_delete=models.PROTECT, related_name="applications")
    rule_version = models.ForeignKey("programs.EligibilityRuleVersion", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="applications")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUBMITTED)
    status_reason = models.TextField(blank=True)
    score = models.ForeignKey("scoring.ScoreResult", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="applications", help_text="Score consultatif au moment de l'instruction")
    social_opinion = models.TextField(blank=True)
    assigned_to = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="assigned_applications")
    submitted_by = models.ForeignKey("accounts.User", null=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "program", "status"])]
        constraints = [
            models.UniqueConstraint(fields=["program", "person"],
                                    condition=~models.Q(status__in=["withdrawn"]),
                                    name="one_open_application_per_program"),
        ]

    def __str__(self):
        return f"{self.person} → {self.program}"


class StatusChange(BaseModel):
    """Historique des transitions d'une candidature."""

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="history")
    from_status = models.CharField(max_length=20)
    to_status = models.CharField(max_length=20)
    reason = models.TextField(blank=True)
    actor = models.ForeignKey("accounts.User", null=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["created_at"]


class Decision(BaseModel):
    class Outcome(models.TextChoices):
        APPROVED = "approved", "Accordée"
        REJECTED = "rejected", "Refusée"
        DEFERRED = "deferred", "Ajournée"

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT,
                                     related_name="decisions")
    application = models.ForeignKey(Application, on_delete=models.PROTECT, related_name="decisions")
    outcome = models.CharField(max_length=20, choices=Outcome.choices)
    motive = models.TextField(help_text="Motif compréhensible, communicable au bénéficiaire")
    internal_notes = models.TextField(blank=True)
    decided_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="decisions")
    committee_members = models.JSONField(default=list, help_text="Membres présents (identifiants et noms)")
    quorum_met = models.BooleanField(default=True)
    rule_version = models.ForeignKey("programs.EligibilityRuleVersion", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="+")
    score_snapshot = models.JSONField(null=True, blank=True)
    supersedes = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="superseded_by")
    notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            changed = {f for f in ("outcome", "motive", "decided_by_id", "application_id")
                       if getattr(self, f) != getattr(Decision.objects.get(pk=self.pk), f)}
            if changed:
                raise ValueError("Une décision est immuable : une révision crée une nouvelle décision.")
        super().save(*args, **kwargs)

    @property
    def is_current(self):
        return not hasattr(self, "superseded_by") or self.superseded_by is None


class Appeal(BaseModel):
    class Status(models.TextChoices):
        OPEN = "open", "Déposé"
        UNDER_REVIEW = "under_review", "En examen"
        UPHELD = "upheld", "Accueilli (réexamen)"
        DISMISSED = "dismissed", "Rejeté"

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT,
                                     related_name="appeals")
    decision = models.ForeignKey(Decision, on_delete=models.PROTECT, related_name="appeals")
    motive = models.TextField()
    filed_by = models.ForeignKey("accounts.User", null=True, on_delete=models.PROTECT, related_name="+")
    filed_via = models.CharField(max_length=20, default="agent",
                                 choices=[("agent", "Par un agent"), ("beneficiary_app", "Application bénéficiaire")])
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    due_date = models.DateField()
    resolution = models.TextField(blank=True)
    resolved_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+")
    resolved_at = models.DateTimeField(null=True, blank=True)

    SLA_DAYS = 30

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.due_date:
            self.due_date = (timezone.now() + timedelta(days=self.SLA_DAYS)).date()
        super().save(*args, **kwargs)
