from apps.core.audit import audit

from .engine import SCORING_V2, compute_score
from .models import ScoreResult, ScoringModelVersion


def active_scoring_version() -> ScoringModelVersion:
    version = ScoringModelVersion.objects.filter(is_active=True).order_by("-version").first()
    if version is None:
        version, _ = ScoringModelVersion.objects.get_or_create(
            code=SCORING_V2["code"], version=SCORING_V2["version"],
            defaults={"config": SCORING_V2, "is_active": True,
                      "notes": "Grille v2 : critères enrichis (alimentation, logement, chocs, enfance). "
                               "Seuils historiques non calibrés scientifiquement."})
    return version


def score_survey_response(response, user=None, version: ScoringModelVersion | None = None) -> ScoreResult:
    version = version or active_scoring_version()
    outcome = compute_score(response.answers, version.config)
    result = ScoreResult.objects.create(
        organization=response.organization, person=response.person, survey_response=response,
        scoring_version=version, total=outcome.total, band=outcome.band["code"],
        completeness=outcome.completeness, is_reliable=outcome.is_reliable, details=outcome.as_dict(),
        computed_by=user)
    audit("score.computed", obj=result, data={"total": outcome.total, "version": str(version)})
    return result
