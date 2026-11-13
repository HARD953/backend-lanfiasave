import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.accounts.models import User

pytestmark = pytest.mark.django_db

PWD = "Premier-Admin-2026!"


def test_bootstrap_admin_creates_working_account(monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": PWD)
    call_command("bootstrap_admin", username="direction", first_name="Awa", last_name="Koné",
                 organization="LanfiaTech", code="lanfiatech")
    user = User.objects.get(username="direction")
    assert user.is_superuser and user.is_staff
    assert user.active_memberships().count() == 6

    c = APIClient()
    r = c.post("/api/v1/auth/login/", {"username": "direction", "password": PWD}, format="json")
    assert r.status_code == 200
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
    assert c.get("/api/v1/persons/").status_code == 200  # accès au portail grâce aux rôles

    # Idempotent : relancer ne duplique ni l'organisation ni les rôles.
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    call_command("bootstrap_admin", username="direction", first_name="Awa", last_name="Koné")
    assert User.objects.get(username="direction").active_memberships().count() == 6
