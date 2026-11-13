from django.db.models import Count
from django.shortcuts import get_object_or_404
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.beneficiaries.models import Person
from apps.core.audit import audit
from apps.core.permissions import OrgScopedViewSetMixin

from . import services
from .models import Operation, OperationCase, Partner


class PartnerSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = Partner
        fields = ["id", "organization", "name", "acronym", "kind", "kind_label", "contact_name", "phone", "email",
                  "notes", "is_active", "created_at"]


class PartnerViewSet(OrgScopedViewSetMixin, mixins.CreateModelMixin, mixins.RetrieveModelMixin,
                     mixins.UpdateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = Partner.objects.all()
    serializer_class = PartnerSerializer
    perms = {"default": "partner.view", "create": "partner.manage", "update": "partner.manage",
             "partial_update": "partner.manage"}
    filterset_fields = ["kind", "is_active", "organization"]
    search_fields = ["name", "acronym"]
    pagination_class = None

    def perform_create(self, serializer):
        self.check_create_scope(serializer.validated_data["organization"].pk)
        p = serializer.save()
        audit("partner.created", obj=p)

    def perform_update(self, serializer):
        p = serializer.save()
        audit("partner.updated", obj=p, data={"fields": list(serializer.validated_data)})


class OperationSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    initiated_by_name = serializers.CharField(source="initiated_by.__str__", read_only=True, default=None)
    territory_name = serializers.CharField(source="territory.name", read_only=True, default=None)
    partners_detail = PartnerSerializer(source="partners", many=True, read_only=True)
    persons_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Operation
        fields = ["id", "organization", "code", "title", "kind", "kind_label", "status", "status_label",
                  "description", "initiated_by", "initiated_by_name", "partners", "partners_detail", "territory",
                  "territory_name", "sites", "legal_reference", "notice_date", "start_date", "end_date",
                  "estimated_households", "compensation_policy", "compensation_amount_per_household",
                  "persons_count", "created_at"]
        read_only_fields = ["code", "status"]

    def validate(self, attrs):
        org = attrs.get("organization") or (self.instance.organization if self.instance else None)
        for p in ([attrs.get("initiated_by")] if attrs.get("initiated_by") else []) + list(attrs.get("partners", [])):
            if org and p.organization_id != org.pk:
                raise serializers.ValidationError("Les structures doivent appartenir à la même organisation.")
        return attrs


class OperationCaseSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    occupancy_label = serializers.CharField(source="get_occupancy_display", read_only=True)
    person_name = serializers.CharField(source="person.display_name", read_only=True)
    person_code = serializers.CharField(source="person.public_code", read_only=True)
    person_phone = serializers.SerializerMethodField()
    operation_title = serializers.CharField(source="operation.title", read_only=True)
    operation_code = serializers.CharField(source="operation.code", read_only=True)
    operation_kind = serializers.CharField(source="operation.get_kind_display", read_only=True)
    household_code = serializers.CharField(source="household.public_code", read_only=True, default=None)
    needs_labels = serializers.SerializerMethodField()
    aid_summary = serializers.SerializerMethodField()

    class Meta:
        model = OperationCase
        fields = ["id", "operation", "operation_title", "operation_code", "operation_kind", "person", "person_name",
                  "person_code", "person_phone", "household", "household_code", "case_number", "status",
                  "status_label", "occupancy", "occupancy_label", "lost_housing", "lost_livelihood",
                  "lost_documents", "children_schooling_disrupted", "lost_assets", "needs", "needs_labels",
                  "current_location", "relocation_site", "compensation_due", "notes", "assigned_to",
                  "last_contact_at", "aid_summary", "created_at"]
        read_only_fields = ["operation", "person", "household", "case_number", "status", "last_contact_at"]

    def get_needs_labels(self, obj):
        labels = dict(OperationCase.NEEDS)
        return [labels.get(n, n) for n in obj.needs]

    def get_person_phone(self, obj):
        user = self.context["request"].user
        if user.has_org_perm("person.view_sensitive", obj.organization_id):
            return obj.person.phone
        return "••••" + obj.person.phone[-2:] if obj.person.phone else ""

    def get_aid_summary(self, obj):
        aids = list(obj.assistances.all())
        delivered = [a for a in aids if a.status == "delivered"]
        return {"delivered": len(delivered), "planned": sum(1 for a in aids if a.status == "planned"),
                "delivered_amount": int(sum(a.amount or 0 for a in delivered))}


class OperationViewSet(OrgScopedViewSetMixin, mixins.CreateModelMixin, mixins.RetrieveModelMixin,
                       mixins.UpdateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = (Operation.objects.select_related("initiated_by", "territory").prefetch_related("partners")
                .annotate(persons_count=Count("cases")).order_by("-created_at"))
    serializer_class = OperationSerializer
    perms = {"default": "operation.view", "create": "operation.manage", "update": "operation.manage",
             "partial_update": "operation.manage", "transition": "operation.manage", "enroll": "operation.enroll"}
    filterset_fields = ["status", "kind", "organization", "territory"]
    search_fields = ["title", "code", "sites"]

    def perform_create(self, serializer):
        self.check_create_scope(serializer.validated_data["organization"].pk)
        op = serializer.save(created_by=self.request.user)
        audit("operation.created", obj=op, data={"kind": op.kind})

    def perform_update(self, serializer):
        op = serializer.save()
        audit("operation.updated", obj=op, data={"fields": list(serializer.validated_data)})

    @action(detail=True, methods=["get"])
    def stats(self, request, pk=None):
        return Response(services.operation_stats(self.get_object()))

    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        op = services.transition_operation(self.get_object(), request.data.get("to"), request.user,
                                           request.data.get("reason", ""))
        return Response(self.get_serializer(op).data)

    @action(detail=True, methods=["post"])
    def enroll(self, request, pk=None):
        """Ouvre le dossier d'une personne déjà enregistrée (ou plusieurs : `persons`)."""
        op = self.get_object()
        ids = request.data.get("persons") or ([request.data["person"]] if request.data.get("person") else [])
        if not ids:
            raise serializers.ValidationError({"person": "Sélectionnez au moins une personne."})
        extra = {k: request.data[k] for k in services.CASE_FIELDS if k in request.data and k != "assigned_to"}
        created, existing = [], []
        for pid in ids:
            person = get_object_or_404(Person.objects.filter(organization=op.organization), pk=pid)
            case, was_created = services.enroll(op, person, user=request.user, **extra)
            (created if was_created else existing).append(case)
        ctx = self.get_serializer_context()
        return Response({"created": OperationCaseSerializer(created, many=True, context=ctx).data,
                         "already_enrolled": [c.person.public_code for c in existing]},
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class OperationCaseViewSet(OrgScopedViewSetMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin,
                           mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = (OperationCase.objects.select_related("person", "household", "operation")
                .prefetch_related("assistances"))
    serializer_class = OperationCaseSerializer
    perms = {"default": "operation.view", "update": "operation.enroll", "partial_update": "operation.enroll",
             "transition": "operation.enroll"}
    filterset_fields = ["operation", "status", "person", "lost_housing", "organization"]
    search_fields = ["person__search_name", "person__public_code", "case_number", "current_location"]

    def perform_update(self, serializer):
        case = serializer.save()
        audit("operation.case_updated", obj=case, data={"fields": list(serializer.validated_data)})

    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        case = services.transition_case(self.get_object(), request.data.get("to"), request.user,
                                        request.data.get("reason", ""))
        return Response(self.get_serializer(case).data)

