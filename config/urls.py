from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from apps.accounts.api import BeneficiaryLoginView, LoginView, MeView
from apps.beneficiaries.api import HouseholdViewSet, PersonViewSet
from apps.cases.api import AppealViewSet, ApplicationViewSet, DecisionViewSet
from apps.core.api import (
    AuditEventViewSet,
    MyAppealCreateView,
    MyApplicationsView,
    MyProfileView,
    OrganizationViewSet,
    OverviewReportView,
    TerritoryViewSet,
)
from apps.programs.api import ProgramViewSet
from apps.scoring.api import ScoreResultViewSet
from apps.surveys.api import CollectSubmissionView, QuestionnaireViewSet, SurveyResponseViewSet

router = DefaultRouter()
router.register("organizations", OrganizationViewSet, basename="organization")
router.register("territories", TerritoryViewSet, basename="territory")
router.register("persons", PersonViewSet, basename="person")
router.register("households", HouseholdViewSet, basename="household")
router.register("questionnaires", QuestionnaireViewSet, basename="questionnaire")
router.register("survey-responses", SurveyResponseViewSet, basename="survey-response")
router.register("scores", ScoreResultViewSet, basename="score")
router.register("programs", ProgramViewSet, basename="program")
router.register("applications", ApplicationViewSet, basename="application")
router.register("decisions", DecisionViewSet, basename="decision")
router.register("appeals", AppealViewSet, basename="appeal")
router.register("audit-events", AuditEventViewSet, basename="audit-event")


def health(_request):
    return JsonResponse({"status": "ok", "service": "lanfiasave-api"})


api_v1 = [
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/beneficiary/login/", BeneficiaryLoginView.as_view(), name="beneficiary-login"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("auth/me/", MeView.as_view(), name="me"),
    path("collect/submissions/", CollectSubmissionView.as_view(), name="collect-submission"),
    path("reports/overview/", OverviewReportView.as_view(), name="report-overview"),
    path("me/profile/", MyProfileView.as_view(), name="my-profile"),
    path("me/applications/", MyApplicationsView.as_view(), name="my-applications"),
    path("me/appeals/", MyAppealCreateView.as_view(), name="my-appeals"),
    path("", include(router.urls)),
]

urlpatterns = [
    path("health/", health),
    path("admin/", admin.site.urls),
    path("api/v1/", include(api_v1)),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]
