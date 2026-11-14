from django.contrib import admin
from django.db import connection, transaction
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from apps.accounts.admin_api import AdminOrganizationViewSet, AdminUserViewSet, ChangePasswordView, RolesCatalogView
from apps.accounts.api import BeneficiaryLoginView, LoginView, MeView
from apps.assistance.api import AssistanceViewSet
from apps.beneficiaries.api import HouseholdViewSet, PersonViewSet, VulnerabilityViewSet
from apps.cases.api import AppealViewSet, ApplicationViewSet, DecisionViewSet
from apps.core.api import (
    AuditEventViewSet,
    CatalogsView,
    MyAppealCreateView,
    MyApplicationsView,
    MyAssistanceView,
    MyCasesView,
    MyProfileView,
    OrganizationViewSet,
    OverviewReportView,
    TerritoryViewSet,
)
from apps.followup.api import (
    GrievanceViewSet,
    MyGrievanceFeedbackView,
    MyGrievancesView,
    ReferralViewSet,
    TerritoryReportView,
    VisitViewSet,
)
from apps.operations.api import OperationCaseViewSet, OperationViewSet, PartnerViewSet
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
router.register("vulnerabilities", VulnerabilityViewSet, basename="vulnerability")
router.register("partners", PartnerViewSet, basename="partner")
router.register("operations", OperationViewSet, basename="operation")
router.register("operation-cases", OperationCaseViewSet, basename="operation-case")
router.register("assistances", AssistanceViewSet, basename="assistance")
router.register("grievances", GrievanceViewSet, basename="grievance")
router.register("referrals", ReferralViewSet, basename="referral")
router.register("visits", VisitViewSet, basename="visit")
router.register("admin/users", AdminUserViewSet, basename="admin-user")
router.register("admin/organizations", AdminOrganizationViewSet, basename="admin-organization")


@transaction.non_atomic_requests
def health(_request):
    """État du service et de la base, sans jamais exposer de secret. 503 si la base est injoignable."""
    from django.conf import settings
    from django.db.migrations.executor import MigrationExecutor

    body = {"service": "lanfiasave-api", "database_source": settings.DATABASE_URL_SOURCE or "POSTGRES_*"}
    try:
        connection.ensure_connection()
        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        body.update(status="ok" if not pending else "migrations_pending", database="ok",
                    pending_migrations=len(pending))
        return JsonResponse(body, status=200 if not pending else 503)
    except Exception as exc:  # noqa: BLE001 — diagnostic volontairement large
        body.update(status="error", database="unreachable", error=type(exc).__name__)
        return JsonResponse(body, status=503)


api_v1 = [
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/beneficiary/login/", BeneficiaryLoginView.as_view(), name="beneficiary-login"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("auth/me/", MeView.as_view(), name="me"),
    path("auth/change-password/", ChangePasswordView.as_view(), name="change-password"),
    path("admin/roles/", RolesCatalogView.as_view(), name="admin-roles"),
    path("catalogs/", CatalogsView.as_view(), name="catalogs"),
    path("me/assistances/", MyAssistanceView.as_view(), name="my-assistances"),
    path("me/cases/", MyCasesView.as_view(), name="my-cases"),
    path("collect/submissions/", CollectSubmissionView.as_view(), name="collect-submission"),
    path("reports/overview/", OverviewReportView.as_view(), name="report-overview"),
    path("reports/territories/", TerritoryReportView.as_view(), name="report-territories"),
    path("me/profile/", MyProfileView.as_view(), name="my-profile"),
    path("me/applications/", MyApplicationsView.as_view(), name="my-applications"),
    path("me/appeals/", MyAppealCreateView.as_view(), name="my-appeals"),
    path("me/grievances/", MyGrievancesView.as_view(), name="my-grievances"),
    path("me/grievances/<uuid:pk>/feedback/", MyGrievanceFeedbackView.as_view(), name="my-grievance-feedback"),
    path("", include(router.urls)),
]

urlpatterns = [
    path("health/", health),
    path("admin/", admin.site.urls),
    path("api/v1/", include(api_v1)),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]
