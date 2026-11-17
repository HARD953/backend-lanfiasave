"""
Règles du circuit des demandes. Chaque étape vérifie ses préalables, trace un événement lisible et l'audit.
Séparation des fonctions : celui qui propose ne valide pas ; le payeur n'examine pas ; l'exploitant ne voit
ni les plaintes confidentielles ni l'identité des plaignants.
"""
import random
import unicodedata

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.assistance.models import Assistance
from apps.core.audit import audit
from apps.core.exceptions import IllegalTransition
from apps.followup.models import Referral

from . import integrations
from .models import (
    APPEAL_WINDOW_DAYS,
    Claim,
    ClaimAppeal,
    ClaimDecision,
    ClaimEvent,
    EvaluationSample,
    OperatorListEntry,
    ProtectedReport,
    UrgentMeasure,
)

S = Claim.Status
OPEN_STATUSES = {S.REGISTERED, S.TRIAGED, S.VERIFYING, S.PROPOSED, S.DECIDED, S.SUPPORT, S.FOLLOWUP}


def _event(claim, kind, user, note="", from_status="", to_status="", data=None):
    ClaimEvent.objects.create(claim=claim, kind=kind, actor=user, note=note, from_status=from_status,
                              to_status=to_status)
    audit(f"claim.{kind}", obj=claim, actor=user, data=data or {})


def _move(claim, to, user, kind, note="", allowed_from=None, **fields):
    if allowed_from is not None and claim.status not in allowed_from:
        raise IllegalTransition(
            f"Étape impossible : la demande est « {claim.get_status_display()} ».")
    before = claim.status
    claim.status = to
    for k, v in fields.items():
        setattr(claim, k, v)
    claim.save()
    _event(claim, kind, user, note, before, to)
    return claim


def _motive(text, field="motive"):
    if not text or len(text.strip()) < 10:
        raise ValidationError({field: "Motif obligatoire et compréhensible (10 caractères minimum)."})
    return text.strip()


def notify(claim, text):
    """Information du ménage : SMS si possible ; sinon le relais lit le récépissé ou la décision."""
    return integrations.send_sms(claim.claimant_phone, text)


@transaction.atomic
def register(claim, user):
    claim.receipt_issued_at = timezone.now()
    claim.save()
    _event(claim, "registered", user, f"Récépissé {claim.reference} remis ({claim.get_channel_display()}).",
           to_status=S.REGISTERED)
    notify(claim, f"LanfiaSave : votre demande {claim.reference} est enregistrée. Vous recevrez une décision "
                  f"motivée au plus tard le {claim.decision_due:%d/%m/%Y}.")
    if claim.urgent:
        _event(claim, "urgent_flagged", user, "Urgence signalée : mesure à ordonner sous 72 h.")
    return claim


@transaction.atomic
def triage(claim, user, *, assigned_to=None, urgent=None, urgency_reason="", operator=None, profile="",
           technical_service="", note=""):
    if urgent is not None:
        claim.urgent = urgent
        claim.urgency_reason = urgency_reason if urgent else ""
    if operator is not None:
        claim.operator = operator
    if profile:
        claim.profile = profile
    if technical_service:
        claim.technical_service = technical_service
    claim.assigned_to = assigned_to or claim.assigned_to or user
    return _move(claim, S.TRIAGED, user, "triaged", note or "Demande triée et orientée.",
                 allowed_from={S.REGISTERED}, triaged_at=timezone.now())


@transaction.atomic
def record_inquiry(claim, user, text):
    if claim.status not in {S.TRIAGED, S.VERIFYING}:
        raise IllegalTransition("L'enquête sociale se fait après le tri et avant la proposition de décision.")
    claim.social_inquiry = text.strip()
    if claim.status == S.TRIAGED:
        return _move(claim, S.VERIFYING, user, "inquiry", "Visite et enquête sociale enregistrées.",
                     verification_started_at=timezone.now())
    claim.save()
    _event(claim, "inquiry", user, "Enquête sociale complétée.")
    return claim


@transaction.atomic
def technical_opinion(claim, user, *, service, opinion, link_confirmed):
    if claim.status not in {S.TRIAGED, S.VERIFYING}:
        raise IllegalTransition("L'avis technique se donne pendant la vérification.")
    claim.technical_service, claim.technical_opinion, claim.link_confirmed = service, opinion.strip(), link_confirmed
    if claim.status == S.TRIAGED:
        claim.status, claim.verification_started_at = S.VERIFYING, timezone.now()
    claim.save()
    _event(claim, "technical_opinion", user, f"Avis du service technique : {claim.get_link_confirmed_display()}.")
    return claim


@transaction.atomic
def propose(claim, user, *, outcome, motive, measures=None, amount=None, committee_members=None):
    if claim.status != S.VERIFYING:
        raise IllegalTransition("Le comité propose une décision sur une demande vérifiée.")
    if not claim.social_inquiry:
        raise ValidationError({"social_inquiry": "L'enquête sociale doit précéder la proposition."})
    if outcome in {ClaimDecision.Outcome.SUPPORT, ClaimDecision.Outcome.SUPPORT_ORIENTATION} and not amount \
            and not measures:
        raise ValidationError({"measures": "Préciser l'appui accordé (montant ou mesures)."})
    after_appeal = claim.appeals.filter(status=ClaimAppeal.Status.UPHELD).exists()
    decision = ClaimDecision.objects.create(
        organization=claim.organization, claim=claim, outcome=outcome, motive=_motive(motive),
        measures=measures or [], amount=amount, committee_members=committee_members or [], proposed_by=user,
        after_appeal=after_appeal)
    _move(claim, S.PROPOSED, user, "proposed", f"Proposition du comité : {decision.get_outcome_display()}.",
          proposed_at=timezone.now())
    return decision


@transaction.atomic
def validate(claim, user, *, approve, note=""):
    decision = claim.decisions.filter(validation=ClaimDecision.Validation.PENDING).first()
    if claim.status != S.PROPOSED or decision is None:
        raise IllegalTransition("Aucune proposition de décision en attente de validation.")
    if decision.proposed_by_id == user.pk:
        raise PermissionDenied("Séparation des fonctions : celui qui propose ne valide pas.")
    decision.validated_by, decision.validated_at, decision.validation_note = user, timezone.now(), note
    if not approve:
        decision.validation = ClaimDecision.Validation.RETURNED
        decision.save()
        _motive(note, "note")
        return _move(claim, S.VERIFYING, user, "returned", f"Renvoyée au comité : {note}")
    decision.validation = ClaimDecision.Validation.VALIDATED
    decision.save()
    if decision.outcome == ClaimDecision.Outcome.DEFERRED:
        _move(claim, S.VERIFYING, user, "deferred", "Examen ajourné : compléments demandés.",
              notified_at=timezone.now())
    else:
        _move(claim, S.DECIDED, user, "decided", f"Décision validée et notifiée : {decision.get_outcome_display()}.",
              notified_at=timezone.now())
    notify(claim, f"LanfiaSave : décision sur votre demande {claim.reference} : {decision.get_outcome_display()}. "
                  f"Motif : {decision.motive[:90].rstrip('.')}. "
                  f"Vous pouvez la contester pendant {APPEAL_WINDOW_DAYS} jours.")
    return decision


@transaction.atomic
def order_urgent(claim, user, *, reason, measure):
    if claim.status not in OPEN_STATUSES:
        raise IllegalTransition("Demande clôturée.")
    claim.urgent, claim.urgency_reason = True, reason
    claim.save()
    m = UrgentMeasure.objects.create(claim=claim, reason=reason, measure=measure.strip(), ordered_by=user)
    _event(claim, "urgent_ordered", user, f"Mesure d'urgence ordonnée : {measure.strip()}")
    return m


@transaction.atomic
def execute_urgent(measure, user, note=""):
    if measure.executed_at:
        raise IllegalTransition("Mesure déjà exécutée.")
    measure.executed_at, measure.execution_note = timezone.now(), note
    measure.save()
    _event(measure.claim, "urgent_executed", user, f"Mesure d'urgence exécutée. {note}".strip())
    return measure


@transaction.atomic
def transmit_to_operator(claim, user):
    if claim.operator_id is None:
        raise ValidationError({"operator": "Indiquer l'exploitant concerné."})
    if claim.status == S.REGISTERED or claim.status == S.CLOSED:
        raise IllegalTransition("La transmission se fait après le tri.")
    if claim.transmitted_to_operator_at:
        raise IllegalTransition("Déjà transmise à l'exploitant.")
    claim.transmitted_to_operator_at = timezone.now()
    claim.save()
    _event(claim, "transmitted", user, f"Transmise à {claim.operator.name}.")
    return claim


@transaction.atomic
def operator_respond(claim, user, response):
    if not claim.transmitted_to_operator_at:
        raise IllegalTransition("Cette réclamation n'a pas été transmise.")
    claim.operator_response, claim.operator_responded_at = response.strip(), timezone.now()
    claim.save()
    _event(claim, "operator_response", user, "Réponse de l'exploitant reçue.")
    return claim


def ensure_person(claim, user):
    """Le dossier personne n'est créé qu'au moment de l'appui (minimisation : pas avant la décision)."""
    if claim.person_id:
        return claim.person
    from apps.beneficiaries.models import Person

    parts = claim.claimant_name.split()
    claim.person = Person.objects.create(
        organization=claim.organization, territory=claim.territory, first_name=" ".join(parts[:-1]) or parts[0],
        last_name=parts[-1] if len(parts) > 1 else "", sex=claim.claimant_sex or Person.Sex.UNDISCLOSED,
        phone=claim.claimant_phone,
        estimated_age=(timezone.localdate().year - claim.claimant_birth_year) if claim.claimant_birth_year else None,
        has_identity_document=not claim.no_identity_document, status="verified", created_by=user)
    claim.save()
    return claim.person


@transaction.atomic
def start_support(claim, user, *, amount=None, channel="mobile_money", services=None, note=""):
    decision = claim.decisions.filter(validation=ClaimDecision.Validation.VALIDATED).first()
    if claim.status != S.DECIDED or decision is None:
        raise IllegalTransition("L'appui suit une décision validée.")
    if decision.outcome == ClaimDecision.Outcome.REJECTED:
        raise IllegalTransition("Décision de rejet : clôturer le dossier avec son motif.")
    amount = amount or decision.amount
    if amount or services:
        ensure_person(claim, user)
    if amount:
        Assistance.objects.create(
            organization=claim.organization, claim=claim, person=claim.person, household=claim.household,
            category="cash", amount=amount, delivery_channel=channel, status="planned",
            planned_date=timezone.localdate(), description=f"Appui validé — demande {claim.reference}",
            recorded_by=user)
    for service in services or []:
        if claim.person_id:  # toujours vrai ici : le dossier personne vient d'être créé
            Referral.objects.create(organization=claim.organization, person=claim.person, service=service,
                                    reason=f"Orientation décidée — demande {claim.reference}", referred_by=user)
        if service == "rsu":
            claim.rsu_status = Claim.RsuStatus.REFERRED
    return _move(claim, S.SUPPORT, user, "support", note or "Appui ou orientation engagé.",
                 support_started_at=timezone.now())


@transaction.atomic
def followup(claim, user, note):
    if claim.status not in {S.SUPPORT, S.FOLLOWUP}:
        raise IllegalTransition("Le suivi concerne une demande en appui.")
    return _move(claim, S.FOLLOWUP, user, "followup", note or "Visite de suivi.")


@transaction.atomic
def close(claim, user, motive):
    decision = claim.decisions.filter(validation=ClaimDecision.Validation.VALIDATED).first()
    allowed = {S.SUPPORT, S.FOLLOWUP}
    if decision and decision.outcome == ClaimDecision.Outcome.REJECTED:
        allowed.add(S.DECIDED)
    if claim.appeals.filter(status=ClaimAppeal.Status.OPEN).exists():
        raise IllegalTransition("Une contestation est en cours.")
    return _move(claim, S.CLOSED, user, "closed", _motive(motive), allowed_from=allowed, closed_at=timezone.now(),
                 closure_motive=motive.strip())


@transaction.atomic
def file_appeal(claim, user, motive):
    decision = claim.decisions.filter(validation=ClaimDecision.Validation.VALIDATED).first()
    if not claim.notified_at or decision is None:
        raise IllegalTransition("Aucune décision notifiée à contester.")
    if timezone.localdate() > claim.appeal_deadline:
        raise IllegalTransition(f"Délai de contestation de {APPEAL_WINDOW_DAYS} jours dépassé. Les recours "
                                "administratifs et judiciaires restent ouverts.")
    if claim.appeals.filter(status=ClaimAppeal.Status.OPEN).exists():
        raise IllegalTransition("Une contestation est déjà en cours.")
    appeal = ClaimAppeal.objects.create(organization=claim.organization, claim=claim, decision=decision,
                                        motive=_motive(motive), filed_by=user)
    _event(claim, "appeal_filed", user, f"Contestation {appeal.reference} déposée.")
    return appeal


@transaction.atomic
def resolve_appeal(appeal, user, *, upheld, resolution, independent_member, commission_members=None):
    if appeal.status != ClaimAppeal.Status.OPEN:
        raise IllegalTransition("Contestation déjà tranchée.")
    if not (independent_member or "").strip():
        raise ValidationError({"independent_member": "La commission siège avec un membre indépendant."})
    appeal.status = ClaimAppeal.Status.UPHELD if upheld else ClaimAppeal.Status.DISMISSED
    appeal.resolution, appeal.resolved_by, appeal.resolved_at = _motive(resolution, "resolution"), user, \
        timezone.now()
    appeal.independent_member, appeal.commission_members = independent_member.strip(), commission_members or []
    appeal.decision_modified = bool(upheld)
    appeal.save()
    claim = appeal.claim
    if upheld:
        _move(claim, S.VERIFYING, user, "appeal_upheld", "Contestation accueillie : nouvel examen.",
              closed_at=None, closure_motive="")
    else:
        _event(claim, "appeal_dismissed", user, "Contestation rejetée par la commission (motif écrit).")
    notify(claim, f"LanfiaSave : réponse à votre contestation {appeal.reference} : {appeal.get_status_display()}.")
    return appeal


@transaction.atomic
def override_priority(claim, user, value, motive):
    if not 0 <= int(value) <= 100:
        raise ValidationError({"value": "Entre 0 et 100."})
    claim.priority_override, claim.override_motive = int(value), _motive(motive)
    claim.save()
    _event(claim, "priority_override", user, f"Ordre d'examen fixé à {value} : {motive.strip()}")
    return claim


@transaction.atomic
def confirm_payment(assistance, user, *, proof, channel=None):
    if assistance.claim_id is None or assistance.status != "planned":
        raise IllegalTransition("Paiement déjà exécuté ou hors dispositif.")
    if not (proof or "").strip():
        raise ValidationError({"proof": "Preuve obligatoire (reçu signé, n° de transaction)."})
    assistance.status, assistance.delivered_at, assistance.delivered_by = "delivered", timezone.now(), user
    assistance.delivery_proof = proof.strip()
    if channel:
        assistance.delivery_channel = channel
    assistance.save()
    _event(assistance.claim, "paid", user, f"Paiement exécuté ({assistance.get_delivery_channel_display()}).")
    return assistance


# --- Plaintes confidentielles -------------------------------------------------

@transaction.atomic
def file_protected(report, user):
    report.recorded_by = user
    report.save()
    if report.kind == ProtectedReport.Kind.AID_CONDITIONED:
        report.prefect_notified_at = timezone.now()
        report.save(update_fields=["prefect_notified_at"])
    audit("protected.filed", obj=report, actor=user, data={"kind": report.kind})
    return report


# --- Liste de l'exploitant ----------------------------------------------------

def normalize(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return " ".join(sorted(s.replace("-", " ").replace("'", " ").split()))


@transaction.atomic
def import_operator_list(org, user, rows, *, operation=None, operator=None, batch=""):
    batch = batch or timezone.now().strftime("%Y%m%d-%H%M")
    entries = []
    for r in rows:
        name = (r.get("full_name") or r.get("nom") or "").strip()
        if not name:
            continue
        entries.append(OperatorListEntry(
            organization=org, operation=operation, operator=operator, batch=batch, full_name=name,
            village=(r.get("village") or "").strip(), household_size=r.get("household_size") or None,
            assets=(r.get("assets") or r.get("biens") or "")[:255], cutoff_date=r.get("cutoff_date") or None,
            imported_by=user))
    OperatorListEntry.objects.bulk_create(entries)
    result = match_operator_list(org, operation=operation)
    audit("claim.operator_list_imported", actor=user, organization=org,
          data={"batch": batch, "rows": len(entries), **{k: v for k, v in result.items() if k != "absent"}})
    return {"batch": batch, "imported": len(entries), **result}


def match_operator_list(org, *, operation=None):
    entries = OperatorListEntry.objects.filter(organization=org)
    claims = Claim.objects.filter(organization=org)
    if operation is not None:
        entries, claims = entries.filter(operation=operation), claims.filter(operation=operation)
    index = {}
    for e in entries:
        index.setdefault(normalize(e.full_name), []).append(e)
    matched, absent = 0, []
    for c in claims.exclude(anonymized_at__isnull=False):
        hits = index.get(normalize(c.claimant_name), [])
        hit = next((e for e in hits if not e.village or not c.village or
                    normalize(e.village) == normalize(c.village)), None)
        found = hit is not None
        if hit and hit.matched_claim_id != c.pk:
            OperatorListEntry.objects.filter(pk=hit.pk).update(matched_claim=c)
        if c.in_operator_list != found:
            c.in_operator_list = found
            c.save()
        matched += found
        if not found:
            absent.append(c.reference)
    unclaimed = entries.filter(matched_claim__isnull=True).count()
    return {"listed": entries.count(), "matched": matched, "absent_count": len(absent), "absent": absent[:200],
            "listed_without_claim": unclaimed}


# --- Évaluation indépendante -------------------------------------------------

@transaction.atomic
def draw_sample(org, user, size=20, note=""):
    pool = list(Claim.objects.filter(organization=org, status__in=[S.DECIDED, S.SUPPORT, S.FOLLOWUP, S.CLOSED])
                .values_list("pk", flat=True))
    seed = timezone.now().strftime("%Y%m%d%H%M%S%f")
    chosen = random.Random(seed).sample(pool, min(size, len(pool)))
    sample = EvaluationSample.objects.create(organization=org, evaluator=user, seed=seed, note=note)
    sample.claims.set(chosen)
    audit("claim.sample_drawn", actor=user, organization=org, data={"size": len(chosen), "seed": seed})
    return sample
