import contextvars
import uuid

_current_request = contextvars.ContextVar("current_request", default=None)


def get_current_request():
    return _current_request.get()


class RequestContextMiddleware:
    """Expose la requête courante (pour l'audit) et un identifiant de corrélation."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        token = _current_request.set(request)
        try:
            response = self.get_response(request)
            from .audit import flush_deferred

            flush_deferred(request)
        finally:
            _current_request.reset(token)
        response["X-Request-ID"] = request.request_id
        return response
