from django.db import transaction

from .middleware import get_current_request
from .models import AuditEvent


def _client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    return (forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")) or None


def audit(action, *, obj=None, actor=None, organization=None, reason="", data=None, outcome="success"):
    """Enregistre un événement d'audit. `obj` fournit type, id et organisation par défaut."""
    request = get_current_request()
    if actor is None and request is not None and getattr(request, "user", None) and request.user.is_authenticated:
        actor = request.user
    if organization is None and obj is not None:
        organization = getattr(obj, "organization", None)
    fields = dict(
        actor=actor,
        organization=organization,
        action=action,
        outcome=outcome,
        object_type=obj._meta.label if obj is not None else "",
        object_id=str(obj.pk) if obj is not None else "",
        reason=reason or "",
        data=data or {},
        ip_address=_client_ip(request),
        request_id=getattr(request, "request_id", "") if request else "",
    )
    if outcome != "success" and request is not None and transaction.get_connection().in_atomic_block:
        # Un refus d'accès annule la transaction de la requête : on diffère l'écriture
        # pour que la trace survive au rollback (le middleware la vide après la vue).
        request._deferred_audit = getattr(request, "_deferred_audit", []) + [fields]
        return None
    return AuditEvent.objects.create(**fields)


def flush_deferred(request):
    for fields in getattr(request, "_deferred_audit", []):
        AuditEvent.objects.create(**fields)
    request._deferred_audit = []
