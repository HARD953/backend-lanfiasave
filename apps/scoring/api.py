from rest_framework import serializers, viewsets

from apps.core.permissions import OrgScopedViewSetMixin

from .models import ScoreResult


class ScoreResultSerializer(serializers.ModelSerializer):
    scoring_version_label = serializers.CharField(source="scoring_version.__str__", read_only=True)
    computed_by_name = serializers.CharField(source="computed_by.get_full_name", read_only=True, default=None)
    disclaimer = serializers.SerializerMethodField()

    class Meta:
        model = ScoreResult
        fields = ["id", "person", "survey_response", "scoring_version", "scoring_version_label", "total", "band",
                  "completeness", "is_reliable", "details", "computed_by_name", "created_at", "disclaimer"]

    def get_disclaimer(self, obj):
        return ("Score consultatif : il ne vaut ni décision, ni refus, ni droit. "
                "Les seuils historiques ne sont pas calibrés scientifiquement.")


class ScoreResultViewSet(OrgScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = ScoreResult.objects.select_related("scoring_version", "computed_by").all()
    serializer_class = ScoreResultSerializer
    perms = {"default": "score.view"}
    filterset_fields = ["person", "band", "is_reliable"]
