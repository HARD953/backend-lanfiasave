"""
Catalogue des situations de vulnérabilité suivies par LanfiaSave, adapté au contexte ivoirien :
déguerpissements urbains, catastrophes (inondations, éboulements), zones minières et orpaillage,
défaut d'état civil et risque d'apatridie, travail des enfants, VBG, handicap, vieillesse isolée…

`sensitive=True` : visible uniquement par les rôles habilités aux données sensibles (person.view_sensitive),
jamais dans les exports financeurs ni les notifications.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    code: str
    label: str
    group: str
    sensitive: bool = False


GROUPS = {
    "displacement": "Déplacement et perte de logement",
    "children": "Enfance",
    "identity": "État civil et statut",
    "health": "Santé et handicap",
    "household": "Situation familiale",
    "economic": "Situation économique et alimentaire",
    "protection": "Protection",
}

CATALOG = [
    Category("evicted", "Déguerpi(e) / expulsé(e) de son logement", "displacement"),
    Category("disaster_victim", "Sinistré(e) (inondation, éboulement, incendie)", "displacement"),
    Category("idp", "Déplacé(e) interne (conflit, insécurité)", "displacement"),
    Category("mining_affected", "Affecté(e) par l'activité minière / orpaillage (terres, pollution)", "displacement"),
    Category("homeless", "Sans abri / logement extrêmement précaire", "displacement"),
    Category("returnee_migrant", "Migrant(e) de retour", "displacement"),
    Category("orphan_ovc", "Orphelin(e) ou enfant vulnérable (OEV)", "children"),
    Category("child_labour", "Enfant au travail (mines, orpaillage, plantations, commerce)", "children", True),
    Category("street_child", "Enfant en situation de rue", "children", True),
    Category("unaccompanied_minor", "Mineur(e) non accompagné(e) ou séparé(e)", "children", True),
    Category("out_of_school", "Enfant non scolarisé(e) ou déscolarisé(e)", "children"),
    Category("malnutrition", "Malnutrition (enfant ou adulte)", "children"),
    Category("no_birth_certificate", "Sans acte de naissance (risque d'apatridie)", "identity"),
    Category("no_identity_document", "Sans pièce d'identité", "identity"),
    Category("refugee_stateless", "Réfugié(e), demandeur(se) d'asile ou apatride", "identity", True),
    Category("disability", "Personne en situation de handicap", "health"),
    Category("chronic_illness", "Maladie chronique grave (VIH, drépanocytose, cancer…)", "health", True),
    Category("mental_health", "Trouble de santé mentale", "health", True),
    Category("albinism", "Personne atteinte d'albinisme", "health", True),
    Category("pregnant_lactating", "Femme enceinte ou allaitante", "household"),
    Category("female_head", "Femme cheffe de ménage", "household"),
    Category("widow", "Veuve / veuf sans soutien", "household"),
    Category("elderly_isolated", "Personne âgée isolée ou dépendante", "household"),
    Category("large_household", "Ménage nombreux à charge (≥ 5 dépendants)", "household"),
    Category("food_insecure", "Insécurité alimentaire sévère", "economic"),
    Category("no_income", "Aucun revenu", "economic"),
    Category("unemployed_youth", "Jeune sans emploi ni formation", "economic"),
    Category("lost_livelihood", "A perdu son activité / outil de travail", "economic"),
    Category("gbv_survivor", "Survivant(e) de violences basées sur le genre", "protection", True),
    Category("former_detainee", "Ancien(ne) détenu(e) en réinsertion", "protection", True),
    Category("drug_user", "Usager(ère) de drogues", "protection", True),
]

BY_CODE = {c.code: c for c in CATALOG}
CHOICES = [(c.code, c.label) for c in CATALOG]
SENSITIVE_CODES = {c.code for c in CATALOG if c.sensitive}


def catalog_payload(include_sensitive: bool):
    return [{"code": c.code, "label": c.label, "group": c.group, "group_label": GROUPS[c.group],
             "sensitive": c.sensitive} for c in CATALOG if include_sensitive or not c.sensitive]


# Déduction de situations « à confirmer » à partir des réponses au questionnaire.
# Une déduction n'est jamais une décision : un agent habilité confirme ou écarte.
def derive_from_answers(answers: dict, person=None) -> set[str]:
    a = answers or {}
    found = set()

    def eq(code, *values):
        return a.get(code) in values

    def num(code):
        v = a.get(code)
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    if eq("displacement_status", "evicted") or eq("shock_type", "eviction"):
        found.add("evicted")
    if eq("shock_type", "flood", "landslide", "fire"):
        found.add("disaster_victim")
    if eq("displacement_status", "displaced") or eq("shock_type", "conflict_displacement"):
        found.add("idp")
    if eq("displacement_status", "returnee"):
        found.add("returnee_migrant")
    if eq("mining_zone", "yes") or eq("shock_type", "mining_impact"):
        found.add("mining_affected")
    if eq("housing_type", "street"):
        found.add("homeless")
    if eq("birth_certificate", "no"):
        found.add("no_birth_certificate")
    if eq("id_document", "no"):
        found.add("no_identity_document")
    if eq("disability", "moderate", "severe"):
        found.add("disability")
    if eq("chronic_illness", "yes"):
        found.add("chronic_illness")
    if eq("child_malnutrition", "yes"):
        found.add("malnutrition")
    if eq("child_labour", "yes"):
        found.add("child_labour")
    if eq("children_out_of_school", "yes"):
        found.add("out_of_school")
    if eq("pregnant_lactating", "yes"):
        found.add("pregnant_lactating")
    if eq("female_head", "yes"):
        found.add("female_head")
    if eq("marital_status", "widowed") and eq("support_network", "none", "weak"):
        found.add("widow")
    if eq("lives_alone", "yes") and person is not None and (person.age or 0) >= 60:
        found.add("elderly_isolated")
    if (num("dependents") or 0) >= 5:
        found.add("large_household")
    if eq("meals_per_day", "one", "none") or (num("days_without_food") or 0) >= 3:
        found.add("food_insecure")
    if eq("monthly_income", "none"):
        found.add("no_income")
    if eq("lost_livelihood", "yes"):
        found.add("lost_livelihood")
    if eq("gbv_disclosed", "yes"):
        found.add("gbv_survivor")
    return found
