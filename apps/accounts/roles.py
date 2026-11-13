"""
Rôles métier (cahier §2.1) et matrice de permissions.

Principe : le super-administrateur technique n'a AUCUN droit implicite sur les données sociales.
Les droits sur les données découlent uniquement d'une adhésion (Membership) à une organisation.
"""
from django.db import models


class Role(models.TextChoices):
    ORG_ADMIN = "org_admin", "Administrateur d'organisation"
    PROGRAM_MANAGER = "program_manager", "Responsable de programme"
    SOCIAL_WORKER = "social_worker", "Travailleur social / gestionnaire"
    COMMITTEE_MEMBER = "committee_member", "Membre du comité de décision"
    ENUMERATOR = "enumerator", "Agent recenseur"
    FIELD_SUPERVISOR = "field_supervisor", "Superviseur terrain"
    TRAINING_MANAGER = "training_manager", "Gestionnaire formation"
    INSERTION_OFFICER = "insertion_officer", "Responsable insertion / AGR"
    MENTOR = "mentor", "Mentor / accompagnateur"
    FINANCE_OFFICER = "finance_officer", "Responsable financier"
    FUNDER = "funder", "Donateur / financeur"
    AUDITOR = "auditor", "Auditeur / DPO"


P = {
    # Référentiels
    "territory.view": "Consulter les territoires",
    "territory.manage": "Administrer les territoires",
    "organization.manage_members": "Gérer les membres de l'organisation",
    # Personnes et ménages
    "person.view": "Consulter les dossiers personnes / ménages",
    "person.view_sensitive": "Voir les champs sensibles non masqués",
    "person.create": "Créer un dossier",
    "person.update": "Modifier un dossier",
    "person.verify": "Valider le contrôle qualité d'un dossier",
    "person.merge": "Fusionner des doublons",
    # Enquêtes et scoring
    "survey.view": "Consulter les enquêtes",
    "survey.submit": "Saisir une enquête",
    "score.view": "Consulter les scores",
    "score.compute": "Lancer un calcul de score",
    # Programmes et instruction
    "program.view": "Consulter les programmes",
    "program.manage": "Créer / modifier les programmes",
    "application.view": "Consulter les candidatures",
    "application.create": "Créer une candidature",
    "application.review": "Instruire (recevabilité, avis social)",
    "decision.create": "Prendre une décision",
    "appeal.create": "Déposer un recours pour un bénéficiaire",
    "appeal.resolve": "Traiter un recours",
    # Pilotage
    "report.aggregate": "Consulter les indicateurs agrégés",
    "audit.view": "Consulter le journal d'audit",
}

_VIEW = {"territory.view", "person.view", "survey.view", "score.view", "program.view", "application.view"}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    Role.ORG_ADMIN: {"territory.view", "territory.manage", "organization.manage_members", "program.view",
                     "report.aggregate", "audit.view"},
    Role.PROGRAM_MANAGER: _VIEW | {"program.manage", "report.aggregate", "application.create"},
    Role.SOCIAL_WORKER: _VIEW | {"person.view_sensitive", "person.create", "person.update", "survey.submit",
                                 "score.compute", "application.create", "application.review", "appeal.create",
                                 "report.aggregate"},
    Role.COMMITTEE_MEMBER: _VIEW | {"decision.create", "appeal.resolve"},
    Role.ENUMERATOR: {"territory.view", "person.view", "person.create", "person.update", "survey.view",
                      "survey.submit", "program.view"},
    Role.FIELD_SUPERVISOR: _VIEW | {"person.create", "person.update", "person.verify", "person.merge",
                                    "survey.submit", "report.aggregate"},
    Role.TRAINING_MANAGER: {"territory.view", "person.view", "program.view", "report.aggregate"},
    Role.INSERTION_OFFICER: {"territory.view", "person.view", "program.view", "report.aggregate"},
    Role.MENTOR: {"territory.view", "person.view"},
    Role.FINANCE_OFFICER: {"program.view", "report.aggregate"},
    Role.FUNDER: {"program.view", "report.aggregate"},
    Role.AUDITOR: {"audit.view", "report.aggregate", "program.view"},
}

assert all(p in P for perms in ROLE_PERMISSIONS.values() for p in perms), "Permission inconnue dans la matrice"
