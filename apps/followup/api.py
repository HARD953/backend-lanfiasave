from datetime import timedelta

from django.db.models import Count, F, Max, Q, Sum
from django.utils import timezone
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import audit
from apps.core.exceptions import IllegalTransition
from apps.core.permissions import IsBeneficiary, IsStaffMember, OrgScopedViewSetMixin

from .models import Grievance, Referral, Visit

RECERTIFICATION_MONTHS = 24  # Mise à jour de la situation du ménage au moins tous les 2 ans.


def _same_org(attrs, org_id, fields):
    for field in fields:
        obj = attrs.get(field)
        if obj is not None and getattr(obj, "organization_id", org_id) != org_id:
            raise serializers.ValidationError({field: "Doit relever de la même organisation."})


def _transition(obj, to, allowed):
    if to not in obj.Status.values:
        raise serializers.ValidationError({"status": "Statut inconnu."})
    if to not in allowed.get(obj.status, set()):
        raise IllegalTransition(f"Passage de « {obj.get_status_display()} » à « {obj.Status(to).label} » impossible.")


# --- Plaintes -----------------------------------------------------------------

class GrievanceSerializer(serializers.ModelSerializer):
    category_label = serializers.CharField(source="get_category_display", read_only=True)
    channel_label = serializers.CharField(source="get_channel_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    priority_label = serializers.CharField(source="get_priority_display", read_only=True)
    person_name = serializers.CharField(source="person.display_name", read_only=True, default=None)
    person_code = serializers.CharField(source="person.public_code", read_only=True, default=None)
    operation_title = serializers.CharField(source="operation.title", read_only=True, default=None)
    territory_name = serializers.CharField(source="territory.name", read_only=True, default=None)
    assigned_to_name = serializers.SerializerMethodField()
    is_overdue = serializers.BooleanField(read_only=True)
    is_confidential = serializers.BooleanField(read_only=True)
    allowed_transitions = serializers.SerializerMethodField()

    class Meta:
        model = Grievance
        fields = ["id", "organization", "reference", "person", "person_name", "person_code", "operation",
                  "operation_title", "program", "territory", "territory_name", "anonymous", "complainant_name",
                  "complainant_phone", "channel", "channel_label", "category", "category_label", "priority",
                  "priority_label", "subject", "description", "status", "status_label", "due_date", "assigned_to",
                  "assigned_to_name", "escalated_to", "resolution", "resolved_at", "satisfaction", "is_overdue",
                  "is_confidential", "allowed_transitions", "created_at"]
        read_only_fields = ["reference", "status", "due_date", "resolution", "resolved_at", "escalated_to",
                            "satisfaction"]

    def get_assigned_to_name(self, obj):
        return obj.assigned_to.get_full_name() if obj.assigned_to_id else None

    def get_allowed_transitions(self, obj):
        return [{"value": s, "label": Grievance.Status(s).label} for s in Grievance.TRANSITIONS[obj.status]]

    def to_representation(self, obj):
        data = super().to_representation(obj)
        user = self.context["request"].user
        if obj.is_confidential and not user.has_org_perm("person.view_sensitive", obj.organization_id):
            for k in ("description", "complainant_name", "complainant_phone", "resolution", "person_name"):
                data[k] = "Confidentiel" if data.get(k) else data.get(k)
        if obj.anonymous:
            data["complainant_name"] = ""
            data["complainant_phone"] = ""
        return data

    def validate(self, attrs):
        org = attrs.get("organization") or getattr(self.instance, "organization", None)
        if org is not None:
            _same_org(attrs, org.pk, ["person", "operation", "program"])
        if attrs.get("anonymous"):
            attrs["person"] = None
            attrs["complainant_name"] = ""
            attrs["complainant_phone"] = ""
        return attrs


class GrievanceViewSet(OrgScopedViewSetMixin, mixins.CreateModelMixin, mixins.RetrieveModelMixin,
                       mixins.ListModelMixin, viewsets.GenericViewSet):
    """Mécanisme de gestion des plaintes : enregistrement multicanal, délai cible, escalade, résolution."""

    queryset = Grievance.objects.select_related("person", "operation", "territory", "assigned_to")
    serializer_class = GrievanceSerializer
    perms = {"default": "grievance.view", "create": "grievance.manage", "transition": "grievance.manage",
             "assign": "grievance.manage"}
    filterset_fields = ["organization", "status", "category", "priority", "channel", "person", "operation"]
    search_fields = ["reference", "subject", "person__search_name", "person__public_code"]
    ordering_fields = ["created_at", "due_date", "priority"]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get("open") == "true":
            qs = qs.filter(status__in=Grievance.OPEN)
        if self.request.query_params.get("overdue") == "true":
            qs = qs.filter(status__in=Grievance.OPEN, due_date__lt=timezone.localdate())
        return qs

    def perform_create(self, serializer):
        org = serializer.validated_data.get("organization")
        if org is None:
            raise serializers.ValidationError({"organization": "Organisation obligatoire."})
        self.check_create_scope(org.pk)
        g = serializer.save(recorded_by=self.request.user)
        audit("grievance.received", obj=g, data={"category": g.category, "priority": g.priority,
                                                 "channel": g.channel})

    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        g = self.get_object()
        to = request.data.get("status")
        _transition(g, to, Grievance.TRANSITIONS)
        note = (request.data.get("note") or "").strip()
        if to in {"resolved", "rejected"} and len(note) < 10:
            raise serializers.ValidationError({"note": "Expliquez la réponse apportée (10 caractères minimum)."})
        if to == "escalated":
            target = (request.data.get("escalated_to") or "").strip()
            if not target:
                raise serializers.ValidationError({"escalated_to": "Précisez l'instance saisie."})
            g.escalated_to = target
        if to in {"resolved", "rejected"}:
            g.resolution = note
            g.resolved_at = timezone.now()
        if to == "closed" and request.data.get("satisfaction"):
            g.satisfaction = request.data["satisfaction"]
        previous = g.status
        g.status = to
        g.save()
        audit("grievance.transition", obj=g, reason=note, data={"from": previous, "to": to})
        return Response(self.get_serializer(g).data)

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        from apps.accounts.models import User

        g = self.get_object()
        user = User.objects.filter(pk=request.data.get("user"), is_active=True).first()
        if user is None or not user.has_org_perm("grievance.manage", g.organization_id):
            raise serializers.ValidationError({"user": "Cette personne ne peut pas traiter les plaintes."})
        g.assigned_to = user
        g.save(update_fields=["assigned_to", "updated_at"])
        audit("grievance.assigned", obj=g, data={"user": str(user.pk)})
        return Response(self.get_serializer(g).data)

    @action(detail=False, methods=["get"])
    def summary(self, request):
        qs = self.filter_queryset(self.get_queryset())
        open_qs = qs.filter(status__in=Grievance.OPEN)
        done = qs.filter(resolved_at__isnull=False)
        on_time = done.filter(resolved_at__date__lte=F("due_date")).count() if done.exists() else 0
        return Response({
            "count": qs.count(),
            "open": open_qs.count(),
            "overdue": open_qs.filter(due_date__lt=timezone.localdate()).count(),
            "resolved": done.count(),
            "resolved_on_time_rate": round(100 * on_time / done.count()) if done.exists() else None,
            "by_category": dict(qs.values_list("category").annotate(n=Count("id"))),
            "by_channel": dict(qs.values_list("channel").annotate(n=Count("id"))),
        })


# --- Orientations -------------------------------------------------------------

class ReferralSerializer(serializers.ModelSerializer):
    service_label = serializers.CharField(source="get_service_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    person_name = serializers.CharField(source="person.display_name", read_only=True)
    person_code = serializers.CharField(source="person.public_code", read_only=True)
    partner_name = serializers.SerializerMethodField()
    referred_by_name = serializers.SerializerMethodField()
    allowed_transitions = serializers.SerializerMethodField()

    class Meta:
        model = Referral
        fields = ["id", "organization", "reference", "person", "person_name", "person_code", "operation_case",
                  "service", "service_label", "partner", "partner_name", "service_name", "reason", "urgent",
                  "status", "status_label", "appointment_date", "feedback", "closed_at", "referred_by_name",
                  "allowed_transitions", "created_at"]
        read_only_fields = ["organization", "reference", "status", "feedback", "closed_at"]

    def get_partner_name(self, obj):
        return str(obj.partner) if obj.partner_id else (obj.service_name or None)

    def get_referred_by_name(self, obj):
        return obj.referred_by.get_full_name() if obj.referred_by_id else None

    def get_allowed_transitions(self, obj):
        return [{"value": s, "label": Referral.Status(s).label} for s in Referral.TRANSITIONS[obj.status]]

    def validate(self, attrs):
        person = attrs.get("person") or getattr(self.instance, "person", None)
        _same_org(attrs, person.organization_id, ["partner", "operation_case"])
        case = attrs.get("operation_case")
        if case is not None and case.person_id != person.pk:
            raise serializers.ValidationError({"operation_case": "Ce dossier ne concerne pas cette personne."})
        if not attrs.get("partner") and not (attrs.get("service_name") or "").strip():
            raise serializers.ValidationError({"partner": "Indiquez la structure qui reçoit la personne."})
        return attrs


class ReferralViewSet(OrgScopedViewSetMixin, mixins.CreateModelMixin, mixins.RetrieveModelMixin,
                      mixins.ListModelMixin, viewsets.GenericViewSet):
    """Orientations vers les services et contre-référence (retour du service)."""

    queryset = Referral.objects.select_related("person", "partner", "referred_by")
    serializer_class = ReferralSerializer
    perms = {"default": "referral.view", "create": "referral.manage", "transition": "referral.manage"}
    filterset_fields = ["organization", "status", "service", "partner", "person", "urgent", "operation_case"]
    search_fields = ["reference", "person__search_name", "person__public_code", "service_name"]
    ordering_fields = ["created_at", "appointment_date"]

    def perform_create(self, serializer):
        person = serializer.validated_data["person"]
        self.check_create_scope(person.organization_id)
        r = serializer.save(organization=person.organization, referred_by=self.request.user)
        audit("referral.sent", obj=r, data={"service": r.service, "partner": str(r.partner_id or "")})

    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        r = self.get_object()
        to = request.data.get("status")
        _transition(r, to, Referral.TRANSITIONS)
        feedback = (request.data.get("feedback") or "").strip()
        if to in {"completed", "refused"} and len(feedback) < 5:
            raise serializers.ValidationError({"feedback": "Saisissez le retour du service (contre-référence)."})
        previous = r.status
        r.status = to
        if feedback:
            r.feedback = feedback
        r.closed_at = timezone.now() if to not in Referral.OPEN else None
        r.save()
        audit("referral.transition", obj=r, reason=feedback, data={"from": previous, "to": to})
        return Response(self.get_serializer(r).data)

    @action(detail=False, methods=["get"])
    def summary(self, request):
        qs = self.filter_queryset(self.get_queryset())
        stale = qs.filter(status__in=Referral.OPEN, created_at__lt=timezone.now() - timedelta(days=30))
        closed = qs.exclude(status__in=Referral.OPEN).exclude(status="cancelled")
        completed = closed.filter(status="completed").count()
        return Response({
            "count": qs.count(),
            "open": qs.filter(status__in=Referral.OPEN).count(),
            "completed": completed,
            "without_feedback_30d": stale.count(),
            "completion_rate": round(100 * completed / closed.count()) if closed.exists() else None,
            "by_service": dict(qs.values_list("service").annotate(n=Count("id"))),
        })


# --- Visites ------------------------------------------------------------------

class VisitSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    trend_label = serializers.CharField(source="get_trend_display", read_only=True)
    person_name = serializers.CharField(source="person.display_name", read_only=True)
    person_code = serializers.CharField(source="person.public_code", read_only=True)
    visited_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Visit
        fields = ["id", "organization", "person", "person_name", "person_code", "household", "operation_case",
                  "kind", "kind_label", "visited_at", "person_present", "trend", "trend_label", "summary",
                  "needs_identified", "next_visit_date", "visited_by_name", "created_at"]
        read_only_fields = ["organization", "household"]

    def get_visited_by_name(self, obj):
        return obj.visited_by.get_full_name() if obj.visited_by_id else None

    def validate(self, attrs):
        person = attrs.get("person") or getattr(self.instance, "person", None)
        _same_org(attrs, person.organization_id, ["operation_case"])
        if attrs.get("visited_at") and attrs["visited_at"] > timezone.localdate():
            raise serializers.ValidationError({"visited_at": "Une visite ne peut pas être datée dans le futur."})
        nxt = attrs.get("next_visit_date")
        if nxt and attrs.get("visited_at") and nxt <= attrs["visited_at"]:
            raise serializers.ValidationError({"next_visit_date": "Doit être postérieure à la visite."})
        return attrs


class VisitViewSet(OrgScopedViewSetMixin, mixins.CreateModelMixin, mixins.RetrieveModelMixin,
                   mixins.ListModelMixin, viewsets.GenericViewSet):
    """Visites de suivi. Une visite enregistrée n'est pas modifiable (nouvelle visite = nouvel enregistrement)."""

    queryset = Visit.objects.select_related("person", "visited_by")
    serializer_class = VisitSerializer
    perms = {"default": "visit.view", "create": "visit.record", "agenda": "visit.view"}
    filterset_fields = ["organization", "person", "kind", "trend", "operation_case"]
    search_fields = ["person__search_name", "person__public_code"]
    ordering_fields = ["visited_at", "next_visit_date"]

    def perform_create(self, serializer):
        person = serializer.validated_data["person"]
        self.check_create_scope(person.organization_id)
        m = person.current_household_membership()
        v = serializer.save(organization=person.organization, visited_by=self.request.user,
                            household=m.household if m else None)
        case = v.operation_case
        if case is not None:
            case.last_contact_at = timezone.now()
            case.save(update_fields=["last_contact_at", "updated_at"])
        audit("visit.recorded", obj=v, data={"kind": v.kind, "trend": v.trend})

    @action(detail=False, methods=["get"])
    def agenda(self, request):
        """Visites à faire : prochaines visites prévues (et en retard) + ménages à revisiter (> 24 mois)."""
        from apps.beneficiaries.models import Household

        qs = self.filter_queryset(self.get_queryset())
        today = timezone.localdate()
        horizon = today + timedelta(days=int(request.query_params.get("days", 14)))
        # Dernière visite de chaque personne : seule sa « prochaine visite » compte.
        latest = {}
        for v in qs.order_by("person_id", "-visited_at", "-created_at"):
            latest.setdefault(v.person_id, v)
        due = sorted((v for v in latest.values() if v.next_visit_date and v.next_visit_date <= horizon),
                     key=lambda v: v.next_visit_date)
        orgs = request.user.organizations_with("visit.view")
        requested = request.query_params.get("organization")
        if requested:
            orgs = {o for o in orgs if str(o) == requested}
        limit = today - timedelta(days=30 * RECERTIFICATION_MONTHS)
        stale = (Household.objects.filter(organization__in=orgs)
                 .annotate(last_survey=Max("survey_responses__collected_at"))
                 .filter(Q(last_survey__lt=timezone.make_aware(
                     timezone.datetime.combine(limit, timezone.datetime.min.time())))
                         | Q(last_survey__isnull=True))
                 .select_related("territory").order_by("last_survey")[:50])
        return Response({
            "today": today,
            "visits_due": [{**VisitSerializer(v, context={"request": request}).data,
                            "overdue": v.next_visit_date < today} for v in due[:100]],
            "recertification_months": RECERTIFICATION_MONTHS,
            "households_to_recertify": [{"id": str(h.id), "public_code": h.public_code,
                                         "territory": h.territory.name if h.territory_id else None,
                                         "last_survey": h.last_survey} for h in stale],
        })


# --- Carte des vulnérabilités par territoire ------------------------------------

MIN_CELL = 3  # En dessous, les effectifs sont masqués (protection contre la ré-identification).


class TerritoryReportView(APIView):
    """Indicateurs agrégés par localité : ménages, personnes, vulnérabilités, aides, plaintes, opérations."""

    permission_classes = [IsStaffMember]

    def get(self, request):
        from apps.assistance.models import Assistance
        from apps.beneficiaries.models import Household, HouseholdMembership, PersonVulnerability
        from apps.operations.models import OperationCase

        orgs = request.user.organizations_with("report.aggregate")
        requested = request.query_params.get("organization")
        if requested:
            orgs = {o for o in orgs if str(o) == requested}
        rows = {}

        def row(tid, name, level):
            return rows.setdefault(tid, {"territory": str(tid) if tid else None, "name": name or "Non localisé",
                                         "level": level, "households": 0, "persons": 0, "vulnerable_persons": 0,
                                         "aids_delivered": 0, "aid_amount": 0, "open_cases": 0,
                                         "open_grievances": 0})

        for t, name, level, n in (Household.objects.filter(organization__in=orgs)
                                  .values_list("territory", "territory__name", "territory__level")
                                  .annotate(n=Count("id"))):
            row(t, name, level)["households"] = n
        memberships = HouseholdMembership.objects.filter(household__organization__in=orgs, end_date__isnull=True)
        for t, name, level, n in (memberships.values_list("household__territory", "household__territory__name",
                                                          "household__territory__level")
                                  .annotate(n=Count("person", distinct=True))):
            row(t, name, level)["persons"] = n
        vulnerable = PersonVulnerability.objects.filter(organization__in=orgs, status__in=["suspected", "confirmed"])
        vuln_persons = vulnerable.values("person")
        for t, name, level, n in (memberships.filter(person__in=vuln_persons)
                                  .values_list("household__territory", "household__territory__name",
                                               "household__territory__level")
                                  .annotate(n=Count("person", distinct=True))):
            row(t, name, level)["vulnerable_persons"] = n
        for t, name, level, n, s in (Assistance.objects.filter(organization__in=orgs, status="delivered")
                                     .values_list("household__territory", "household__territory__name",
                                                  "household__territory__level")
                                     .annotate(n=Count("id"), s=Sum("amount"))):
            r = row(t, name, level)
            r["aids_delivered"] = n
            r["aid_amount"] = s or 0
        for t, name, level, n in (OperationCase.objects.filter(organization__in=orgs)
                                  .exclude(status__in=["closed", "lost_contact"])
                                  .values_list("operation__territory", "operation__territory__name",
                                               "operation__territory__level")
                                  .annotate(n=Count("id"))):
            row(t, name, level)["open_cases"] = n
        for t, name, level, n in (Grievance.objects.filter(organization__in=orgs, status__in=Grievance.OPEN)
                                  .values_list("territory", "territory__name", "territory__level")
                                  .annotate(n=Count("id"))):
            row(t, name, level)["open_grievances"] = n

        result = []
        for r in rows.values():
            r["vulnerability_rate"] = round(100 * r["vulnerable_persons"] / r["persons"]) if r["persons"] else None
            r["suppressed"] = 0 < r["persons"] < MIN_CELL
            if r["suppressed"]:
                r["persons"] = r["vulnerable_persons"] = None
                r["vulnerability_rate"] = None
            result.append(r)
        result.sort(key=lambda r: (-(r["vulnerable_persons"] or 0), -(r["households"] or 0)))
        return Response({"min_cell": MIN_CELL, "territories": result})


# ---------------------------------------------------------------------------
# Côté bénéficiaire (app Mon Parcours) : « Signaler un problème »
# ---------------------------------------------------------------------------
# Catégories proposées dans l'app, en langage simple (pas « inclusion indue » côté personne).
BENEFICIARY_CATEGORIES = [
    ("payment_delay", "Mon aide est en retard"),
    ("payment_amount", "Mon aide est incomplète"),
    ("compensation", "Mon indemnisation"),
    ("relocation", "Mon relogement / le site d'accueil"),
    ("data_correction", "Une erreur dans mon dossier"),
    ("exclusion", "Un proche n'a pas été recensé"),
    ("service_quality", "Mauvais accueil / service"),
    ("staff_conduct", "Comportement d'un agent"),
    ("fraud", "On m'a demandé de l'argent"),
    ("sea", "Abus ou harcèlement sexuel"),
    ("information", "J'ai une question"),
    ("other", "Autre problème"),
]
BENEFICIARY_PRIORITY = {"sea": Grievance.Priority.URGENT, "fraud": Grievance.Priority.HIGH,
                        "staff_conduct": Grievance.Priority.HIGH, "relocation": Grievance.Priority.HIGH}
MAX_OPEN_FROM_APP = 5  # anti-abus : au-delà, la personne est invitée à contacter son agent.


def _public_grievance(g):
    answered = g.status in {"resolved", "rejected", "closed"}
    return {
        "id": str(g.id), "reference": g.reference, "category": g.category,
        "category_label": dict(BENEFICIARY_CATEGORIES).get(g.category, g.get_category_display()),
        "subject": g.subject, "status": g.status,
        "status_label": "Réponse donnée" if answered else ("En cours de traitement"
                                                            if g.status != "received" else "Reçue"),
        "answered": answered, "created_at": g.created_at, "due_date": g.due_date,
        "resolution": g.resolution if answered else "", "satisfaction": g.satisfaction,
    }


class MyGrievanceSerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=BENEFICIARY_CATEGORIES)
    description = serializers.CharField(min_length=10, max_length=3000, trim_whitespace=True, error_messages={
        "min_length": "Décrivez le problème en quelques mots (10 caractères minimum)."})
    callback = serializers.BooleanField(default=False)


class MyGrievancesView(APIView):
    """Plaintes déposées par le bénéficiaire lui-même. Il ne voit que les siennes, sans notes internes."""

    permission_classes = [IsAuthenticated, IsBeneficiary]

    def get(self, request):
        qs = Grievance.objects.filter(person=request.user.beneficiary).order_by("-created_at")
        return Response({"results": [_public_grievance(g) for g in qs],
                         "categories": [{"value": v, "label": label} for v, label in BENEFICIARY_CATEGORIES]})

    def post(self, request):
        person = request.user.beneficiary
        ser = MyGrievanceSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        open_count = Grievance.objects.filter(person=person, channel=Grievance.Channel.APP,
                                              status__in=Grievance.OPEN).count()
        if open_count >= MAX_OPEN_FROM_APP:
            raise serializers.ValidationError({"category": "Vous avez déjà plusieurs signalements en cours. "
                                                           "Contactez votre agent pour en ajouter un autre."})
        cat = ser.validated_data["category"]
        description = ser.validated_data["description"]
        if ser.validated_data["callback"]:
            description += "\n\n[La personne souhaite être rappelée.]"
        g = Grievance.objects.create(
            organization=person.organization, person=person, territory=person.territory,
            channel=Grievance.Channel.APP, category=cat,
            priority=BENEFICIARY_PRIORITY.get(cat, Grievance.Priority.NORMAL),
            subject=dict(BENEFICIARY_CATEGORIES)[cat], description=description,
            recorded_by=request.user,
        )
        audit("grievance.received", obj=g, data={"category": g.category, "priority": g.priority, "channel": "app"})
        return Response(_public_grievance(g), status=status.HTTP_201_CREATED)


class MyGrievanceFeedbackView(APIView):
    """La personne dit si la réponse lui convient (boucle de retour, comme Inua Jamii / 4Ps)."""

    permission_classes = [IsAuthenticated, IsBeneficiary]

    def post(self, request, pk):
        g = Grievance.objects.filter(pk=pk, person=request.user.beneficiary).first()
        if g is None:
            return Response({"error": {"code": "not_found", "message": "Signalement introuvable."}},
                            status=status.HTTP_404_NOT_FOUND)
        value = request.data.get("satisfaction")
        if value not in {"satisfied", "partly", "unsatisfied"}:
            raise serializers.ValidationError({"satisfaction": "Choix invalide."})
        if g.status not in {"resolved", "rejected", "closed"}:
            raise serializers.ValidationError({"satisfaction": "Aucune réponse à évaluer pour l'instant."})
        g.satisfaction = value
        g.save(update_fields=["satisfaction", "updated_at"])
        audit("grievance.feedback", obj=g, data={"satisfaction": value})
        return Response(_public_grievance(g))


def catalogs_payload():
    def ch(choices):
        return [{"value": v, "label": label} for v, label in choices]

    return {
        "grievance_categories": ch(Grievance.Category.choices),
        "grievance_channels": ch(Grievance.Channel.choices),
        "grievance_priorities": ch(Grievance.Priority.choices),
        "grievance_statuses": ch(Grievance.Status.choices),
        "referral_services": ch(Referral.Service.choices),
        "referral_statuses": ch(Referral.Status.choices),
        "visit_kinds": ch(Visit.Kind.choices),
        "visit_trends": ch(Visit.Trend.choices),
    }
