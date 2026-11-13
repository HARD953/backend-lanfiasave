from django.db.models import Count, Sum
from django.utils import timezone
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.audit import audit
from apps.core.exceptions import IllegalTransition
from apps.core.permissions import OrgScopedViewSetMixin, require_org_perm

from .models import Assistance


class AssistanceSerializer(serializers.ModelSerializer):
    category_label = serializers.CharField(source="get_category_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    person_name = serializers.CharField(source="person.display_name", read_only=True)
    person_code = serializers.CharField(source="person.public_code", read_only=True)
    provider_name = serializers.SerializerMethodField()
    program_name = serializers.CharField(source="program.name", read_only=True, default=None)
    operation_title = serializers.CharField(source="operation_case.operation.title", read_only=True, default=None)
    recorded_by_name = serializers.CharField(source="recorded_by.get_full_name", read_only=True, default=None)
    delivered_by_name = serializers.CharField(source="delivered_by.get_full_name", read_only=True, default=None)

    class Meta:
        model = Assistance
        fields = ["id", "organization", "person", "person_name", "person_code", "household", "operation_case",
                  "operation_title", "program", "program_name", "provider", "provider_name", "category",
                  "category_label", "description", "quantity", "unit", "amount", "currency", "status",
                  "status_label", "planned_date", "delivered_at", "delivery_proof", "delivery_channel",
                  "cancel_reason", "recorded_by_name", "delivered_by_name", "created_at"]
        read_only_fields = ["organization", "status", "delivered_at", "cancel_reason", "household"]

    def get_provider_name(self, obj):
        return str(obj.provider) if obj.provider_id else None

    def validate(self, attrs):
        person = attrs.get("person") or getattr(self.instance, "person", None)
        for field in ("operation_case", "program", "provider"):
            obj = attrs.get(field)
            if obj is not None and obj.organization_id != person.organization_id:
                raise serializers.ValidationError({field: "Doit relever de la même organisation que la personne."})
        case = attrs.get("operation_case")
        if case is not None and case.person_id != person.pk:
            raise serializers.ValidationError({"operation_case": "Ce dossier ne concerne pas cette personne."})
        amount = attrs.get("amount")
        if amount is not None and amount < 0:
            raise serializers.ValidationError({"amount": "Montant négatif impossible."})
        return attrs


class AssistanceViewSet(OrgScopedViewSetMixin, mixins.CreateModelMixin, mixins.RetrieveModelMixin,
                        mixins.UpdateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """Aides : planifier, confirmer la remise (avec preuve), annuler avec motif. Jamais de suppression."""

    queryset = Assistance.objects.select_related("person", "provider", "program", "operation_case__operation",
                                                 "recorded_by", "delivered_by")
    serializer_class = AssistanceSerializer
    perms = {"default": "assistance.view", "create": "assistance.record", "update": "assistance.record",
             "partial_update": "assistance.record", "deliver": "assistance.deliver",
             "cancel": "assistance.record"}
    filterset_fields = ["person", "status", "category", "provider", "program", "operation_case",
                        "operation_case__operation", "organization"]
    search_fields = ["person__search_name", "person__public_code", "description", "delivery_proof"]
    ordering_fields = ["created_at", "delivered_at", "amount"]

    def perform_create(self, serializer):
        person = serializer.validated_data["person"]
        self.check_create_scope(person.organization_id)
        m = person.current_household_membership()
        a = serializer.save(organization=person.organization, recorded_by=self.request.user,
                            household=m.household if m else None)
        audit("assistance.planned", obj=a, data={"category": a.category, "amount": str(a.amount or "")})
        if self.request.data.get("deliver_now"):
            require_org_perm(self.request.user, "assistance.deliver", a.organization_id, obj=a)
            self._deliver(a, self.request.data)

    def perform_update(self, serializer):
        if serializer.instance.status != Assistance.Status.PLANNED:
            raise IllegalTransition("Une aide remise ou annulée n'est plus modifiable : annulez-la avec un motif.")
        a = serializer.save()
        audit("assistance.updated", obj=a, data={"fields": list(serializer.validated_data)})

    def _deliver(self, a, data):
        proof = (data.get("delivery_proof") or "").strip()
        if not proof:
            raise serializers.ValidationError({"delivery_proof": "Une preuve de remise est obligatoire "
                                                                 "(reçu, liste émargée, n° de transaction…)."})
        a.status = Assistance.Status.DELIVERED
        a.delivered_at = data.get("delivered_at") or timezone.now()
        a.delivery_proof = proof
        a.delivery_channel = data.get("delivery_channel") or a.delivery_channel
        a.delivered_by = self.request.user
        a.save()
        if a.operation_case and a.operation_case.status in {"identified", "assessed"}:
            a.operation_case.status = "supported"
            a.operation_case.last_contact_at = timezone.now()
            a.operation_case.save(update_fields=["status", "last_contact_at", "updated_at"])
        audit("assistance.delivered", obj=a, data={"proof": proof, "amount": str(a.amount or "")})

    @action(detail=True, methods=["post"])
    def deliver(self, request, pk=None):
        a = self.get_object()
        if a.status != Assistance.Status.PLANNED:
            raise IllegalTransition("Seule une aide planifiée peut être confirmée comme remise.")
        self._deliver(a, request.data)
        return Response(self.get_serializer(a).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        a = self.get_object()
        reason = (request.data.get("reason") or "").strip()
        if a.status == Assistance.Status.CANCELLED:
            raise IllegalTransition("Cette aide est déjà annulée.")
        if len(reason) < 5:
            raise serializers.ValidationError({"reason": "Motif d'annulation obligatoire."})
        if a.status == Assistance.Status.DELIVERED:
            require_org_perm(request.user, "assistance.deliver", a.organization_id, obj=a)
        previous = a.status
        a.status = Assistance.Status.CANCELLED
        a.cancel_reason = reason
        a.save(update_fields=["status", "cancel_reason", "updated_at"])
        audit("assistance.cancelled", obj=a, reason=reason, data={"previous": previous})
        return Response(self.get_serializer(a).data)

    @action(detail=False, methods=["get"])
    def summary(self, request):
        qs = self.filter_queryset(self.get_queryset())
        delivered = qs.filter(status="delivered")
        return Response({
            "count": qs.count(),
            "delivered": delivered.count(),
            "planned": qs.filter(status="planned").count(),
            "cancelled": qs.filter(status="cancelled").count(),
            "delivered_amount": delivered.aggregate(s=Sum("amount"))["s"] or 0,
            "planned_amount": qs.filter(status="planned").aggregate(s=Sum("amount"))["s"] or 0,
            "persons_helped": delivered.values("person").distinct().count(),
            "by_category": dict(delivered.values_list("category").annotate(n=Count("id"))),
        })


def categories_payload():
    return [{"value": v, "label": label} for v, label in Assistance.Category.choices]
