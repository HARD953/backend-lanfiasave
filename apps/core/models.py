"""Briques communes : identifiants opaques, horodatage, journal d'audit, idempotence."""
import uuid

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import models


class BaseModel(models.Model):
    """Identifiant UUID non signifiant + horodatage (MLD §20.2)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AuditEvent(models.Model):
    """
    Journal d'audit en ajout seul (GOV-03, SEC-04).
    Aucune modification ni suppression via l'ORM : toute correction est un nouvel événement.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                              related_name="audit_events")
    organization = models.ForeignKey("organizations.Organization", null=True, blank=True,
                                     on_delete=models.PROTECT, related_name="audit_events")
    action = models.CharField(max_length=80, db_index=True)
    outcome = models.CharField(max_length=20, default="success",
                               choices=[("success", "Succès"), ("denied", "Refusé"), ("error", "Erreur")])
    object_type = models.CharField(max_length=80, blank=True)
    object_id = models.CharField(max_length=64, blank=True, db_index=True)
    reason = models.TextField(blank=True)
    data = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    request_id = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-occurred_at"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionDenied("Le journal d'audit est en ajout seul.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Le journal d'audit est en ajout seul.")

    def __str__(self):
        return f"{self.occurred_at:%Y-%m-%d %H:%M} {self.action} {self.object_type}:{self.object_id}"


class IdempotencyRecord(models.Model):
    """Réponse mémorisée pour un en-tête Idempotency-Key (synchronisation mobile, MOB-01)."""

    key = models.CharField(max_length=128)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    endpoint = models.CharField(max_length=200)
    status_code = models.PositiveSmallIntegerField()
    response_body = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["key", "user", "endpoint"], name="uniq_idempotency")]
