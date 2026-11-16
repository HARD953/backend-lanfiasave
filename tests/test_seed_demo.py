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
