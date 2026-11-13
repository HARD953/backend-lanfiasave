from django.db.models import Count, Max
from django.utils import timezone
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.audit import audit
from apps.core.exceptions import IllegalTransition
from apps.core.permissions import OrgScopedViewSetMixin

from .models import EligibilityRuleVersion, Program


class RuleVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = EligibilityRuleVersion
        fields = ["id", "version", "effective_from", "rules", "description", "created_at"]
        read_only_fields = ["version"]
        extra_kwargs = {"effective_from": {"required": False}}


class ProgramSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    sector_label = serializers.CharField(source="get_sector_display", read_only=True)
    applications_count = serializers.IntegerField(read_only=True, default=0)
    current_rule = serializers.SerializerMethodField()

    class Meta:
        model = Program
        fields = ["id", "organization", "code", "name", "description", "sector", "sector_label", "status",
                  "status_label", "start_date", "end_date", "budget_amount", "currency", "target_beneficiaries",
                  "territories", "eligibility_summary", "funders", "applications_count", "current_rule",
                  "created_at"]
        read_only_fields = ["status"]

    def get_current_rule(self, obj):
        rule = obj.rule_versions.order_by("-version").first()
        return RuleVersionSerializer(rule).data if rule else None


class ProgramViewSet(OrgScopedViewSetMixin, mixins.CreateModelMixin, mixins.RetrieveModelMixin,
                     mixins.UpdateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = (Program.objects.annotate(applications_count=Count("applications"))
                .prefetch_related("territories").order_by("name"))
    serializer_class = ProgramSerializer
    perms = {"default": "program.view", "create": "program.manage", "update": "program.manage",
             "partial_update": "program.manage", "transition": "program.manage", "rules": "program.view",
             "add_rule": "program.manage"}
    program_field = "id"
    filterset_fields = ["status", "sector", "organization"]
    search_fields = ["name", "code"]

    def perform_create(self, serializer):
        self.check_create_scope(serializer.validated_data["organization"].pk)
        program = serializer.save()
        audit("program.created", obj=program)

    def perform_update(self, serializer):
        program = serializer.save()
        audit("program.updated", obj=program, data={"fields": list(serializer.validated_data)})

    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        program = self.get_object()
        to = request.data.get("to")
        if to not in Program.TRANSITIONS.get(program.status, set()):
            raise IllegalTransition(f"Passage de « {program.get_status_display()} » à « {to} » impossible.")
        if to == "active" and not program.rule_versions.exists():
            raise IllegalTransition("Un programme ne peut être activé sans règle d'éligibilité publiée.")
        previous = program.status
        program.status = to
        program.save(update_fields=["status", "updated_at"])
        audit("program.status_changed", obj=program, reason=request.data.get("reason", ""),
              data={"from": previous, "to": to})
        return Response(self.get_serializer(program).data)

    @action(detail=True, methods=["get"])
    def rules(self, request, pk=None):
        return Response(RuleVersionSerializer(self.get_object().rule_versions.all(), many=True).data)

    @rules.mapping.post
    def add_rule(self, request, pk=None):
        program = self.get_object()
        ser = RuleVersionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        next_version = (program.rule_versions.aggregate(m=Max("version"))["m"] or 0) + 1
        rule = ser.save(program=program, version=next_version, created_by=request.user,
                        effective_from=ser.validated_data.get("effective_from") or timezone.localdate())
        audit("program.rule_published", obj=program, data={"version": next_version})
        return Response(RuleVersionSerializer(rule).data, status=201)
