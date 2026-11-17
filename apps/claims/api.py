"""
API du dispositif d'accompagnement des ménages affectés (mines, pétrole, énergie).

Visibilité (diapositive 22) :
- relais : les demandes qu'il a saisies, jusqu'à leur transmission (tri) ;
- cellule locale, comité, préfet, services techniques : les demandes de leur zone (adhésion limitée à un territoire) ;
- exploitant : les réclamations qui lui sont transmises, sans coordonnées sauf accord, jamais les plaintes
  confidentielles ;
- payeur : identité, montant validé, moyen de paiement — pas le contenu de la demande ;
- évaluateur : un échantillon tiré au sort, chaque consultation journalisée ;
- ministère : uniquement des indicateurs agrégés (voir reports.py).
"""
import csv
import io
import os
import re

from django.db.models import Q
from django.utils import timezone
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.assistance.models import Assistance
from apps.core.audit import audit
from apps.core.permissions import IsStaffMember, require_org_perm
from apps.operations.models import Operation, Partner
from apps.organizations.models import Organization
from apps.territories.models import Territory

from . import integrations, services
from .models import (
    Claim,
    ClaimAppeal,
    ClaimDecision,
    EvaluationSample,
    OperatorListEntry,
    ProtectedReport,
    UrgentMeasure,
)
from .referential import IMPACT_CODES, meta

REF_RE = re.compile(r"^DM-[ACDEFGHJKMNPQRTUVWXY34679]{4}-[ACDEFGHJKMNPQRTUVWXY34679]{4}$")


# --- Périmètre de visibilité -------------------------------------------------

def _subtree(territory_ids):
    ids, frontier = set(territory_ids), set(territory_ids)
    while frontier:
        frontier = set(Territory.objects.filter(parent_id__in=frontier).values_list("pk", flat=True)) - ids
        ids |= frontier
    return ids


def zone_q(user, perm, prefix=""):
    """Demandes visibles avec `perm` : toute l'organisation, ou la zone de l'adhésion (et ses subdivisions)."""
    from apps.accounts.roles import ROLE_PERMISSIONS

    cond = Q(pk__in=[])
    for m in user.active_memberships():
        if perm not in ROLE_PERMISSIONS.get(m.role, set()):
            continue
        part = Q(**{f"{prefix}organization_id": m.organization_id})
        if m.territory_id:
            part &= Q(**{f"{prefix}territory_id__in": _subtree([m.territory_id])})
        cond |= part
    return cond


def visible_claims(user):
    q = zone_q(user, "claim.view")
    own_orgs = user.organizations_with("claim.view_own")
    if own_orgs:
        q |= Q(organization_id__in=own_orgs, recorded_by=user, status=Claim.Status.REGISTERED)
    op_orgs = user.organizations_with("claim.operator")
    partners = [m.partner_id for m in user.active_memberships() if m.partner_id and m.organization_id in op_orgs]
    if partners:
        q |= Q(organization_id__in=op_orgs, operator_id__in=partners, transmitted_to_operator_at__isnull=False)
    ev_orgs = user.organizations_with("claim.evaluate")
    if ev_orgs:
        sampled = EvaluationSample.objects.filter(evaluator=user, organization_id__in=ev_orgs) \
            .values_list("claims", flat=True)
        q |= Q(pk__in=list(sampled))
    return Claim.objects.filter(q)


def access_mode(user, claim):
    if user.has_org_perm("claim.view", claim.organization_id) and \
            Claim.objects.filter(zone_q(user, "claim.view"), pk=claim.pk).exists():
        return "full"
    if claim.recorded_by_id == user.pk and user.has_org_perm("claim.view_own", claim.organization_id):
        return "own"
    if user.has_org_perm("claim.evaluate", claim.organization_id):
        return "evaluator"
    if user.has_org_perm("claim.operator", claim.organization_id):
        return "operator"
    return "none"


# --- Sérialisation -----------------------------------------------------------

class DecisionSerializer(serializers.ModelSerializer):
    outcome_label = serializers.CharField(source="get_outcome_display", read_only=True)
    validation_label = serializers.CharField(source="get_validation_display", read_only=True)
    proposed_by_name = serializers.CharField(source="proposed_by.get_full_name", read_only=True)
    validated_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ClaimDecision
        fields = ["id", "outcome", "outcome_label", "motive", "measures", "amount", "committee_members",
                  "proposed_by_name", "validation", "validation_label", "validated_by_name", "validated_at",
                  "validation_note", "after_appeal", "created_at"]

    def get_validated_by_name(self, obj):
        return obj.validated_by.get_full_name() if obj.validated_by_id else None


class AppealSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    response_due = serializers.DateField(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    claim_reference = serializers.CharField(source="claim.reference", read_only=True)

    class Meta:
        model = ClaimAppeal
        fields = ["id", "reference", "claim", "claim_reference", "motive", "status", "status_label",
                  "independent_member", "commission_members", "resolution", "resolved_at", "decision_modified",
                  "response_due", "is_overdue", "created_at"]


class UrgentSerializer(serializers.ModelSerializer):
    reason_label = serializers.CharField(source="get_reason_display", read_only=True)
    ordered_by_name = serializers.CharField(source="ordered_by.get_full_name", read_only=True)
    executed_in_time = serializers.BooleanField(read_only=True)

    class Meta:
        model = UrgentMeasure
        fields = ["id", "reason", "reason_label", "measure", "ordered_by_name", "created_at", "executed_at",
                  "execution_note", "executed_in_time"]


class ClaimSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    channel_label = serializers.CharField(source="get_channel_display", read_only=True)
    sector_label = serializers.CharField(source="get_sector_display", read_only=True)
    profile_label = serializers.CharField(source="get_profile_display", read_only=True)
    rsu_status_label = serializers.CharField(source="get_rsu_status_display", read_only=True)
    impact_labels = serializers.ListField(read_only=True)
    territory_name = serializers.CharField(source="territory.name", read_only=True, default=None)
    operation_title = serializers.CharField(source="operation.title", read_only=True, default=None)
    operator_name = serializers.CharField(source="operator.name", read_only=True, default=None)
    assigned_to_name = serializers.SerializerMethodField()
    recorded_by_name = serializers.SerializerMethodField()
    deadlines = serializers.SerializerMethodField()
    is_overdue = serializers.BooleanField(read_only=True)
    effective_priority = serializers.IntegerField(read_only=True)
    decision_days = serializers.IntegerField(read_only=True)
    appeal_deadline = serializers.DateField(read_only=True)

    class Meta:
        model = Claim
        fields = ["id", "organization", "reference", "operation", "operation_title", "operator", "operator_name",
                  "territory", "territory_name", "person", "claimant_name", "claimant_sex", "claimant_birth_year",
                  "claimant_phone", "language", "village", "household_size", "women_head", "has_dependent_person",
                  "channel", "channel_label", "sector", "sector_label", "impacts", "impact_labels", "profile",
                  "profile_label", "sources", "impact_date", "description", "assets_lost", "income_before",
                  "income_after", "aid_received", "no_identity_document", "community_validated",
                  "community_validation_note", "consent_operator_contact", "in_operator_list", "rsu_status",
                  "rsu_status_label", "status", "status_label", "urgent", "urgency_reason", "registered_at",
                  "receipt_issued_at", "triaged_at", "notified_at", "support_started_at", "closed_at",
                  "closure_motive", "assigned_to", "assigned_to_name", "social_inquiry", "technical_service",
                  "technical_opinion", "link_confirmed", "transmitted_to_operator_at", "operator_response",
                  "operator_responded_at", "priority_score", "priority_factors", "priority_override",
                  "override_motive", "effective_priority", "deadlines", "is_overdue", "decision_days",
                  "appeal_deadline", "recorded_by_name", "anonymized_at"]
        read_only_fields = ["reference", "status", "registered_at", "receipt_issued_at", "triaged_at", "notified_at",
                            "support_started_at", "closed_at", "closure_motive", "assigned_to", "social_inquiry",
                            "technical_opinion", "link_confirmed", "transmitted_to_operator_at",
                            "operator_response", "operator_responded_at", "priority_score", "priority_factors",
                            "priority_override", "override_motive", "in_operator_list", "rsu_status", "person",
                            "anonymized_at"]

    def get_assigned_to_name(self, obj):
        return obj.assigned_to.get_full_name() if obj.assigned_to_id else None

    def get_recorded_by_name(self, obj):
        return obj.recorded_by.get_full_name() if obj.recorded_by_id else None

    def get_deadlines(self, obj):
        return {"receipt": obj.receipt_due, "triage": obj.triage_due, "verification": obj.verification_due,
                "decision": obj.decision_due, "urgent": obj.urgent_due, "operator_transmit": obj.operator_transmit_due,
                "operator_response": obj.operator_response_due, "followup": obj.followup_due,
                "current": obj.current_step_due()}

    def validate_impacts(self, value):
        unknown = set(value) - IMPACT_CODES
        if unknown:
            raise serializers.ValidationError(f"Impacts inconnus : {', '.join(sorted(unknown))}")
        return value

    def validate(self, attrs):
        org = attrs.get("organization") or getattr(self.instance, "organization", None)
        for f in ("operation", "operator"):
            obj = attrs.get(f)
            if obj is not None and org is not None and obj.organization_id != org.pk:
                raise serializers.ValidationError({f: "Doit relever de la même organisation."})
        return attrs

    def to_representation(self, obj):
        data = super().to_representation(obj)
        mode = self.context.get("mode") or access_mode(self.context["request"].user, obj)
        if mode == "operator":
            # L'exploitant voit l'impact et la réclamation, pas le dossier social ni les coordonnées sans accord.
            keep = {"id", "reference", "village", "territory_name", "operation_title", "sector", "sector_label",
                    "impacts", "impact_labels", "impact_date", "assets_lost", "description", "status",
                    "status_label", "transmitted_to_operator_at", "operator_response", "operator_responded_at",
                    "deadlines", "household_size"}
            data = {k: v for k, v in data.items() if k in keep}
            if obj.consent_operator_contact:
                data["claimant_name"], data["claimant_phone"] = obj.claimant_name, obj.claimant_phone
            else:
                data["claimant_name"] = "Coordonnées non transmises (pas d'accord du ménage)"
        data["access_mode"] = mode
        return data


class ClaimDetailSerializer(ClaimSerializer):
    decisions = DecisionSerializer(many=True, read_only=True)
    appeals = AppealSerializer(many=True, read_only=True)
    urgent_measures = UrgentSerializer(many=True, read_only=True)
    events = serializers.SerializerMethodField()
    payments = serializers.SerializerMethodField()

    class Meta(ClaimSerializer.Meta):
        fields = ClaimSerializer.Meta.fields + ["decisions", "appeals", "urgent_measures", "events", "payments"]

    def get_events(self, obj):
        return [{"kind": e.kind, "note": e.note, "to_status": e.to_status, "at": e.created_at,
                 "actor": e.actor.get_full_name() if e.actor_id else None} for e in obj.events.all()]

    def get_payments(self, obj):
        return [{"id": str(a.pk), "amount": a.amount, "status": a.status, "channel": a.get_delivery_channel_display(),
                 "delivered_at": a.delivered_at, "proof": bool(a.delivery_proof)} for a in obj.assistances.all()]

    def to_representation(self, obj):
        data = super().to_representation(obj)
        if data.get("access_mode") == "operator":
            for k in ("decisions", "appeals", "urgent_measures", "events", "payments"):
                data.pop(k, None)
        return data


def _org_from(request):
    org_id = request.data.get("organization") or request.query_params.get("organization")
    org = Organization.objects.filter(pk=org_id).first() if org_id else None
    if org is None:
        raise serializers.ValidationError({"organization": "Organisation obligatoire."})
    return org


def _user_in_org(org, user_id, field):
    if not user_id:
        return None
    from apps.accounts.models import User

    u = User.objects.filter(pk=user_id, memberships__organization=org, memberships__is_active=True).first()
    if u is None:
        raise serializers.ValidationError({field: "Agent inconnu dans l'organisation."})
    return u


class ClaimViewSet(mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.ListModelMixin,
                   mixins.UpdateModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsStaffMember]
    filterset_fields = ["organization", "status", "sector", "channel", "urgent", "operation", "operator",
                        "territory", "rsu_status", "in_operator_list"]
    search_fields = ["reference", "claimant_name", "village"]
    ordering_fields = ["registered_at", "priority_score", "status"]
    http_method_names = ["get", "post", "patch"]

    def get_queryset(self):
        qs = visible_claims(self.request.user).select_related("territory", "operation", "operator", "assigned_to",
                                                               "recorded_by")
        p = self.request.query_params
        if p.get("overdue") == "true":
            ids = [c.pk for c in qs.exclude(status=Claim.Status.CLOSED) if c.is_overdue]
            qs = qs.filter(pk__in=ids)
        if p.get("open") == "true":
            qs = qs.exclude(status=Claim.Status.CLOSED)
        if p.get("appeal") == "open":
            qs = qs.filter(appeals__status=ClaimAppeal.Status.OPEN).distinct()
        if p.get("ordering") in (None, ""):
            qs = qs.order_by("-urgent", "-priority_score", "registered_at")
        return qs

    def get_serializer_class(self):
        return ClaimDetailSerializer if self.action == "retrieve" else ClaimSerializer

    def retrieve(self, request, *args, **kwargs):
        claim = self.get_object()
        mode = access_mode(request.user, claim)
        if mode == "evaluator":
            audit("claim.evaluator_access", obj=claim)
        return Response(self.get_serializer(claim, context={"request": request, "mode": mode}).data)

    def create(self, request, *args, **kwargs):
        # Saisie hors ligne (app Collecte) : un renvoi après coupure ne crée jamais de doublon.
        cu = request.data.get("client_uuid")
        if cu:
            existing = Claim.objects.filter(client_uuid=cu, recorded_by=request.user).first()
            if existing:
                return Response(ClaimSerializer(existing, context={"request": request, "mode": "own"}).data)
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        org = serializer.validated_data.get("organization")
        if org is None:
            raise serializers.ValidationError({"organization": "Organisation obligatoire."})
        require_org_perm(self.request.user, "claim.create", org.pk)
        # Le numéro remis hors ligne sur le récépissé est conservé s'il est libre ; sinon un autre est attribué.
        ref = (self.request.data.get("reference") or "").strip().upper()
        extra = {"reference": ref} if REF_RE.match(ref) and not Claim.objects.filter(reference=ref).exists() else {}
        cu = self.request.data.get("client_uuid")
        if cu:
            extra["client_uuid"] = cu
        claim = serializer.save(recorded_by=self.request.user, **extra)
        services.register(claim, self.request.user)

    def perform_update(self, serializer):
        claim = serializer.instance
        require_org_perm(self.request.user, "claim.process", claim.organization_id, obj=claim)
        if claim.status in {Claim.Status.CLOSED}:
            raise PermissionDenied("Demande clôturée.")
        serializer.save()
        audit("claim.updated", obj=claim, data={"fields": sorted(serializer.validated_data)})

    # --- étapes du circuit ---
    def _claim(self, perm):
        claim = self.get_object()
        require_org_perm(self.request.user, perm, claim.organization_id, obj=claim)
        return claim

    def _ok(self, claim):
        claim.refresh_from_db()
        return Response(ClaimDetailSerializer(claim, context={"request": self.request}).data)

    @action(detail=True, methods=["post"])
    def triage(self, request, pk=None):
        claim = self._claim("claim.process")
        d = request.data
        operator = Partner.objects.filter(pk=d.get("operator"), organization=claim.organization).first() \
            if d.get("operator") else None
        services.triage(claim, request.user, assigned_to=_user_in_org(claim.organization, d.get("assigned_to"),
                                                                      "assigned_to"),
                        urgent=d.get("urgent") if "urgent" in d else None, urgency_reason=d.get("urgency_reason", ""),
                        operator=operator, profile=d.get("profile", ""),
                        technical_service=d.get("technical_service", ""), note=d.get("note", ""))
        return self._ok(claim)

    @action(detail=True, methods=["post"])
    def inquiry(self, request, pk=None):
        claim = self._claim("claim.process")
        text = (request.data.get("social_inquiry") or "").strip()
        if len(text) < 10:
            raise serializers.ValidationError({"social_inquiry": "Résumer la visite (10 caractères minimum)."})
        services.record_inquiry(claim, request.user, text)
        return self._ok(claim)

    @action(detail=True, methods=["post"], url_path="technical-opinion")
    def technical_opinion(self, request, pk=None):
        claim = self.get_object()
        if not (request.user.has_org_perm("claim.process", claim.organization_id)
                or request.user.has_org_perm("claim.technical_opinion", claim.organization_id)):
            require_org_perm(request.user, "claim.technical_opinion", claim.organization_id, obj=claim)
        d = request.data
        if d.get("link_confirmed") not in {"yes", "no", "uncertain"}:
            raise serializers.ValidationError({"link_confirmed": "Préciser le lien avec l'activité."})
        services.technical_opinion(claim, request.user, service=d.get("service", ""),
                                   opinion=d.get("opinion", ""), link_confirmed=d["link_confirmed"])
        return self._ok(claim)

    @action(detail=True, methods=["post"])
    def propose(self, request, pk=None):
        claim = self._claim("claim.propose")
        d = request.data
        if d.get("outcome") not in ClaimDecision.Outcome.values:
            raise serializers.ValidationError({"outcome": "Issue inconnue."})
        services.propose(claim, request.user, outcome=d["outcome"], motive=d.get("motive", ""),
                         measures=d.get("measures") or [], amount=d.get("amount") or None,
                         committee_members=d.get("committee_members") or [])
        return self._ok(claim)

    @action(detail=True, methods=["post"])
    def validate(self, request, pk=None):
        claim = self._claim("claim.validate")
        services.validate(claim, request.user, approve=bool(request.data.get("approve")),
                          note=request.data.get("note", ""))
        return self._ok(claim)

    @action(detail=True, methods=["post"])
    def urgent(self, request, pk=None):
        claim = self._claim("claim.urgent_order")
        reason, measure = request.data.get("reason"), (request.data.get("measure") or "").strip()
        if not reason or len(measure) < 5:
            raise serializers.ValidationError({"measure": "Préciser le motif et la mesure ordonnée."})
        services.order_urgent(claim, request.user, reason=reason, measure=measure)
        return self._ok(claim)

    @action(detail=True, methods=["post"], url_path="urgent-executed")
    def urgent_executed(self, request, pk=None):
        claim = self._claim("claim.process")
        m = UrgentMeasure.objects.filter(pk=request.data.get("measure"), claim=claim).first()
        if m is None:
            raise NotFound("Mesure inconnue.")
        services.execute_urgent(m, request.user, request.data.get("note", ""))
        return self._ok(claim)

    @action(detail=True, methods=["post"])
    def transmit(self, request, pk=None):
        claim = self._claim("claim.process")
        if request.data.get("operator"):
            claim.operator = Partner.objects.filter(pk=request.data["operator"],
                                                    organization=claim.organization).first()
        services.transmit_to_operator(claim, request.user)
        return self._ok(claim)

    @action(detail=True, methods=["post"], url_path="operator-response")
    def operator_response(self, request, pk=None):
        claim = self.get_object()
        require_org_perm(request.user, "claim.operator", claim.organization_id, obj=claim)
        text = (request.data.get("response") or "").strip()
        if len(text) < 10:
            raise serializers.ValidationError({"response": "Réponse motivée (10 caractères minimum)."})
        services.operator_respond(claim, request.user, text)
        claim.refresh_from_db()
        return Response(ClaimSerializer(claim, context={"request": request, "mode": "operator"}).data)

    @action(detail=True, methods=["post"])
    def support(self, request, pk=None):
        claim = self._claim("claim.process")
        d = request.data
        services.start_support(claim, request.user, amount=d.get("amount") or None,
                               channel=d.get("channel") or "mobile_money", services=d.get("services") or [],
                               note=d.get("note", ""))
        return self._ok(claim)

    @action(detail=True, methods=["post"])
    def followup(self, request, pk=None):
        claim = self._claim("claim.process")
        services.followup(claim, request.user, request.data.get("note", ""))
        return self._ok(claim)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        claim = self._claim("claim.process")
        services.close(claim, request.user, request.data.get("motive", ""))
        return self._ok(claim)

    @action(detail=True, methods=["post"])
    def appeal(self, request, pk=None):
        claim = self._claim("claim.appeal_file")
        services.file_appeal(claim, request.user, request.data.get("motive", ""))
        return self._ok(claim)

    @action(detail=True, methods=["post"])
    def priority(self, request, pk=None):
        claim = self._claim("claim.process")
        services.override_priority(claim, request.user, request.data.get("value", -1), request.data.get("motive", ""))
        return self._ok(claim)

    @action(detail=True, methods=["post"])
    def rsu(self, request, pk=None):
        claim = self._claim("claim.process")
        if request.data.get("action") == "refer":
            claim.rsu_status = Claim.RsuStatus.REFERRED
            claim.save()
            services._event(claim, "rsu_referred", request.user, "Orienté vers une inscription gratuite au RSU.")
            return self._ok(claim)
        result = integrations.rsu_lookup(claim)
        if result.get("configured") and "registered" in result:
            claim.rsu_status = Claim.RsuStatus.REGISTERED if result["registered"] else Claim.RsuStatus.NOT_REGISTERED
            claim.save()
            services._event(claim, "rsu_checked", request.user, claim.get_rsu_status_display())
        return Response({**result, "claim": ClaimDetailSerializer(claim, context={"request": request}).data})

    @action(detail=True, methods=["get"])
    def receipt(self, request, pk=None):
        claim = self.get_object()
        audit("claim.receipt_printed", obj=claim)
        return Response({
            "reference": claim.reference, "claimant_name": claim.claimant_name, "village": claim.village,
            "territory": claim.territory.name if claim.territory_id else "", "registered_at": claim.registered_at,
            "channel": claim.get_channel_display(), "impacts": claim.impact_labels(), "language": claim.language,
            "decision_due": claim.decision_due, "urgent": claim.urgent,
            "organization": claim.organization.name,
            "recorded_by": claim.recorded_by.get_full_name() if claim.recorded_by_id else "",
        })


class ClaimMetaView(APIView):
    permission_classes = [IsStaffMember]

    def get(self, request):
        from .models import (
            APPEAL_RESPONSE_DAYS,
            APPEAL_WINDOW_DAYS,
            DECISION_DAYS,
            OPERATOR_RESPONSE_DAYS,
            TRIAGE_BUSINESS_DAYS,
            URGENT_HOURS,
            VERIFICATION_BUSINESS_DAYS,
        )

        return Response({
            **meta(),
            "channels": [{"value": v, "label": label} for v, label in Claim.Channel.choices],
            "statuses": [{"value": v, "label": label} for v, label in Claim.Status.choices],
            "outcomes": [{"value": v, "label": label} for v, label in ClaimDecision.Outcome.choices],
            "protected_kinds": [{"value": v, "label": label} for v, label in ProtectedReport.Kind.choices],
            "delays": {"triage_business_days": TRIAGE_BUSINESS_DAYS,
                       "verification_business_days": VERIFICATION_BUSINESS_DAYS, "decision_days": DECISION_DAYS,
                       "urgent_hours": URGENT_HOURS, "operator_response_days": OPERATOR_RESPONSE_DAYS,
                       "appeal_window_days": APPEAL_WINDOW_DAYS, "appeal_response_days": APPEAL_RESPONSE_DAYS},
            "integrations": {"sms": integrations.sms_configured(), "rsu": integrations.rsu_configured()},
        })


class AppealViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsStaffMember]
    serializer_class = AppealSerializer
    filterset_fields = ["organization", "status"]

    def get_queryset(self):
        return ClaimAppeal.objects.filter(claim__in=visible_claims(self.request.user)).select_related("claim")

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        appeal = self.get_object()
        require_org_perm(request.user, "claim.appeal_resolve", appeal.organization_id, obj=appeal)
        d = request.data
        services.resolve_appeal(appeal, request.user, upheld=bool(d.get("upheld")), resolution=d.get("resolution", ""),
                                independent_member=d.get("independent_member", ""),
                                commission_members=d.get("commission_members") or [])
        return Response(AppealSerializer(appeal).data)


# --- Paiements (payeur) ------------------------------------------------------

class PaymentSerializer(serializers.ModelSerializer):
    reference = serializers.CharField(source="claim.reference", read_only=True)
    beneficiary = serializers.CharField(source="claim.claimant_name", read_only=True)
    phone = serializers.CharField(source="claim.claimant_phone", read_only=True)
    village = serializers.CharField(source="claim.village", read_only=True)
    channel_label = serializers.CharField(source="get_delivery_channel_display", read_only=True)
    validated_at = serializers.SerializerMethodField()

    class Meta:
        model = Assistance
        fields = ["id", "reference", "beneficiary", "phone", "village", "amount", "currency", "delivery_channel",
                  "channel_label", "status", "planned_date", "delivered_at", "delivery_proof", "validated_at"]

    def get_validated_at(self, obj):
        d = obj.claim.decisions.filter(validation=ClaimDecision.Validation.VALIDATED).first()
        return d.validated_at if d else None


class PaymentViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Le payeur voit l'identité, le montant validé et le moyen de paiement — pas le contenu de la demande."""

    permission_classes = [IsStaffMember]
    serializer_class = PaymentSerializer
    filterset_fields = ["status"]

    def get_queryset(self):
        orgs = self.request.user.organizations_with("claim.pay") | self.request.user.organizations_with("claim.view")
        return Assistance.objects.filter(claim__isnull=False, organization_id__in=orgs).select_related("claim") \
            .order_by("status", "planned_date")

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        a = self.get_queryset().filter(pk=pk).first()
        if a is None:
            raise NotFound()
        require_org_perm(request.user, "claim.pay", a.organization_id, obj=a)
        services.confirm_payment(a, request.user, proof=request.data.get("proof", ""),
                                 channel=request.data.get("channel"))
        return Response(PaymentSerializer(a).data)

    @action(detail=False, methods=["get"])
    def export(self, request):
        """Ordre de paiement CSV pour l'opérateur (paiements validés non encore exécutés)."""
        rows = [r for r in self.get_queryset().filter(status="planned")
                if request.user.has_org_perm("claim.pay", r.organization_id)]
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["reference", "beneficiaire", "telephone", "village", "montant", "moyen"])
        for a in rows:
            w.writerow([a.claim.reference, a.claim.claimant_name, a.claim.claimant_phone, a.claim.village, a.amount,
                        a.get_delivery_channel_display()])
        audit("claim.payment_order_exported", actor=request.user, data={"rows": len(rows)})
        return Response({"filename": f"ordre-paiement-{timezone.localdate():%Y%m%d}.csv", "content": buf.getvalue(),
                         "rows": len(rows)})


# --- Plaintes confidentielles ------------------------------------------------

class ProtectedSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    is_priority = serializers.BooleanField(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    followup_1m_due = serializers.DateField(read_only=True)
    followup_3m_due = serializers.DateField(read_only=True)
    claim_reference = serializers.CharField(source="claim.reference", read_only=True, default=None)
    territory_name = serializers.CharField(source="territory.name", read_only=True, default=None)

    class Meta:
        model = ProtectedReport
        fields = ["id", "organization", "reference", "claim", "claim_reference", "territory", "territory_name", "kind",
                  "kind_label", "channel", "description", "reporter_name", "reporter_phone", "consent_disclosure",
                  "consent_at", "status", "status_label", "due_at", "prefect_notified_at", "followup_1m_due",
                  "followup_1m_done_at", "followup_3m_due", "followup_3m_done_at", "resolution", "resolved_at",
                  "is_priority", "is_overdue", "created_at"]
        read_only_fields = ["reference", "status", "due_at", "prefect_notified_at", "followup_1m_done_at",
                            "followup_3m_done_at", "resolution", "resolved_at", "consent_at"]

    def to_representation(self, obj):
        data = super().to_representation(obj)
        if not obj.consent_disclosure:
            data["reporter_name"] = "Identité masquée" if obj.reporter_name else ""
            data["reporter_phone"] = "•••" if obj.reporter_phone else ""
            data["identity_on_file"] = bool(obj.reporter_name or obj.reporter_phone)
        return data


class ProtectedViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                       viewsets.GenericViewSet):
    """Canal séparé, traité par le membre indépendant de la commission de recours."""

    permission_classes = [IsStaffMember]
    serializer_class = ProtectedSerializer
    filterset_fields = ["organization", "status", "kind"]

    def get_queryset(self):
        orgs = self.request.user.organizations_with("protected.view")
        return ProtectedReport.objects.filter(organization_id__in=orgs).select_related("claim", "territory")

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        org = ser.validated_data["organization"]
        require_org_perm(request.user, "protected.file", org.pk)
        report = services.file_protected(ProtectedReport(**ser.validated_data), request.user)
        # Celui qui enregistre ne voit ensuite que la référence : le contenu va au membre indépendant.
        return Response({"id": str(report.pk), "reference": report.reference, "due_at": report.due_at,
                         "is_priority": report.is_priority}, status=201)

    def retrieve(self, request, *args, **kwargs):
        report = self.get_object()
        audit("protected.viewed", obj=report)
        return Response(self.get_serializer(report).data)

    @action(detail=True, methods=["post"])
    def update_status(self, request, pk=None):
        r = self.get_object()
        d = request.data
        to = d.get("status")
        if to and to not in ProtectedReport.Status.values:
            raise serializers.ValidationError({"status": "Statut inconnu."})
        if to in {"resolved", "closed"}:
            note = (d.get("resolution") or "").strip()
            if len(note) < 10:
                raise serializers.ValidationError({"resolution": "Réponse écrite obligatoire."})
            r.resolution, r.resolved_at = note, timezone.now()
        if to:
            r.status = to
        if d.get("followup") == "1m":
            r.followup_1m_done_at = timezone.now()
        if d.get("followup") == "3m":
            r.followup_3m_done_at = timezone.now()
        if d.get("consent_disclosure"):
            r.consent_disclosure, r.consent_at = True, timezone.now()
        r.handled_by = request.user
        r.save()
        audit("protected.updated", obj=r, data={k: d.get(k) for k in ("status", "followup", "consent_disclosure")})
        return Response(self.get_serializer(r).data)


# --- Liste de l'exploitant ----------------------------------------------------

class OperatorListView(APIView):
    permission_classes = [IsStaffMember]

    def get(self, request):
        org = _org_from(request)
        require_org_perm(request.user, "claim.import_list", org.pk)
        op = Operation.objects.filter(pk=request.query_params.get("operation"), organization=org).first() \
            if request.query_params.get("operation") else None
        result = services.match_operator_list(org, operation=op)
        entries = OperatorListEntry.objects.filter(organization=org)
        if op:
            entries = entries.filter(operation=op)
        result["entries"] = [{"id": str(e.pk), "full_name": e.full_name, "village": e.village,
                              "household_size": e.household_size, "assets": e.assets, "batch": e.batch,
                              "matched_claim": e.matched_claim.reference if e.matched_claim_id else None}
                             for e in entries.select_related("matched_claim")[:500]]
        return Response(result)

    def post(self, request):
        org = _org_from(request)
        require_org_perm(request.user, "claim.import_list", org.pk)
        rows = request.data.get("rows")
        if rows is None and request.data.get("csv"):
            text = request.data["csv"]
            dialect = ";" if text.count(";") > text.count(",") else ","
            rows = list(csv.DictReader(io.StringIO(text), delimiter=dialect))
            rows = [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in rows]
        if not rows:
            raise serializers.ValidationError({"rows": "Aucune ligne à importer (colonnes : nom, village, biens)."})
        op = Operation.objects.filter(pk=request.data.get("operation"), organization=org).first()
        operator = Partner.objects.filter(pk=request.data.get("operator"), organization=org).first() or \
            (op.initiated_by if op else None)
        return Response(services.import_operator_list(org, request.user, rows, operation=op, operator=operator,
                                                      batch=request.data.get("batch", "")))


# --- Évaluation indépendante -------------------------------------------------

class EvaluationView(APIView):
    permission_classes = [IsStaffMember]

    def get(self, request):
        samples = EvaluationSample.objects.filter(evaluator=request.user).prefetch_related("claims")
        return Response([{"id": str(s.pk), "created_at": s.created_at, "seed": s.seed, "note": s.note,
                          "claims": [{"id": str(c.pk), "reference": c.reference, "status": c.get_status_display()}
                                     for c in s.claims.all()]} for s in samples])

    def post(self, request):
        org = _org_from(request)
        require_org_perm(request.user, "claim.evaluate", org.pk)
        size = max(1, min(int(request.data.get("size") or 20), 100))
        s = services.draw_sample(org, request.user, size, request.data.get("note", ""))
        return Response({"id": str(s.pk), "size": s.claims.count(), "seed": s.seed}, status=201)


# --- SMS entrants (numéro court) ---------------------------------------------

SMS_FOLLOW = re.compile(r"^\s*SUIVI\s+(DM-[A-Z0-9]{4}-[A-Z0-9]{4})", re.I)
SMS_REPORT = re.compile(r"^\s*SIGNAL(?:ER)?\s+(.+)$", re.I | re.S)


class SmsInboundView(APIView):
    """
    Passerelle de l'opérateur SMS (lot 3). Protégée par un jeton partagé (LANFIA_SMS_INBOUND_TOKEN).
    « SUIVI DM-XXXX-XXXX » : état du dossier (si le numéro correspond) ; « SIGNALER <texte> » : nouvelle demande.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        token = os.getenv("LANFIA_SMS_INBOUND_TOKEN")
        if not token:
            return Response({"error": {"code": "not_configured", "message": "Canal SMS non activé."}}, status=503)
        if request.headers.get("X-Lanfia-Sms-Token") != token:
            return Response({"error": {"code": "forbidden", "message": "Jeton invalide."}}, status=403)
        phone = (request.data.get("from") or "").strip()
        text = (request.data.get("text") or "").strip()
        org = Organization.objects.filter(code=request.data.get("organization") or os.getenv(
            "LANFIA_SMS_ORGANIZATION", "")).first()
        if org is None:
            return Response({"reply": "Service indisponible."}, status=400)
        m = SMS_FOLLOW.match(text)
        if m:
            claim = Claim.objects.filter(organization=org, reference=m.group(1).upper()).first()
            if claim is None or not phone or claim.claimant_phone[-8:] != phone[-8:]:
                return Response({"reply": "Dossier introuvable pour ce numéro. Adressez-vous à votre relais."})
            reply = f"{claim.reference} : {claim.get_status_display()}."
            if claim.status not in {Claim.Status.DECIDED, Claim.Status.SUPPORT, Claim.Status.FOLLOWUP,
                                    Claim.Status.CLOSED}:
                reply += f" Décision au plus tard le {claim.decision_due:%d/%m/%Y}."
            return Response({"reply": reply})
        m = SMS_REPORT.match(text)
        if m:
            claim = Claim(organization=org, claimant_name="À compléter (signalement SMS)", claimant_phone=phone,
                          channel=Claim.Channel.SMS, description=m.group(1)[:1000], sources=["self"])
            claim.save()
            services.register(claim, None)
            return Response({"reply": f"Demande {claim.reference} enregistrée. Un relais vous rappellera. "
                                      f"Suivi : envoyez SUIVI {claim.reference}."}, status=201)
        return Response({"reply": "Envoyez SIGNALER suivi de votre situation, ou SUIVI suivi de votre numéro "
                                  "de dossier."})
