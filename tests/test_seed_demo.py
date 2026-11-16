"""Le jeu de démonstration se charge sur une base vide, une seule fois, avec des données cohérentes."""
import pytest
from django.core.management import call_command

from apps.beneficiaries.models import Household
from apps.organizations.models import Organization
from apps.poverty.api import poverty_stats

pytestmark = pytest.mark.django_db


def test_seed_demo_is_rich_and_idempotent(monkeypatch):
    monkeypatch.setenv("DEMO_PASSWORD", "Demo-Test-2026!")
    call_command("seed_demo")
    org = Organization.objects.get(code="demo-ong")
    count = Household.objects.filter(organization=org).count()
    assert count >= 200
    stats = poverty_stats([org.pk])
    assert 40 < stats["summary"]["poverty_rate"] < 90
    assert stats["trajectories"]["followed"] > 20
    assert len({t["name"] for t in stats["territories"]}) == 10
    call_command("seed_demo")
    assert Household.objects.filter(organization=org).count() == count


def test_seed_demo_into_existing_organization(org, monkeypatch):
    """En production : la démo va dans l'organisation réelle, sans aucun compte fictif utilisable."""
    from apps.accounts.models import User

    monkeypatch.setenv("DEMO_PASSWORD", "Demo-Test-2026!")
    call_command("seed_demo", organization=org.code)
    assert Household.objects.filter(organization=org).count() >= 200
    assert not Organization.objects.filter(code__in=["demo-ong", "demo-fondation"]).exists()
    assert not User.objects.filter(username__in=["admin.tech", "autre.org"]).exists()
    fake = User.objects.filter(memberships__organization=org).distinct()
    assert fake.exists() and not any(u.has_usable_password() for u in fake)
