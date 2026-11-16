"""Commande create_account : compte terrain créé avec mot de passe masqué et rôle agent recenseur."""
import pytest
from django.core.management import CommandError, call_command

from apps.accounts.models import User
from apps.accounts.roles import Role

pytestmark = pytest.mark.django_db


def test_create_account_makes_enumerator(org, monkeypatch):
    org.code = "lanfiatech"
    org.save()
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "Terrain-2026!x")
    call_command("create_account", username="a.kone", first_name="Awa", last_name="Koné")
    user = User.objects.get(username="a.kone")
    assert user.check_password("Terrain-2026!x") and not user.is_staff
    assert list(user.memberships.values_list("role", flat=True)) == [Role.ENUMERATOR]
    with pytest.raises(CommandError):
        call_command("create_account", username="a.kone", first_name="A", last_name="K")
