from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler


class IllegalTransition(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Transition d'état non autorisée."
    default_code = "illegal_transition"


def api_exception_handler(exc, context):
    """Format d'erreur normalisé : {"error": {"code", "message", "details"}}."""
    if isinstance(exc, DjangoValidationError):
        detail = exc.message_dict if hasattr(exc, "message_dict") else {"non_field_errors": exc.messages}
        return Response({"error": {"code": "invalid", "message": "Données invalides.", "details": detail}},
                        status=status.HTTP_400_BAD_REQUEST)

    response = exception_handler(exc, context)
    if response is None:
        return None
    code = getattr(exc, "default_code", "error")
    if hasattr(exc, "get_codes"):
        codes = exc.get_codes()
        if isinstance(codes, str):
            code = codes
    data = response.data
    message = data.get("detail") if isinstance(data, dict) and "detail" in data else "Requête invalide."
    details = None if isinstance(data, dict) and set(data) == {"detail"} else data
    response.data = {"error": {"code": code, "message": str(message), "details": details}}
    return response
