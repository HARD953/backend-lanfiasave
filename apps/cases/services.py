from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.core.audit import audit
from apps.core.exceptions import IllegalTransition

from .models import Appeal, Application, Decision, StatusChange


def transition_application(app: Application, to_status: str, user, reason: str = "") -> Application:
    if to_status not in Application.TRANSITIONS.get(app.status, set()):
        raise IllegalTransition(f"Passage de « {app.get_status_display()} » à « {to_status} » impossible.")
    if to_status in {"inadmissible", "on_hold"} and not reason.strip():
        raise ValidationError({"reason": "Un motif compréhensible est obligatoire."})
    if to_status == "decided":
        raise IllegalTransition("Une candidature n'est décidée que par l'enregistrement d'une décision du comité.")
    previous = app.status
    app.status = to_status
    app.status_reason = reason
    app.save(update_fields=["status", "status_reason", "updated_at"])
    StatusChange.objects.create(application=app, from_status=previous, to_status=to_status, reason=reason, actor=user)
    audit("application.status_changed", obj=app, reason=reason, data={"from": previous, "to": to_status})
    return app


@transaction.atomic
def record_decision(app: Application, user, *, outcome: str, motive: str, committee_members=None,
                    quorum_met=True, internal_notes="") -> Decision:
    """
    Décision humaine motivée (DEC-01). Garde-fous :
    - la candidature doit être au comité ;
    - le motif est obligatoire ;
    - l'auteur de la candidature ne peut pas la décider (conflit d'intérêts, §2.2) ;
    - le quorum doit être atteint pour accorder ou refuser.
    """
    app = Application.objects.select_for_update().get(pk=app.pk)
    if app.status != Application.Status.COMMITTEE:
        raise IllegalTransition("La candidature doit être au comité pour être décidée.")
    if not motive or len(motive.strip()) < 10:
        raise ValidationError({"motive": "Motif obligatoire et compréhensible (10 caractères minimum)."})
    if app.submitted_by_id and app.submitted_by_id == user.pk:
        raise PermissionDenied("Conflit d'intérêts : l'auteur de la candidature ne peut pas la décider.")
    if outcome in {Decision.Outcome.APPROVED, Decision.Outcome.REJECTED} and not quorum_met:
        raise ValidationError({"quorum_met": "Quorum non atteint : seule une décision d'ajournement est possible."})

    previous = app.decisions.filter(superseded_by__isnull=True).first()
    decision = Decision.objects.create(
        organization=app.organization, application=app, outcome=outcome, motive=motive.strip(),
        internal_notes=internal_notes, decided_by=user, committee_members=committee_members or [],
        quorum_met=quorum_met, rule_version=app.rule_version,
        score_snapshot=({"total": app.score.total, "band": app.score.band, "version": str(app.score.scoring_version)}
                        if app.score else None),
        supersedes=previous, notified_at=timezone.now())
    StatusChange.objects.create(application=app, from_status=app.status, to_status="decided",
                                reason=f"Décision : {decision.get_outcome_display()}", actor=user)
    app.status = Application.Status.DECIDED
    app.save(update_fields=["status", "updated_at"])
    audit("decision.recorded", obj=decision, reason=motive, data={"outcome": outcome, "application": str(app.pk)})
    return decision


def file_appeal(decision: Decision, user, motive: str, via: str = "agent") -> Appeal:
    if not motive or len(motive.strip()) < 5:
        raise ValidationError({"motive": "Expliquez la raison du recours."})
    if decision.appeals.filter(status__in=[Appeal.Status.OPEN, Appeal.Status.UNDER_REVIEW]).exists():
        raise IllegalTransition("Un recours est déjà en cours pour cette décision.")
    appeal = Appeal.objects.create(organization=decision.organization, decision=decision, motive=motive.strip(),
                                   filed_by=user, filed_via=via)
    audit("appeal.filed", obj=appeal, data={"decision": str(decision.pk), "via": via})
    return appeal


@transaction.atomic
def resolve_appeal(appeal: Appeal, user, *, upheld: bool, resolution: str) -> Appeal:
    if appeal.status not in {Appeal.Status.OPEN, Appeal.Status.UNDER_REVIEW}:
        raise IllegalTransition("Ce recours est déjà traité.")
    if not resolution or len(resolution.strip()) < 10:
        raise ValidationError({"resolution": "Réponse motivée obligatoire."})
    if appeal.decision.decided_by_id == user.pk:
        raise PermissionDenied("Le recours doit être examiné par une autre personne que l'auteur de la décision.")
    appeal.status = Appeal.Status.UPHELD if upheld else Appeal.Status.DISMISSED
    appeal.resolution = resolution.strip()
    appeal.resolved_by = user
    appeal.resolved_at = timezone.now()
    appeal.save()
    if upheld:
        app = appeal.decision.application
        StatusChange.objects.create(application=app, from_status=app.status, to_status="committee",
                                    reason="Recours accueilli : réexamen", actor=user)
        app.status = Application.Status.COMMITTEE
        app.save(update_fields=["status", "updated_at"])
    audit("appeal.resolved", obj=appeal, reason=resolution, data={"upheld": upheld})
    return appeal
