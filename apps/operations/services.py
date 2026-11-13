from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.beneficiaries.vulnerability_services import flag
from apps.core.audit import audit
from apps.core.exceptions import IllegalTransition

from .models import Operation, OperationCase

CASE_FIELDS = ("occupancy", "lost_housing", "lost_livelihood", "lost_documents", "children_schooling_disrupted",
               "lost_assets", "needs", "current_location", "relocation_site", "compensation_due", "notes",
               "assigned_to")


def enroll(operation: Operation, person, *, user=None, household=None, **fields) -> tuple[OperationCase, bool]:
    """Ouvre (ou retrouve) le dossier d'une personne affectée et qualifie sa vulnérabilité."""
    if person.organization_id != operation.organization_id:
        raise ValidationError({"person": "La personne et l'opération doivent relever de la même organisation."})
    if operation.status == Operation.Status.CLOSED:
        raise IllegalTransition("Cette opération est clôturée : rouvrez-la pour ajouter des personnes.")
    if household is None:
        m = person.current_household_membership()
        household = m.household if m else None
    try:
        with transaction.atomic():
            case = OperationCase.objects.create(
                organization=operation.organization, operation=operation, person=person, household=household,
                identified_by=user, last_contact_at=timezone.now(),
                **{k: v for k, v in fields.items() if k in CASE_FIELDS})
    except IntegrityError:
        return OperationCase.objects.get(operation=operation, person=person), False
    category = Operation.VULNERABILITY_BY_KIND.get(operation.kind)
    if category:
        flag(person, category, source="operation", user=user, operation=operation, status="confirmed")
    if case.lost_livelihood:
        flag(person, "lost_livelihood", source="operation", user=user, operation=operation)
    if case.lost_documents:
        flag(person, "no_identity_document", source="operation", user=user, operation=operation)
    audit("operation.person_enrolled", obj=case, actor=user, data={"operation": operation.code,
                                                                     "person": person.public_code})
    return case, True


def transition_case(case: OperationCase, to: str, user, reason=""):
    if to not in OperationCase.TRANSITIONS.get(case.status, set()):
        raise IllegalTransition(f"Passage de « {case.get_status_display()} » à « {to} » impossible.")
    if to in {"closed", "lost_contact"} and not reason.strip():
        raise ValidationError({"reason": "Indiquez le motif."})
    previous = case.status
    case.status = to
    if to != "lost_contact":
        case.last_contact_at = timezone.now()
    case.save(update_fields=["status", "last_contact_at", "updated_at"])
    audit("operation.case_status_changed", obj=case, actor=user, reason=reason, data={"from": previous, "to": to})
    return case


def transition_operation(op: Operation, to: str, user, reason=""):
    if to not in Operation.TRANSITIONS.get(op.status, set()):
        raise IllegalTransition(f"Passage de « {op.get_status_display()} » à « {to} » impossible.")
    previous = op.status
    op.status = to
    op.save(update_fields=["status", "updated_at"])
    audit("operation.status_changed", obj=op, actor=user, reason=reason, data={"from": previous, "to": to})
    return op


def operation_stats(op: Operation) -> dict:
    """Tableau de bord d'une opération : qui a été identifié, accompagné, indemnisé, perdu de vue."""
    from apps.assistance.models import Assistance

    cases = op.cases.all()
    total = cases.count()
    by_status = dict(cases.values_list("status").annotate(n=Count("id")))
    needs = {}
    for lst in cases.values_list("needs", flat=True):
        for n in lst or []:
            needs[n] = needs.get(n, 0) + 1
    aids = Assistance.objects.filter(operation_case__operation=op)
    delivered = aids.filter(status="delivered")
    persons_helped = delivered.values("person").distinct().count()
    compensated = delivered.filter(category__in=["compensation", "relocation"]).values("person").distinct().count()
    return {
        "persons": total,
        "households": cases.exclude(household__isnull=True).values("household").distinct().count(),
        "by_status": by_status,
        "lost_housing": cases.filter(lost_housing=True).count(),
        "lost_livelihood": cases.filter(lost_livelihood=True).count(),
        "lost_documents": cases.filter(lost_documents=True).count(),
        "children_schooling_disrupted": cases.filter(children_schooling_disrupted=True).count(),
        "needs": needs,
        "persons_helped": persons_helped,
        "persons_without_aid": total - persons_helped,
        "persons_compensated": compensated,
        "compensation_due_total": cases.aggregate(s=Sum("compensation_due"))["s"] or 0,
        "aid_delivered_amount": delivered.aggregate(s=Sum("amount"))["s"] or 0,
        "aid_planned_amount": aids.filter(status="planned").aggregate(s=Sum("amount"))["s"] or 0,
        "aid_by_category": dict(delivered.values_list("category").annotate(n=Count("id"))),
        "lost_contact": cases.filter(Q(status="lost_contact")).count(),
    }
