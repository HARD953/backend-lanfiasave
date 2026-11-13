"""
Crée le premier compte administrateur d'une installation (base vide) :
organisation + utilisateur + rôles métier + accès à l'administration Django.

    python manage.py bootstrap_admin
    python manage.py bootstrap_admin --username issa --organization "LanfiaTech" --code lanfiatech

Le mot de passe est demandé de façon masquée (jamais passé en argument ni journalisé).
Rappel : le statut super-administrateur seul ne donne AUCUN accès aux données sociales ;
ce sont les adhésions (rôles) à l'organisation qui ouvrent le portail.
"""
import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import Membership, User
from apps.accounts.roles import Role
from apps.core.audit import audit
from apps.organizations.models import Organization

# Rôles du compte d'amorçage. À restreindre ensuite en créant des comptes nominatifs par fonction.
BOOTSTRAP_ROLES = [
    Role.ORG_ADMIN,
    Role.PROGRAM_MANAGER,
    Role.SOCIAL_WORKER,
    Role.COMMITTEE_MEMBER,
    Role.FIELD_SUPERVISOR,
    Role.AUDITOR,
]


class Command(BaseCommand):
    help = "Crée l'organisation et le premier administrateur (rôles métier + administration Django)."

    def add_arguments(self, parser):
        parser.add_argument("--username")
        parser.add_argument("--first-name", default="")
        parser.add_argument("--last-name", default="")
        parser.add_argument("--email", default="")
        parser.add_argument("--organization", default="LanfiaTech")
        parser.add_argument("--code", default="lanfiatech")
        parser.add_argument("--kind", default=Organization.Kind.OPERATOR, choices=Organization.Kind.values)

    def handle(self, *args, **opts):
        username = opts["username"] or input("Identifiant de connexion : ").strip()
        if not username:
            raise CommandError("Identifiant obligatoire.")
        first = opts["first_name"] or input("Prénom : ").strip()
        last = opts["last_name"] or input("Nom : ").strip()

        user = User.objects.filter(username=username).first()
        password = None
        if user is None or input("Le compte existe : redéfinir son mot de passe ? [o/N] ").strip().lower() == "o":
            password = self._ask_password(username)

        with transaction.atomic():
            org, org_created = Organization.objects.get_or_create(
                code=opts["code"], defaults={"name": opts["organization"], "kind": opts["kind"]})
            if user is None:
                user = User(username=username)
            user.first_name, user.last_name = first or user.first_name, last or user.last_name
            user.email = opts["email"] or user.email
            user.is_staff = user.is_superuser = True
            if password:
                user.set_password(password)
            user.save()
            created_roles = []
            for role in BOOTSTRAP_ROLES:
                _, created = Membership.objects.get_or_create(user=user, organization=org, role=role, program=None,
                                                              is_active=True)
                if created:
                    created_roles.append(role.label)
            audit("account.bootstrap_admin", obj=org, actor=user, organization=org,
                  data={"username": username, "roles": [r.value for r in BOOTSTRAP_ROLES]})

        self.stdout.write(self.style.SUCCESS(
            f"Organisation « {org.name} » {'créée' if org_created else 'existante'}. "
            f"Compte « {username} » prêt avec {len(BOOTSTRAP_ROLES)} rôles."))
        if created_roles:
            self.stdout.write("Rôles ajoutés : " + ", ".join(created_roles))

    def _ask_password(self, username):
        for _ in range(3):
            pwd = getpass.getpass("Mot de passe (10 caractères minimum) : ")
            if pwd != getpass.getpass("Confirmez le mot de passe : "):
                self.stderr.write("Les mots de passe ne correspondent pas.")
                continue
            try:
                validate_password(pwd, User(username=username))
                return pwd
            except ValidationError as exc:
                self.stderr.write(" ".join(exc.messages))
        raise CommandError("Mot de passe non défini.")
