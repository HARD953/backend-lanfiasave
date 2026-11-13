"""Référentiels, journal d'audit, indicateurs agrégés et espace bénéficiaire."""
from django.db.models import Count, Q
from rest_framework import serializers, status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.beneficiaries.models import Household, Person
from apps.cases.models import Appeal, Application, Decision
from apps.cases.services import file_appeal
from apps.organizations.models import Organization
from apps.programs.models import Program
from apps.scoring.models import ScoreResult
from apps.territories.models import Territory

from .models import AuditEvent
from .permissions import IsBeneficiary, IsStaffMember, OrgScopedViewSetMixin

# --- Référentiels -----------------------------------------------------------


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "code", "name", "kind", "country", "status", "health_coverage_label"]


class OrganizationViewSet(viewsets.ReadOnlyModelViewSet):
    """Organisations dont l'utilisateur est membre."""

    serializer_class = OrganizationSerializer
    permission_classes = [IsStaffMember]
    pagination_class = None

    def get_queryset(self):
        return Organization.objects.filter(memberships__user=self.request.user,
                                           memberships__is_active=True).distinct()


class TerritorySerializer(serializers.ModelSerializer):
    level_label = serializers.CharField(source="get_level_display", read_only=True)
    parent_name = serializers.CharField(source="parent.name", read_only=True, default=None)

    class Meta:
        model = Territory
        fields = ["id", "code", "name", "level", "level_label", "parent", "parent_name", "country", "status"]


class TerritoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Territory.objects.filter(status="active").select_related("parent")
    serializer_class = TerritorySerializer
    permission_classes = [IsStaffMember]
    filterset_fields = ["level", "parent", "country"]
    search_fields = ["name", "code"]
    pagination_class = None


# --- Audit ------------------------------------------------------------------


class AuditEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = AuditEvent
        fields = ["id", "occurred_at", "actor_name", "action", "outcome", "object_type", "object_id", "reason",
                  "data", "ip_address"]

    def get_actor_name(self, obj):
        return (obj.actor.get_full_name() or obj.actor.username) if obj.actor else "Système"


class AuditEventViewSet(OrgScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = AuditEvent.objects.select_related("actor")
    serializer_class = AuditEventSerializer
    perms = {"default": "audit.view"}
    filterset_fields = ["action", "outcome", "object_id", "organization"]


# --- Indicateurs agrégés (RPT-01 : chiffres réels, périmètre explicite) -----


class OverviewReportView(APIView):
    permission_classes = [IsStaffMember]

    def get(self, request):
        orgs = request.user.organizations_with("report.aggregate")
        requested = request.query_params.get("organization")
        if requested:
            orgs = {o for o in orgs if str(o) == requested}
        persons = Person.objects.filter(organization__in=orgs, merged_into__isnull=True)
        apps = Application.objects.filter(organization__in=orgs)
        latest_scores = (ScoreResult.objects.filter(organization__in=orgs)
                         .order_by("person_id", "-created_at").distinct("person_id"))
        bands = {}
        for s in latest_scores:
            bands[s.band] = bands.get(s.band, 0) + 1
        return Response({
            "scope": {"organizations": [str(o) for o in orgs], "generated_from": "base de données en direct",
                      "synthetic_data": False},
            "persons": {
                "total": persons.count(),
                "provisional": persons.filter(is_provisional=True).count(),
                "women": persons.filter(sex="F").count(),
                "by_status": dict(persons.values_list("status").annotate(n=Count("id"))),
            },
            "households": Household.objects.filter(organization__in=orgs).count(),
            "programs": {"active": Program.objects.filter(organization__in=orgs, status="active").count(),
                         "total": Program.objects.filter(organization__in=orgs).count()},
            "applications": {
                "total": apps.count(),
                "by_status": dict(apps.values_list("status").annotate(n=Count("id"))),
            },
            "decisions": dict(Decision.objects.filter(organization__in=orgs, superseded_by__isnull=True)
                              .values_list("outcome").annotate(n=Count("id"))),
            "appeals_open": Appeal.objects.filter(organization__in=orgs,
                                                  status__in=["open", "under_review"]).count(),
            "score_bands": bands,
            "duplicates_open": Person.objects.filter(organization__in=orgs).filter(
                Q(duplicate_candidates__status="open")).distinct().count(),
            **_vulnerability_and_aid_indicators(orgs),
        })


def _vulnerability_and_aid_indicators(orgs):
    from django.db.models import Sum

    from apps.assistance.models import Assistance
    from apps.beneficiaries.models import PersonVulnerability
    from apps.beneficiaries.vulnerabilities import BY_CODE, SENSITIVE_CODES
    from apps.operations.models import Operation, OperationCase

    vulns = (PersonVulnerability.objects.filter(organization__in=orgs).exclude(status="resolved")
             .exclude(category__in=SENSITIVE_CODES).values_list("category").annotate(n=Count("person", distinct=True))
             .order_by("-n"))
    ops = Operation.objects.filter(organization__in=orgs)
    cases = OperationCase.objects.filter(organization__in=orgs)
    aids = Assistance.objects.filter(organization__in=orgs)
    delivered = aids.filter(status="delivered")
    helped_cases = cases.filter(assistances__status="delivered").distinct().count()
    return {
        "vulnerabilities": [{"code": c, "label": BY_CODE[c].label if c in BY_CODE else c, "persons": n}
                            for c, n in vulns[:12]],
        "operations": {
            "active": ops.exclude(status="closed").count(),
            "total": ops.count(),
            "affected_persons": cases.count(),
            "affected_without_aid": cases.count() - helped_cases,
            "lost_contact": cases.filter(status="lost_contact").count(),
        },
        "assistance": {
            "delivered": delivered.count(),
            "planned": aids.filter(status="planned").count(),
            "delivered_amount": delivered.aggregate(s=Sum("amount"))["s"] or 0,
            "persons_helped": delivered.values("person").distinct().count(),
            "by_category": dict(delivered.values_list("category").annotate(n=Count("id"))),
        },
    }


class CatalogsView(APIView):
    """Référentiels affichés par le portail et les applications (libellés, listes de choix)."""

    permission_classes = [IsStaffMember]

    def get(self, request):
        from apps.assistance.models import Assistance
        from apps.beneficiaries.vulnerabilities import catalog_payload
        from apps.operations.models import Operation, OperationCase, Partner

        def ch(choices):
            return [{"value": v, "label": label} for v, label in choices]

        return Response({
            "vulnerabilities": catalog_payload(bool(request.user.organizations_with("person.view_sensitive"))),
            "assistance_categories": ch(Assistance.Category.choices),
            "delivery_channels": ch(Assistance._meta.get_field("delivery_channel").choices),
            "operation_kinds": ch(Operation.Kind.choices),
            "operation_statuses": ch(Operation.Status.choices),
            "case_statuses": ch(OperationCase.Status.choices),
            "case_needs": ch(OperationCase.NEEDS),
            "occupancy": ch(OperationCase.Occupancy.choices),
            "partner_kinds": ch(Partner.Kind.choices),
        })


# --- Espace bénéficiaire « Mon Parcours » -----------------------------------
# Le bénéficiaire ne voit jamais : son score interne, les notes internes, les autres membres. (§16.2)


class MyProfileView(APIView):
    permission_classes = [IsAuthenticated, IsBeneficiary]

    def get(self, request):
        p = request.user.beneficiary
        m = p.current_household_membership()
        return Response({
            "public_code": p.public_code,
            "first_name": p.first_name,
            "last_name": p.last_name,
            "display_name": p.display_name,
            "organization": p.organization.name,
            "status": p.status,
            "status_label": p.get_status_display(),
            "is_provisional": p.is_provisional,
            "household_code": m.household.public_code if m else None,
            "territory": p.territory.name if p.territory else None,
        })


class MyApplicationsView(APIView):
    permission_classes = [IsAuthenticated, IsBeneficiary]

    def get(self, request):
        person = request.user.beneficiary
        out = []
        for app in Application.objects.filter(person=person).select_related("program").order_by("-created_at"):
            d = app.decisions.filter(superseded_by__isnull=True).first()
            appeal = d.appeals.order_by("-created_at").first() if d else None
            out.append({
                "id": str(app.id),
                "program": app.program.name,
                "program_description": app.program.description,
                "status": app.status,
                "status_label": app.get_status_display(),
                "status_reason": app.status_reason if app.status in {"inadmissible", "on_hold"} else "",
                "submitted_at": app.created_at,
                "steps": _public_steps(app),
                "decision": ({"id": str(d.id), "outcome": d.outcome, "outcome_label": d.get_outcome_display(),
                              "motive": d.motive, "date": d.created_at,
                              "can_appeal": appeal is None or appeal.status in {"dismissed", "upheld"}}
                             if d else None),
                "appeal": ({"status": appeal.status, "status_label": appeal.get_status_display(),
                            "due_date": appeal.due_date, "resolution": appeal.resolution}
                           if appeal else None),
            })
        return Response(out)


def _public_steps(app):
    order = ["submitted", "admissible", "under_review", "committee", "decided"]
    labels = {"submitted": "Demande reçue", "admissible": "Dossier recevable", "under_review": "Étude du dossier",
              "committee": "Passage en comité", "decided": "Décision rendue"}
    current = app.status if app.status in order else "submitted"
    idx = order.index(current)
    return [{"code": c, "label": labels[c], "done": i < idx or (i == idx and c == "decided"), "current": i == idx}
            for i, c in enumerate(order)]


class MyAppealCreateView(APIView):
    permission_classes = [IsAuthenticated, IsBeneficiary]

    def post(self, request):
        person = request.user.beneficiary
        decision = Decision.objects.filter(pk=request.data.get("decision"), application__person=person).first()
        if decision is None:
            return Response({"error": {"code": "not_found", "message": "Décision introuvable."}},
                            status=status.HTTP_404_NOT_FOUND)
        appeal = file_appeal(decision, request.user, request.data.get("motive", ""), via="beneficiary_app")
        return Response({"id": str(appeal.id), "status": appeal.status, "due_date": appeal.due_date},
                        status=status.HTTP_201_CREATED)


class MyAssistanceView(APIView):
    """Aides reçues ou prévues pour le bénéficiaire (sans montant planifié interne ni notes)."""

    permission_classes = [IsAuthenticated, IsBeneficiary]

    def get(self, request):
        from apps.assistance.models import Assistance

        qs = (Assistance.objects.filter(person=request.user.beneficiary).exclude(status="cancelled")
              .select_related("provider").order_by("-delivered_at", "-planned_date", "-created_at"))
        return Response([{
            "id": str(a.id), "category": a.category, "category_label": a.get_category_display(),
            "description": a.description, "status": a.status, "status_label": a.get_status_display(),
            "amount": int(a.amount) if a.amount is not None and a.status == "delivered" else None,
            "quantity": str(a.quantity) if a.quantity is not None else None, "unit": a.unit,
            "provider": str(a.provider) if a.provider else None,
            "date": a.delivered_at or a.planned_date,
        } for a in qs])


class MyCasesView(APIView):
    """Dossiers d'accompagnement du bénéficiaire (déguerpissement, sinistre…)."""

    permission_classes = [IsAuthenticated, IsBeneficiary]

    def get(self, request):
        from apps.operations.models import OperationCase

        labels = dict(OperationCase.NEEDS)
        qs = OperationCase.objects.filter(person=request.user.beneficiary).select_related("operation")
        return Response([{
            "id": str(c.id), "case_number": c.case_number, "operation": c.operation.title,
            "operation_kind": c.operation.get_kind_display(), "status": c.status,
            "status_label": c.get_status_display(), "needs": [labels.get(n, n) for n in c.needs],
            "relocation_site": c.relocation_site, "opened_at": c.created_at,
        } for c in qs])
