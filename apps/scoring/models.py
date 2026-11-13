from django.db import models

from apps.core.models import BaseModel


class ScoringModelVersion(BaseModel):
    """Grille de scoring versionnée (GOV-02). Une version active n'est jamais modifiée en place."""

    code = models.CharField(max_length=40)
    version = models.PositiveIntegerField()
    config = models.JSONField()
    is_active = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["code", "version"], name="uniq_scoring_version")]

    def __str__(self):
        return f"{self.code} v{self.version}"


class ScoreResult(BaseModel):
    """Résultat consultatif et explicable. Ne vaut jamais décision."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT,
                                     related_name="score_results")
    person = models.ForeignKey("beneficiaries.Person", on_delete=models.PROTECT, related_name="scores")
    survey_response = models.ForeignKey("surveys.SurveyResponse", on_delete=models.PROTECT, related_name="scores")
    scoring_version = models.ForeignKey(ScoringModelVersion, on_delete=models.PROTECT, related_name="results")
    total = models.PositiveSmallIntegerField()
    band = models.CharField(max_length=20)
    completeness = models.DecimalField(max_digits=4, decimal_places=3)
    is_reliable = models.BooleanField()
    details = models.JSONField()
    computed_by = models.ForeignKey("accounts.User", null=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-created_at"]
