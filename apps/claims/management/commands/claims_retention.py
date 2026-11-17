"""
Conservation (diapositive 22, à valider avec l'ARTCI) :
- à la clôture, les éléments libres non nécessaires au suivi sont effacés, sauf litige (contestation ouverte) ;
- trois ans après la clôture, le dossier est anonymisé (nom, téléphone, année de naissance, texte libre).

    python manage.py claims_retention [--dry-run] [--years 3]
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.claims.models import Claim, ClaimAppeal, ProtectedReport
from apps.core.audit import audit


class Command(BaseCommand):
    help = "Applique les durées de conservation des demandes (purge à la clôture, anonymisation à 3 ans)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--years", type=int, default=3)

    @transaction.atomic
    def handle(self, *args, dry_run=False, years=3, **opts):
        closed = Claim.objects.filter(status=Claim.Status.CLOSED, anonymized_at__isnull=True) \
            .exclude(appeals__status=ClaimAppeal.Status.OPEN)
        purge = closed.exclude(assets_lost="", community_validation_note="")
        limit = timezone.now() - timedelta(days=365 * years)
        old = closed.filter(closed_at__lt=limit)
        old_reports = ProtectedReport.objects.filter(status="closed", resolved_at__lt=limit) \
            .exclude(reporter_name="", reporter_phone="", description="Anonymisé")
        self.stdout.write(f"Pièces à effacer : {purge.count()} dossiers clôturés ; "
                          f"à anonymiser : {old.count()} dossiers et {old_reports.count()} plaintes confidentielles.")
        if dry_run:
            return
        n_purge = purge.update(assets_lost="", community_validation_note="")
        n_old = 0
        for c in old:
            c.claimant_name, c.claimant_phone, c.claimant_birth_year = "Anonymisé", "", None
            c.description = c.social_inquiry = c.technical_opinion = c.operator_response = ""
            c.aid_received = c.override_motive = ""
            c.person = None
            c.anonymized_at = timezone.now()
            c.save()
            n_old += 1
        n_rep = old_reports.update(reporter_name="", reporter_phone="", description="Anonymisé")
        audit("claim.retention_applied", data={"purged": n_purge, "anonymized": n_old, "protected": n_rep})
        self.stdout.write(self.style.SUCCESS(f"Effacés : {n_purge} ; anonymisés : {n_old} dossiers, {n_rep} plaintes."))
