"""
Référentiel des impacts des activités minières, pétrolières et énergétiques (proposition MMPE, diapositive 6).

Chaque impact indique les secteurs où il se rencontre et le service technique qui donne son avis.
Référentiel adaptable : il est arrêté par le comité de pilotage au mois 1 du pilote.
"""

SECTORS = [
    ("mining", "Mines"),
    ("oil_gas", "Pétrole et gaz"),
    ("energy", "Énergie"),
]

SERVICES = [
    ("agriculture", "Agriculture"),
    ("hydraulics", "Hydraulique"),
    ("fisheries", "Pêche et ressources halieutiques"),
    ("mines", "Administration des Mines"),
    ("energy", "Énergie"),
    ("social", "Services sociaux"),
]

# code, libellé, secteurs, service technique
IMPACTS = [
    ("land_loss", "Perte de terres de culture", ["mining", "energy"], "agriculture"),
    ("crop_loss", "Cultures ou arbres détruits", ["mining", "oil_gas", "energy"], "agriculture"),
    ("water_access", "Accès à l'eau coupé ou dégradé", ["mining", "oil_gas", "energy"], "hydraulics"),
    ("access_cut", "Accès coupé aux pistes, pâturages ou zones de cueillette", ["mining", "energy"], "agriculture"),
    ("artisanal_site_closed", "Fermeture d'un site d'orpaillage", ["mining"], "mines"),
    ("job_loss", "Perte d'un emploi agricole ou d'une clientèle", ["mining", "oil_gas", "energy"], "social"),
    ("market_moved", "Marché ou activité commerciale déplacé", ["mining", "oil_gas", "energy"], "social"),
    ("rent_increase", "Hausse des loyers pendant la construction", ["mining", "energy"], "social"),
    ("fishing_restricted", "Accès restreint aux zones de pêche", ["oil_gas", "energy"], "fisheries"),
    ("landing_disrupted", "Débarcadère perturbé", ["oil_gas"], "fisheries"),
    ("pipeline_works", "Travaux de pipeline à terre", ["oil_gas"], "energy"),
    ("land_flooded", "Terres ennoyées par une retenue", ["energy"], "agriculture"),
    ("downstream_changes", "Débit ou pêche modifiés en aval d'un barrage", ["energy"], "fisheries"),
    ("line_servitude", "Terrain grevé d'une servitude sous une ligne", ["energy"], "energy"),
    ("housing_damage", "Habitation endommagée (fissures, poussière)", ["mining", "oil_gas", "energy"], "mines"),
    ("other", "Autre impact", ["mining", "oil_gas", "energy"], "social"),
]
IMPACT_CODES = {c for c, *_ in IMPACTS}
IMPACT_LABELS = {c: label for c, label, *_ in IMPACTS}
IMPACT_SERVICE = {c: service for c, _, _, service in IMPACTS}

# Ménages à repérer, par secteur (aide au repérage pour les relais).
WATCH_LIST = {
    "mining": "Métayers et ouvriers agricoles, femmes vivant de la cueillette, personnes recensées après la "
              "date butoir",
    "oil_gas": "Pêcheurs artisanaux, mareyeuses et transformatrices, équipages de pirogues",
    "energy": "Riverains des retenues, pêcheurs fluviaux, exploitants grevés de servitudes",
}

PROFILES = [
    ("vulnerable", "Déjà vulnérable", "Personne âgée seule, femme cheffe de ménage, personne dépendante : "
                                      "le projet aggrave la situation, même sans impact direct."),
    ("weakened", "Fragilisé par l'activité", "La perte d'une parcelle, d'un emploi ou d'une clientèle fait basculer "
                                             "le ménage."),
    ("indirect", "Touché indirectement", "Aucun bien recensé perdu, mais un revenu perdu (ouvrier, commerçante, "
                                         "pêcheur non propriétaire)."),
]

# Sources de repérage, chacune avec l'accord de son détenteur.
SOURCES = [
    ("self", "Le ménage s'est signalé lui-même"),
    ("operator_list", "Liste des personnes affectées de l'exploitant"),
    ("rsu", "Registre social unique (si l'accès est autorisé)"),
    ("relay", "Signalement d'un relais communautaire"),
    ("community", "Validation communautaire devant l'autorité"),
]

URGENCY_REASONS = [
    ("water", "Eau"),
    ("food", "Alimentation"),
    ("health", "Santé"),
    ("security", "Sécurité"),
    ("shelter", "Abri"),
]


def as_options(rows):
    return [{"value": r[0], "label": r[1]} for r in rows]


def meta():
    return {
        "sectors": as_options(SECTORS),
        "services": as_options(SERVICES),
        "impacts": [{"value": c, "label": label, "sectors": sectors, "service": service}
                    for c, label, sectors, service in IMPACTS],
        "watch_list": WATCH_LIST,
        "profiles": [{"value": c, "label": label, "help": help_} for c, label, help_ in PROFILES],
        "sources": as_options(SOURCES),
        "urgency_reasons": as_options(URGENCY_REASONS),
    }
