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
    # Dispositif d'accompagnement des ménages affectés (mines, pétrole, énergie)
    RELAY = "relay", "Relais communautaire"
    TECHNICAL_SERVICE = "technical_service", "Service technique (agriculture, hydraulique, pêche, mines)"
    PREFECT = "prefect", "Préfet / sous-préfet (validation, urgences)"
    APPEAL_INDEPENDENT = "appeal_independent", "Membre indépendant de la commission de recours"
    PAYER = "payer", "Payeur / opérateur de paiement"
    OPERATOR = "operator", "Exploitant (mine, pétrole, énergie)"
    MINISTRY = "ministry", "Ministère (vue agrégée sans nom)"
    EVALUATOR = "evaluator", "Évaluateur indépendant"


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
    # Administration des comptes
    "user.manage": "Créer, modifier et désactiver les comptes de l'organisation",
    # Structures partenaires (gouvernement, collectivités, entreprises minières, ONG…)
    "partner.view": "Consulter les structures partenaires",
    "partner.manage": "Gérer les structures partenaires",
    # Opérations / événements (déguerpissements, catastrophes, sites miniers…)
    "operation.view": "Consulter les opérations et les personnes affectées",
    "operation.manage": "Créer et piloter les opérations",
    "operation.enroll": "Ouvrir des dossiers de personnes affectées",
    # Aides
    "assistance.view": "Consulter les aides",
    "assistance.record": "Planifier une aide",
    "assistance.deliver": "Confirmer la remise d'une aide",
    # Profils de vulnérabilité
    "vulnerability.manage": "Qualifier les vulnérabilités d'une personne",
    # Suivi social : plaintes, orientations, visites
    "grievance.view": "Consulter les plaintes et réclamations",
    "grievance.manage": "Enregistrer et traiter les plaintes",
    "referral.view": "Consulter les orientations vers les services",
    "referral.manage": "Orienter une personne et saisir le retour du service",
    "visit.view": "Consulter les visites de suivi",
    "visit.record": "Enregistrer une visite de suivi",
    # Demandes des ménages affectés (circuit : enregistrement → décision motivée → recours)
    "claim.create": "Enregistrer une demande et remettre le récépissé",
    "claim.view_own": "Voir les demandes qu'on a saisies, jusqu'à leur transmission",
    "claim.view": "Consulter les demandes de sa zone",
    "claim.process": "Trier, vérifier, orienter et suivre une demande (cellule locale)",
    "claim.technical_opinion": "Donner l'avis du service technique",
    "claim.propose": "Proposer une décision motivée (comité local)",
    "claim.validate": "Valider une décision (préfet ou délégué)",
    "claim.urgent_order": "Ordonner une mesure d'urgence (72 h)",
    "claim.appeal_file": "Enregistrer une contestation",
    "claim.appeal_resolve": "Statuer en commission de recours",
    "claim.operator": "Recevoir et répondre aux réclamations transmises (exploitant)",
    "claim.pay": "Exécuter les paiements validés",
    "claim.evaluate": "Auditer un échantillon de dossiers tiré au sort",
    "claim.import_list": "Importer la liste des personnes affectées de l'exploitant",
    "protected.view": "Voir et traiter les plaintes confidentielles et les représailles",
    "protected.file": "Enregistrer une plainte confidentielle",
    "report.ministry": "Consulter le tableau de bord agrégé du dispositif",
}

_VIEW = {"territory.view", "person.view", "survey.view", "score.view", "program.view", "application.view"}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    Role.ORG_ADMIN: {"territory.view", "territory.manage", "organization.manage_members", "program.view",
                     "report.aggregate", "audit.view", "user.manage", "partner.view", "partner.manage",
                     "operation.view", "assistance.view",
        "grievance.manage", "grievance.view", "referral.view", "visit.view"},
    Role.PROGRAM_MANAGER: _VIEW | {"program.manage", "report.aggregate", "application.create", "partner.view",
                                   "partner.manage", "operation.view", "operation.manage", "operation.enroll",
                                   "assistance.view", "assistance.record",
        "grievance.manage", "grievance.view", "referral.view", "visit.view"},
    Role.SOCIAL_WORKER: _VIEW | {"person.view_sensitive", "person.create", "person.update", "survey.submit",
                                 "score.compute", "application.create", "application.review", "appeal.create",
                                 "report.aggregate", "partner.view", "operation.view", "operation.enroll",
                                 "assistance.view", "assistance.record", "assistance.deliver", "vulnerability.manage",
        "grievance.manage", "grievance.view", "referral.manage", "referral.view", "visit.record", "visit.view"},
    Role.COMMITTEE_MEMBER: _VIEW | {"decision.create", "appeal.resolve", "operation.view", "assistance.view",
        "grievance.view", "referral.view"},
    Role.ENUMERATOR: {"territory.view", "person.view", "person.create", "person.update", "survey.view",
                      "survey.submit", "program.view", "operation.view", "operation.enroll",
        "visit.record", "visit.view"},
    Role.FIELD_SUPERVISOR: _VIEW | {"person.create", "person.update", "person.verify", "person.merge",
                                    "survey.submit", "report.aggregate", "partner.view", "operation.view",
                                    "operation.enroll", "assistance.view", "assistance.deliver",
                                    "vulnerability.manage",
        "grievance.manage", "grievance.view", "referral.manage", "referral.view", "visit.record", "visit.view"},
    Role.TRAINING_MANAGER: {"territory.view", "person.view", "program.view", "report.aggregate",
        "referral.view"},
    Role.INSERTION_OFFICER: {"territory.view", "person.view", "program.view", "report.aggregate",
                             "assistance.view", "assistance.record",
        "referral.manage", "referral.view", "visit.record", "visit.view"},
    Role.MENTOR: {"territory.view", "person.view",
        "visit.record", "visit.view"},
    Role.FINANCE_OFFICER: {"program.view", "report.aggregate", "partner.view", "operation.view", "person.view",
                           "assistance.view", "assistance.record", "assistance.deliver",
        "grievance.view"},
    Role.FUNDER: {"program.view", "report.aggregate"},
    Role.AUDITOR: {"audit.view", "report.aggregate", "program.view", "operation.view", "assistance.view",
                   "partner.view",
        "grievance.view", "referral.view", "visit.view"},
    Role.RELAY: {"claim.create", "claim.view_own", "protected.file", "territory.view"},
    Role.TECHNICAL_SERVICE: {"claim.view", "claim.technical_opinion", "territory.view"},
    Role.PREFECT: {"claim.view", "claim.validate", "claim.urgent_order", "claim.appeal_resolve", "report.ministry",
                   "territory.view"},
    Role.APPEAL_INDEPENDENT: {"claim.view", "claim.appeal_resolve", "protected.view", "protected.file",
                              "territory.view"},
    Role.PAYER: {"claim.pay"},
    Role.OPERATOR: {"claim.operator"},
    Role.MINISTRY: {"report.ministry", "territory.view"},
    Role.EVALUATOR: {"claim.evaluate", "report.ministry", "territory.view"},
}

# Dispositif MMPE : droits ajoutés aux rôles existants.
_CLAIM_EXTRA = {
    Role.ORG_ADMIN: {"claim.view", "report.ministry", "protected.file"},
    Role.PROGRAM_MANAGER: {"claim.view", "claim.import_list", "report.ministry", "protected.file"},
    Role.SOCIAL_WORKER: {"claim.create", "claim.view", "claim.process", "claim.appeal_file", "claim.import_list",
                         "protected.file"},
    Role.COMMITTEE_MEMBER: {"claim.view", "claim.propose"},
    Role.FIELD_SUPERVISOR: {"claim.create", "claim.view", "claim.process", "claim.appeal_file", "protected.file"},
    Role.ENUMERATOR: {"claim.create", "claim.view_own", "protected.file"},
    Role.AUDITOR: {"report.ministry"},
}
for _role, _perms in _CLAIM_EXTRA.items():
    ROLE_PERMISSIONS[_role] = ROLE_PERMISSIONS[_role] | _perms

# Rôles qui ne voient JAMAIS de données nominatives dans le dispositif (tableau de bord agrégé seulement).
AGGREGATE_ONLY_ROLES = {Role.MINISTRY}

assert all(p in P for perms in ROLE_PERMISSIONS.values() for p in perms), "Permission inconnue dans la matrice"
