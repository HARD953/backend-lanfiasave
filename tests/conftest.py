import uuid
from datetime import date

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Membership, User
from apps.accounts.roles import Role
from apps.organizations.models import Organization
from apps.programs.models import EligibilityRuleVersion, Program
from apps.surveys.definitions import QUESTIONNAIRE_V2
from apps.surveys.models import QuestionnaireVersion
from apps.territories.models import Territory

PWD = "Test-password-2026!"


@pytest.fixture
def org(db):
    return Organization.objects.create(code="org-a", name="Org A", kind="ngo")


@pytest.fixture
def other_org(db):
    return Organization.objects.create(code="org-b", name="Org B", kind="foundation")


@pytest.fixture
def territory(db):
    ci = Territory.objects.create(code="CI", name="Côte d'Ivoire", level="country")
    return Territory.objects.create(code="CI-AB-ABO", name="Abobo", level="commune", parent=ci)


@pytest.fixture
def make_user(db):
    def _make(username, role=None, organization=None, program=None):
        u = User.objects.create_user(username=username, password=PWD, first_name=username.title())
        if role:
            Membership.objects.create(user=u, organization=organization, role=role, program=program)
        return u
    return _make


@pytest.fixture
def social_worker(make_user, org):
    return make_user("social", Role.SOCIAL_WORKER, org)


@pytest.fixture
def enumerator(make_user, org):
    return make_user("recenseur", Role.ENUMERATOR, org)


@pytest.fixture
def committee(make_user, org):
    return make_user("comite", Role.COMMITTEE_MEMBER, org)


@pytest.fixture
def committee2(make_user, org):
    return make_user("comite2", Role.COMMITTEE_MEMBER, org)


@pytest.fixture
def outsider(make_user, other_org):
    return make_user("autre", Role.SOCIAL_WORKER, other_org)


@pytest.fixture
def client_for():
    def _client(user):
        c = APIClient()
        c.force_authenticate(user)
        return c
    return _client


@pytest.fixture
def questionnaire(db):
    """Questionnaire v2 publié, installé par la migration surveys.0002."""
    return QuestionnaireVersion.objects.get(code="RECENSEMENT", version=QUESTIONNAIRE_V2["version"],
                                            status="published")


@pytest.fixture
def program(org):
    p = Program.objects.create(organization=org, code="prog", name="Programme test", status="active")
    EligibilityRuleVersion.objects.create(program=p, version=1, effective_from=date(2026, 1, 1),
                                          description="Règle test")
    return p


@pytest.fixture
def submission(org, questionnaire, territory):
    def _make(**overrides):
        payload = {
            "client_uuid": str(uuid.uuid4()),
            "organization": str(org.pk),
            "questionnaire": str(questionnaire.pk),
            "territory": str(territory.pk),
            "collected_at": timezone.now().isoformat(),
            "device_id": "tablette-01",
            "members": [{
                "client_uuid": str(uuid.uuid4()), "first_name": "Aya", "last_name": "Kouassi", "sex": "F",
                "estimated_age": 34, "relation": "head",
                "answers": {"housing_type": "precarious", "monthly_income": "none", "household_size": 5},
            }],
        }
        payload.update(overrides)
        return payload
    return _make
