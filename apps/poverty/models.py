from django.db import models

from apps.core.models import BaseModel


class IndicatorTarget(BaseModel):
    """Valeur de référence et cible fixées par une organisation pour un indicateur du cadre de suivi."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT,
                                     related_name="indicator_targets")
    code = models.CharField(max_length=40)
    baseline = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
                                   help_text="Valeur de départ (situation de référence)")
    target = models.DecimalField(max_digits=12, decimal_places=2)
    deadline = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    updated_by = models.ForeignKey("accounts.User", null=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "code"], name="uniq_indicator_target")]
