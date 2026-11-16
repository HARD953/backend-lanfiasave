"""
Crée un compte nominatif dans une organisation existante, avec un ou plusieurs rôles.
Sert surtout à ouvrir les premiers comptes terrain (app Collecte) avant que le portail soit en ligne.

    python manage.py create_account --username a.kone --role enumerator
    python manage.py create_account --username m.traore --role social_worker --role field_supervisor

Le mot de passe est demandé de façon masquée (jamais passé en argument ni journalisé).
Ce compte n'a pas accès à l'administration Django.
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


class Command(BaseCommand):
    help = "Crée un compte nominatif (ex. agent recenseur pour l'app Collecte) dans une organisation existante."

    def add_arguments(self, parser):
        parser.add_argument("--username")
        parser.add_argument("--first-name", default="")
        parser.add_argument("--last-name", default="")
        parser.add_argument("--email", default="")
        parser.add_argument("--code", default="lanfiatech", help="Code de l'organisation (défaut : lanfiatech)")
        parser.add_argument("--role", action="append", choices=Role.values,
                            help="Rôle à donner (répétable). Défaut : enumerator (agent recenseur).")

    def handle(self, *args, **opts):
        org = Organization.objects.filter(code=opts["code"]).first()
        if org is None:
            raise CommandError(f"Organisation « {opts['code']} » introuvable : créez d'abord l'administrateur "
                               "(bootstrap_admin).")
        roles = [Role(r) for r in (opts["role"] or [Role.ENUMERATOR])]
        username = opts["username"] or input("Identifiant de connexion : ").strip()
        if not username:
            raise CommandError("Identifiant obligatoire.")
        if User.objects.filter(username=username).exists():
            raise CommandError(f"Le compte « {username} » existe déjà.")
        first = opts["first_name"] or input("Prénom : ").strip()
        last = opts["last_name"] or input("Nom : ").strip()
        password = self._ask_password(username)

        with transaction.atomic():
            user = User(username=username, first_name=first, last_name=last, email=opts["email"])
            user.set_password(password)
            user.save()
            for role in roles:
                Membership.objects.create(user=user, organization=org, role=role, program=None, is_active=True)
            audit("account.created_cli", obj=user, organization=org,
                  data={"username": username, "roles": [r.value for r in roles]})

        self.stdout.write(self.style.SUCCESS(
            f"Compte « {username} » créé dans « {org.name} » : {', '.join(r.label for r in roles)}."))

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
