from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.beneficiaries.models import Household, HouseholdMembership, Person
from apps.beneficiaries.services import detect_duplicates
from apps.beneficiaries.vulnerabilities import CHOICES as VULNERABILITY_CHOICES
from apps.beneficiaries.vulnerability_services import flag, flag_from_survey
from apps.core.audit import audit
from apps.core.idempotency import IdempotentCreateMixin
from apps.core.permissions import IsStaffMember, OrgScopedViewSetMixin, require_org_perm
from apps.operations.models import Operation
from apps.operations.services import enroll
from apps.organizations.models import Organization
from apps.scoring.api import ScoreResultSerializer
from apps.scoring.services import score_survey_response
from apps.territories.models import Territory

from .models import QuestionnaireVersion, SurveyResponse


class QuestionnaireSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuestionnaireVersion
        fields = ["id", "code", "version", "title", "status", "published_at", "definition"]


class QuestionnaireViewSet(viewsets.ReadOnlyModelViewSet):
    """Questionnaires publiés (téléchargés par l'application de collecte)."""

    queryset = QuestionnaireVersion.objects.filter(status="published")
    serializer_class = QuestionnaireSerializer
    permission_classes = [IsStaffMember]
    pagination_class = None


class SurveyResponseSerializer(serializers.ModelSerializer):
    questionnaire_label = serializers.CharField(source="questionnaire.__str__", read_only=True)
    collected_by_name = serializers.CharField(source="collected_by.get_full_name", read_only=True, default=None)

    class Meta:
        model = SurveyResponse
        fields = ["id", "organization", "person", "household", "questionnaire", "questionnaire_label", "answers",
                  "collected_at", "collected_by_name", "device_id", "client_uuid", "created_at"]

    def validate(self, attrs):
        person = attrs.get("person")
        if person and attrs.get("organization") and person.organization_id != attrs["organization"].pk:
            raise serializers.ValidationError("La personne n'appartient pas à cette organisation.")
        attrs["questionnaire"].validate_answers(attrs.get("answers") or {})
        return attrs


class SurveyResponseViewSet(IdempotentCreateMixin, OrgScopedViewSetMixin, mixins.CreateModelMixin,
                            mixins.RetrieveModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """Une enquête n'est pas modifiée : une nouvelle collecte crée une nouvelle réponse (historique)."""

    queryset = SurveyResponse.objects.select_related("questionnaire", "collected_by").all()
    serializer_class = SurveyResponseSerializer
    perms = {"default": "survey.view", "create": "survey.submit", "score": "score.compute"}
    filterset_fields = ["person", "household", "questionnaire"]

    def perform_create(self, serializer):
        self.check_create_scope(serializer.validated_data["organization"].pk)
        resp = serializer.save(collected_by=self.request.user)
        audit("survey.submitted", obj=resp)
        flag_from_survey(resp, self.request.user)

    @action(detail=True, methods=["post"])
    def score(self, request, pk=None):
        resp = self.get_object()
        result = score_survey_response(resp, request.user)
        return Response(ScoreResultSerializer(result).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Synchronisation de l'application de collecte : un ménage, ses membres et leurs enquêtes en une
# seule transaction, rejouable sans doublon (Idempotency-Key + client_uuid). (MOB-01, TC-04)
# ---------------------------------------------------------------------------

class CollectMemberSerializer(serializers.Serializer):
    client_uuid = serializers.UUIDField()
    first_name = serializers.CharField(max_length=120)
    last_name = serializers.CharField(max_length=120, allow_blank=True, required=False, default="")
    usual_name = serializers.CharField(max_length=120, allow_blank=True, required=False, default="")
    sex = serializers.ChoiceField(choices=Person.Sex.choices, default="U")
    estimated_age = serializers.IntegerField(min_value=0, max_value=120, required=False, allow_null=True)
    birth_date = serializers.DateField(required=False, allow_null=True)
    phone = serializers.CharField(max_length=20, allow_blank=True, required=False, default="")
    language = serializers.CharField(max_length=40, allow_blank=True, required=False, default="")
    relation = serializers.ChoiceField(choices=HouseholdMembership.Relation.choices)
    has_identity_document = serializers.BooleanField(default=False)
    answers = serializers.DictField(required=False, default=dict)
    vulnerabilities = serializers.ListField(child=serializers.ChoiceField(choices=VULNERABILITY_CHOICES),
                                            required=False, default=list)


class CollectSubmissionSerializer(serializers.Serializer):
    client_uuid = serializers.UUIDField()
    organization = serializers.PrimaryKeyRelatedField(queryset=Organization.objects.all())
    questionnaire = serializers.PrimaryKeyRelatedField(queryset=QuestionnaireVersion.objects.all())
    territory = serializers.PrimaryKeyRelatedField(queryset=Territory.objects.all(), allow_null=True,
                                                   required=False)
    address_description = serializers.CharField(max_length=255, allow_blank=True, required=False, default="")
    gps_consent = serializers.BooleanField(default=False)
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    emergency = serializers.BooleanField(default=False)
    collected_at = serializers.DateTimeField()
    device_id = serializers.CharField(max_length=80, allow_blank=True, required=False, default="")
    operation = serializers.PrimaryKeyRelatedField(queryset=Operation.objects.all(), required=False, allow_null=True,
                                                   help_text="Opération à laquelle rattacher le ménage")
    members = CollectMemberSerializer(many=True)

    def validate_members(self, members):
        if not members:
            raise serializers.ValidationError("Au moins une personne est requise.")
        if sum(1 for m in members if m["relation"] == "head") != 1:
            raise serializers.ValidationError("Le ménage doit avoir exactement un chef de ménage.")
        return members

    def validate(self, attrs):
        for m in attrs["members"]:
            if m.get("answers"):
                attrs["questionnaire"].validate_answers(m["answers"])
        op = attrs.get("operation")
        if op is not None and op.organization_id != attrs["organization"].pk:
            raise serializers.ValidationError({"operation": "Opération d'une autre organisation."})
        return attrs


class CollectSubmissionView(APIView):
    permission_classes = [IsStaffMember]

    def post(self, request):
        ser = CollectSubmissionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        require_org_perm(request.user, "person.create", d["organization"].pk)
        require_org_perm(request.user, "survey.submit", d["organization"].pk)
        if d.get("operation"):
            require_org_perm(request.user, "operation.enroll", d["organization"].pk)

        member_uuids = [m["client_uuid"] for m in d["members"]]
        existing = SurveyResponse.objects.filter(client_uuid__in=member_uuids).select_related("person", "household")
        if existing.exists():  # rejeu d'une synchronisation déjà reçue
            first = existing.first()
            return Response(self._summary(first.household, list(existing)), status=status.HTTP_200_OK,
                            headers={"Idempotent-Replay": "true"})
        try:
            with transaction.atomic():
                hh = Household.objects.create(
                    organization=d["organization"], territory=d.get("territory"),
                    address_description=d["address_description"], gps_consent=d["gps_consent"],
                    latitude=d.get("latitude"), longitude=d.get("longitude"), created_by=request.user)
                responses = []
                for m in d["members"]:
                    person = Person.objects.create(
                        organization=d["organization"], first_name=m["first_name"], last_name=m["last_name"],
                        usual_name=m["usual_name"], sex=m["sex"], estimated_age=m.get("estimated_age"),
                        birth_date=m.get("birth_date"), phone=m["phone"], language=m["language"],
                        territory=d.get("territory"), has_identity_document=m["has_identity_document"],
                        enrollment_context="emergency" if d["emergency"] else "standard", created_by=request.user)
                    HouseholdMembership.objects.create(household=hh, person=person, relation=m["relation"],
                                                       start_date=d["collected_at"].date())
                    responses.append(SurveyResponse.objects.create(
                        organization=d["organization"], person=person, household=hh, questionnaire=d["questionnaire"],
                        answers=m.get("answers") or {}, collected_at=d["collected_at"], collected_by=request.user,
                        device_id=d["device_id"], client_uuid=m["client_uuid"]))
                audit("collect.submission", obj=hh, data={"members": len(responses), "device": d["device_id"]})
        except IntegrityError:
            existing = SurveyResponse.objects.filter(client_uuid__in=member_uuids)
            if existing.exists():
                first = existing.first()
                return Response(self._summary(first.household, list(existing)), status=status.HTTP_200_OK)
            raise
        head_answers = next((m.get("answers") or {} for m in d["members"] if m["relation"] == "head"), {})
        for r, m in zip(responses, d["members"]):
            detect_duplicates(r.person)
            flag_from_survey(r, request.user)
            for code in m.get("vulnerabilities") or []:
                flag(r.person, code, source="survey", user=request.user)
            if d.get("operation"):
                enroll(d["operation"], r.person, user=request.user, household=hh,
                       lost_housing=head_answers.get("lost_housing") == "yes",
                       lost_livelihood=head_answers.get("lost_livelihood") == "yes",
                       lost_documents=head_answers.get("lost_documents") == "yes")
        return Response(self._summary(hh, responses), status=status.HTTP_201_CREATED)

    @staticmethod
    def _summary(hh, responses):
        return {
            "household": {"id": str(hh.id), "public_code": hh.public_code} if hh else None,
            "members": [{"client_uuid": str(r.client_uuid), "person_id": str(r.person_id),
                         "public_code": r.person.public_code, "survey_response_id": str(r.id)} for r in responses],
            "received_at": timezone.now().isoformat(),
        }
