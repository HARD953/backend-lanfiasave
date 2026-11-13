from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.api import grant_beneficiary_access
from apps.core.audit import audit
from apps.core.idempotency import IdempotentCreateMixin
from apps.core.permissions import OrgScopedViewSetMixin, require_org_perm

from . import services
from .models import DuplicateCandidate, Household, HouseholdMembership, Person, RecordStatus, normalize_name


def mask(value: str) -> str:
    if not value:
        return value
    return "•" * max(len(value) - 2, 0) + value[-2:]


class HouseholdMembershipSerializer(serializers.ModelSerializer):
    person_name = serializers.CharField(source="person.display_name", read_only=True)
    person_code = serializers.CharField(source="person.public_code", read_only=True)
    relation_label = serializers.CharField(source="get_relation_display", read_only=True)

    class Meta:
        model = HouseholdMembership
        fields = ["id", "household", "person", "person_name", "person_code", "relation", "relation_label",
                  "start_date", "end_date", "end_reason"]


class PersonSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(read_only=True)
    age = serializers.IntegerField(read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    territory_name = serializers.CharField(source="territory.name", read_only=True, default=None)
    household = serializers.SerializerMethodField()
    has_beneficiary_access = serializers.SerializerMethodField()
    open_duplicates = serializers.SerializerMethodField()
    latest_score = serializers.SerializerMethodField()

    class Meta:
        model = Person
        fields = ["id", "organization", "public_code", "is_provisional", "enrollment_context", "first_name",
                  "last_name", "usual_name", "display_name", "sex", "birth_date", "estimated_age", "age", "phone",
                  "language", "territory", "territory_name", "residence_status", "health_coverage_number",
                  "has_identity_document", "status", "status_label", "merged_into", "household",
                  "has_beneficiary_access", "open_duplicates", "latest_score", "created_at", "updated_at"]
        read_only_fields = ["public_code", "status", "merged_into"]

    def validate(self, attrs):
        if not attrs.get("birth_date") and attrs.get("estimated_age") is None and not self.instance:
            # L'âge peut rester inconnu : on n'invente rien, mais on le signale.
            pass
        return attrs

    def get_household(self, obj):
        m = obj.current_household_membership()
        return ({"id": str(m.household_id), "code": m.household.public_code, "relation": m.relation}
                if m else None)

    def get_has_beneficiary_access(self, obj):
        return hasattr(obj, "account")

    def get_open_duplicates(self, obj):
        return DuplicateCandidate.objects.filter(status="open").filter(person=obj).count() + \
            DuplicateCandidate.objects.filter(status="open", other=obj).count()

    def get_latest_score(self, obj):
        s = obj.scores.order_by("-created_at").first()
        return {"id": str(s.id), "total": s.total, "band": s.band, "is_reliable": s.is_reliable} if s else None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        user = self.context["request"].user
        if not user.has_org_perm("person.view_sensitive", instance.organization_id):
            for f in Person.SENSITIVE_FIELDS:
                if data.get(f):
                    data[f] = mask(str(data[f]))
            data["masked"] = True
        return data


class HouseholdSerializer(serializers.ModelSerializer):
    members = serializers.SerializerMethodField()
    territory_name = serializers.CharField(source="territory.name", read_only=True, default=None)

    class Meta:
        model = Household
        fields = ["id", "organization", "public_code", "territory", "territory_name", "address_description",
                  "latitude", "longitude", "gps_consent", "status", "members", "created_at"]
        read_only_fields = ["public_code", "status"]

    def get_members(self, obj):
        qs = obj.memberships.filter(end_date__isnull=True).select_related("person")
        return HouseholdMembershipSerializer(qs, many=True).data


class PersonViewSet(IdempotentCreateMixin, OrgScopedViewSetMixin, mixins.CreateModelMixin,
                    mixins.RetrieveModelMixin, mixins.UpdateModelMixin, mixins.ListModelMixin,
                    viewsets.GenericViewSet):
    """Pas de suppression : un dossier s'archive (historique conservé)."""

    queryset = Person.objects.select_related("territory", "organization").all()
    serializer_class = PersonSerializer
    perms = {"default": "person.view", "create": "person.create", "update": "person.update",
             "partial_update": "person.update", "transition": "person.update", "verify": "person.verify",
             "merge": "person.merge", "unmerge": "person.merge", "beneficiary_access": "person.update"}
    filterset_fields = ["status", "territory", "organization", "is_provisional", "sex"]
    ordering_fields = ["created_at", "last_name"]

    def get_queryset(self):
        qs = super().get_queryset()
        term = self.request.query_params.get("q")
        if term:
            from django.db.models import Q

            n = normalize_name(term)
            qs = qs.filter(Q(search_name__contains=n) | Q(public_code__iexact=term.strip()) | Q(phone=term.strip()))
        if self.request.query_params.get("include_merged") != "1" and self.action == "list":
            qs = qs.filter(merged_into__isnull=True)
        return qs

    def perform_create(self, serializer):
        org = serializer.validated_data["organization"]
        self.check_create_scope(org.pk)
        person = serializer.save(created_by=self.request.user)
        audit("person.created", obj=person)
        services.detect_duplicates(person)

    def perform_update(self, serializer):
        before = {f: getattr(serializer.instance, f) for f in serializer.validated_data}
        if "organization" in serializer.validated_data and \
                serializer.validated_data["organization"] != serializer.instance.organization:
            raise serializers.ValidationError({"organization": "Un dossier ne change pas d'organisation."})
        person = serializer.save()
        changed = [f for f, v in before.items() if getattr(person, f) != v]
        audit("person.updated", obj=person, data={"fields": changed})

    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        person = self.get_object()
        to = request.data.get("to")
        if to in {RecordStatus.VERIFIED, RecordStatus.TO_CORRECT}:
            require_org_perm(request.user, "person.verify", person.organization_id, obj=person)
        services.transition_person(person, to, request.data.get("reason", ""))
        return Response(self.get_serializer(person).data)

    @action(detail=True, methods=["get"])
    def duplicates(self, request, pk=None):
        person = self.get_object()
        from django.db.models import Q

        cands = DuplicateCandidate.objects.filter(Q(person=person) | Q(other=person)).select_related("person",
                                                                                                    "other")
        out = []
        for c in cands:
            other = c.other if c.person_id == person.pk else c.person
            out.append({"id": str(c.id), "confidence": c.confidence, "reasons": c.reasons, "status": c.status,
                        "other": {"id": str(other.id), "public_code": other.public_code,
                                  "display_name": other.display_name, "age": other.age}})
        return Response(out)

    @action(detail=True, methods=["post"])
    def merge(self, request, pk=None):
        duplicate = self.get_object()
        keep = get_object_or_404(self.get_queryset(), pk=request.data.get("keep"))
        reason = request.data.get("reason", "")
        if not reason:
            raise serializers.ValidationError({"reason": "Motif obligatoire."})
        services.merge_persons(duplicate, keep, request.user, reason)
        return Response(self.get_serializer(duplicate).data)

    @action(detail=True, methods=["post"])
    def unmerge(self, request, pk=None):
        person = self.get_object()
        services.unmerge_person(person, request.data.get("reason", ""))
        return Response(self.get_serializer(person).data)

    @action(detail=True, methods=["post"], url_path="beneficiary-access")
    def beneficiary_access(self, request, pk=None):
        person = self.get_object()
        pin = grant_beneficiary_access(person, request.user)
        return Response({"identifier": person.public_code, "pin": pin,
                         "message": "Communiquez ce code PIN à la personne. Il ne sera plus affiché."})


class HouseholdViewSet(IdempotentCreateMixin, OrgScopedViewSetMixin, mixins.CreateModelMixin,
                       mixins.RetrieveModelMixin, mixins.UpdateModelMixin, mixins.ListModelMixin,
                       viewsets.GenericViewSet):
    queryset = Household.objects.select_related("territory").all()
    serializer_class = HouseholdSerializer
    perms = {"default": "person.view", "create": "person.create", "update": "person.update",
             "partial_update": "person.update", "add_member": "person.update", "end_member": "person.update"}
    filterset_fields = ["status", "territory"]

    def perform_create(self, serializer):
        self.check_create_scope(serializer.validated_data["organization"].pk)
        hh = serializer.save(created_by=self.request.user)
        audit("household.created", obj=hh)

    @action(detail=True, methods=["post"], url_path="members")
    def add_member(self, request, pk=None):
        hh = self.get_object()
        person = get_object_or_404(Person.objects.filter(organization=hh.organization), pk=request.data.get("person"))
        with transaction.atomic():
            current = person.current_household_membership()
            if current:
                current.end_date = timezone.localdate()
                current.end_reason = request.data.get("reason") or "Changement de ménage"
                current.save()
            m = HouseholdMembership.objects.create(household=hh, person=person,
                                                   relation=request.data.get("relation", "other"),
                                                   start_date=request.data.get("start_date") or timezone.localdate())
        audit("household.member_added", obj=hh, data={"person": str(person.pk)})
        return Response(HouseholdMembershipSerializer(m).data, status=201)
