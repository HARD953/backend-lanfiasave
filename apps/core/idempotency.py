"""Prise en charge de l'en-tête Idempotency-Key pour les créations sensibles (mobile hors ligne)."""
from django.db import IntegrityError, transaction
from rest_framework.response import Response

from .models import IdempotencyRecord


class IdempotentCreateMixin:
    """Rejouer un POST avec la même clé renvoie la réponse initiale, sans recréer l'objet."""

    def create(self, request, *args, **kwargs):
        key = request.headers.get("Idempotency-Key")
        if not key:
            return super().create(request, *args, **kwargs)
        endpoint = request.path
        existing = IdempotencyRecord.objects.filter(key=key, user=request.user, endpoint=endpoint).first()
        if existing:
            resp = Response(existing.response_body, status=existing.status_code)
            resp["Idempotent-Replay"] = "true"
            return resp
        response = super().create(request, *args, **kwargs)
        if 200 <= response.status_code < 300:
            try:
                with transaction.atomic():
                    IdempotencyRecord.objects.create(key=key, user=request.user, endpoint=endpoint,
                                                     status_code=response.status_code,
                                                     response_body=_jsonable(response.data))
            except IntegrityError:
                pass
        return response


def _jsonable(data):
    import json

    from rest_framework.utils.encoders import JSONEncoder

    return json.loads(json.dumps(data, cls=JSONEncoder))
