from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.audit import audit
from apps.core.permissions import OrgScopedViewSetMixin, require_org_perm

from . import services
from .models import Appeal, Application, Decision, StatusChange


class StatusChangeSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.get_full_name", read_only=True, default=None)

    class Meta:
        model = StatusChange
        fields = ["from_status", "to_status", "reason", "actor_name", "created_at"]


class DecisionSerializer(serializers.ModelSerializer):
    outcome_label = serializers.CharField(source="get_outcome_display", read_only=True)
    decided_by_name = serializers.CharField(source="decided_by.get_full_name", read_only=True)
    is_current = serializers.BooleanField(read_only=True)
    person_name = serializers.CharField(source="application.person.display_name", read_only=True)
    program_name = serializers.CharField(source="application.program.name", read_only=True)

    class Meta:
        model = Decision
        fields = ["id", "application", "person_name", "program_name", "outcome", "outcome_label", "motive",
                  "internal_notes", "decided_by_name", "committee_members", "quorum_met", "rule_version",
                  "score_snapshot", "supersedes", "is_current", "notified_at", "created_at"]


class AppealSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    filed_by_name = serializers.CharField(source="filed_by.get_full_name", read_only=True, default=None)
    person_name = serializers.CharField(source="decision.application.person.display_name", read_only=True)

    class Meta:
        model = Appeal
        fields = ["id", "decision", "person_name", "motive", "filed_via", "filed_by_name", "status", "status_label",
                  "due_date", "resolution", "resolved_at", "created_at"]
        read_only_fields = ["status", "due_date", "resolution", "resolved_at", "filed_via"]


class ApplicationSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    person_name = serializers.CharField(source="person.display_name", read_only=True)
    person_code = serializers.CharField(source="person.public_code", read_only=True)
    program_name = serializers.CharField(source="program.name", read_only=True)
    score_summary = serializers.SerializerMethodField()
    current_decision = serializers.SerializerMethodField()
    history = StatusChangeSerializer(many=True, read_only=True)

    class Meta:
        model = Application
        fields = ["id", "organization", "program", "program_name", "person", "person_name", "person_code",
                  "rule_version", "status", "status_label", "status_reason", "score", "score_summary",
                  "social_opinion", "assigned_to", "current_decision", "history", "created_at", "updated_at"]
        read_only_fields = ["organization", "status", "status_reason", "rule_version"]

    def validate(self, attrs):
        person, program = attrs.get("person"), attrs.get("program")
        if person and program and person.organization_id != program.organization_id:
            raise serializers.ValidationError("La personne et le programme doivent relever de la même organisation.")
        if program and program.status != "active" and not self.instance:
            raise serializers.ValidationError({"program": "Le programme n'est pas ouvert aux candidatures."})
        score = attrs.get("score")
        if score and person and score.person_id != person.pk:
            raise serializers.ValidationError({"score": "Ce score ne concerne pas cette personne."})
        return attrs

    def get_score_summary(self, obj):
        s = obj.score
        return ({"total": s.total, "band": s.band, "completeness": float(s.completeness),
                 "is_reliable": s.is_reliable} if s else None)

    def get_current_decision(self, obj):
        d = obj.decisions.filter(superseded_by__isnull=True).first()
        return DecisionSerializer(d).data if d else None


class ApplicationViewSet(OrgScopedViewSetMixin, mixins.CreateModelMixin, mixins.RetrieveModelMixin,
                         mixins.UpdateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = (Application.objects.select_related("person", "program", "score", "score__scoring_version")
                .prefetch_related("history", "decisions"))
    serializer_class = ApplicationSerializer
    perms = {"default": "application.view", "create": "application.create", "update": "application.review",
             "partial_update": "application.review", "transition": "application.review",
             "decide": "decision.create"}
    program_field = "program"
    filterset_fields = ["status", "program", "person", "organization"]
    search_fields = ["person__search_name", "person__public_code"]

    def perform_create(self, serializer):
        program = serializer.validated_data["program"]
        self.check_create_scope(program.organization_id)
        rule = program.rule_versions.order_by("-version").first()
        app = serializer.save(organization=program.organization, submitted_by=self.request.user, rule_version=rule)
        audit("application.created", obj=app)

    def perform_update(self, serializer):
        allowed = {"social_opinion", "score", "assigned_to"}
        extra = set(serializer.validated_data) - allowed
        if extra:
            raise serializers.ValidationError({f: "Champ non modifiable après dépôt." for f in extra})
        app = serializer.save()
        audit("application.updated", obj=app, data={"fields": list(serializer.validated_data)})

    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        app = services.transition_application(self.get_object(), request.data.get("to"), request.user,
                                              request.data.get("reason", ""))
        return Response(self.get_serializer(app).data)

    @action(detail=True, methods=["post"])
    def decide(self, request, pk=None):
        app = self.get_object()
        decision = services.record_decision(
            app, request.user, outcome=request.data.get("outcome"), motive=request.data.get("motive", ""),
            committee_members=request.data.get("committee_members") or [],
            quorum_met=bool(request.data.get("quorum_met", True)),
            internal_notes=request.data.get("internal_notes", ""))
        return Response(DecisionSerializer(decision).data, status=status.HTTP_201_CREATED)


class DecisionViewSet(OrgScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = Decision.objects.select_related("decided_by", "application__person", "application__program")
    serializer_class = DecisionSerializer
    perms = {"default": "application.view"}
    program_field = "application__program"
    filterset_fields = ["outcome", "application", "organization"]


class AppealViewSet(OrgScopedViewSetMixin, mixins.CreateModelMixin, mixins.RetrieveModelMixin,
                    mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = Appeal.objects.select_related("decision__application__person", "filed_by")
    serializer_class = AppealSerializer
    perms = {"default": "application.view", "create": "appeal.create", "resolve": "appeal.resolve"}
    program_field = "decision__application__program"
    filterset_fields = ["status", "organization"]

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        decision = ser.validated_data["decision"]
        require_org_perm(request.user, "appeal.create", decision.organization_id)
        appeal = services.file_appeal(decision, request.user, ser.validated_data["motive"])
        return Response(self.get_serializer(appeal).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        appeal = services.resolve_appeal(self.get_object(), request.user, upheld=bool(request.data.get("upheld")),
                                         resolution=request.data.get("resolution", ""))
        return Response(self.get_serializer(appeal).data)
