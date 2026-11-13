from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.audit import audit
from apps.core.exceptions import IllegalTransition

from .models import RECORD_TRANSITIONS, DuplicateCandidate, Person


def detect_duplicates(person: Person) -> list[DuplicateCandidate]:
    """
    Indices de doublon gradués (REC-05). Ne fusionne jamais : crée des candidats à examiner.
    Critères : même téléphone (fort), même nom normalisé (moyen), même territoire et âge proche (bonus).
    """
    qs = Person.objects.filter(organization=person.organization, merged_into__isnull=True).exclude(pk=person.pk)
    cond = Q(search_name=person.search_name)
    if person.phone:
        cond |= Q(phone=person.phone)
    found = []
    for other in qs.filter(cond)[:20]:
        score, reasons = 0, []
        if person.phone and other.phone == person.phone:
            score += 55
            reasons.append("Même numéro de téléphone")
        if other.search_name == person.search_name:
            score += 35
            reasons.append("Même nom")
        if person.territory_id and other.territory_id == person.territory_id:
            score += 5
            reasons.append("Même territoire")
        if person.age is not None and other.age is not None and abs(person.age - other.age) <= 2:
            score += 5
            reasons.append("Âge proche")
        a, b = sorted([person, other], key=lambda p: str(p.pk))
        cand, _ = DuplicateCandidate.objects.get_or_create(
            person=a, other=b,
            defaults={"organization": person.organization, "confidence": min(score, 100), "reasons": reasons})
        found.append(cand)
    return found


def transition_person(person: Person, to_status: str, reason: str = ""):
    if to_status not in RECORD_TRANSITIONS.get(person.status, set()):
        raise IllegalTransition(f"Passage de « {person.get_status_display()} » à « {to_status} » impossible.")
    previous = person.status
    person.status = to_status
    person.save(update_fields=["status", "updated_at"])
    audit("person.status_changed", obj=person, reason=reason, data={"from": previous, "to": to_status})
    return person


@transaction.atomic
def merge_persons(duplicate: Person, keep: Person, user, reason: str):
    """Fusion logique et réversible : le doublon pointe vers le dossier conservé, rien n'est effacé."""
    if duplicate.organization_id != keep.organization_id:
        raise IllegalTransition("Fusion impossible entre organisations différentes.")
    if duplicate.pk == keep.pk or keep.merged_into_id:
        raise IllegalTransition("Le dossier conservé ne peut pas être lui-même fusionné.")
    duplicate.merged_into = keep
    duplicate.save(update_fields=["merged_into", "updated_at"])
    DuplicateCandidate.objects.filter(
        Q(person=duplicate, other=keep) | Q(person=keep, other=duplicate)
    ).update(status=DuplicateCandidate.Status.CONFIRMED, reviewed_by=user, reviewed_at=timezone.now())
    audit("person.merged", obj=duplicate, reason=reason, data={"kept": str(keep.pk)})


@transaction.atomic
def unmerge_person(person: Person, reason: str):
    if not person.merged_into_id:
        raise IllegalTransition("Ce dossier n'est pas fusionné.")
    kept = person.merged_into_id
    person.merged_into = None
    person.save(update_fields=["merged_into", "updated_at"])
    audit("person.unmerged", obj=person, reason=reason, data={"was_merged_into": str(kept)})
