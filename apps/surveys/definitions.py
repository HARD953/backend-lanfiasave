"""
Questionnaire de recensement v1 — douze rubriques (SRC-02, cahier §4.1).

Les valeurs spéciales ne sont PAS interchangeables (cahier §4.1) :
    __unknown__  inconnu           __refused__  refus de répondre
    __na__       non applicable    __absent__   personne absente
"""

SPECIAL_VALUES = {
    "__unknown__": "Ne sait pas",
    "__refused__": "Refus de répondre",
    "__na__": "Non applicable",
    "__absent__": "Personne absente",
}


def q(code, label, type_="single", options=None, required=False, sensitive=False, hint="", show_if=None):
    out = {"code": code, "label": label, "type": type_, "required": required, "sensitive": sensitive}
    if options:
        out["options"] = [{"value": v, "label": lab} for v, lab in options]
    if hint:
        out["hint"] = hint
    if show_if:
        out["show_if"] = show_if
    return out


YES_NO = [("yes", "Oui"), ("no", "Non")]

QUESTIONNAIRE_V1 = {
    "code": "RECENSEMENT",
    "version": 1,
    "title": "Recensement des personnes vulnérables",
    "special_values": SPECIAL_VALUES,
    "sections": [
        {"code": "identity", "title": "1. Identité", "icon": "person", "questions": [
            q("marital_status", "Situation matrimoniale", options=[
                ("single", "Célibataire"), ("married", "Marié(e)"), ("widowed", "Veuf / veuve"),
                ("divorced", "Divorcé(e) / séparé(e)")]),
            q("birth_certificate", "Possède un acte de naissance", options=YES_NO),
            q("id_document", "Possède une pièce d'identité", options=YES_NO),
        ]},
        {"code": "household", "title": "2. Ménage", "icon": "home", "questions": [
            q("household_size", "Nombre de personnes dans le ménage", "number", required=True),
            q("dependents", "Personnes à charge sans revenu", "number"),
            q("female_head", "Ménage dirigé par une femme", options=YES_NO),
        ]},
        {"code": "housing", "title": "3. Habitat et services", "icon": "business", "questions": [
            q("housing_type", "Type de logement", options=[
                ("precarious", "Précaire / de fortune"), ("rented", "Location"), ("shared", "Hébergé(e)"),
                ("owned", "Propriétaire")], required=True),
            q("water_access", "Accès à l'eau potable", options=[
                ("none", "Aucun / source non sûre"), ("shared", "Point d'eau partagé"), ("private", "Robinet privé")]),
            q("electricity", "Accès à l'électricité", options=[
                ("none", "Aucun"), ("informal", "Branchement informel"), ("yes", "Raccordé")]),
        ]},
        {"code": "education", "title": "4. Éducation et compétences", "icon": "school", "questions": [
            q("literacy", "Sait lire et écrire", options=[("no", "Non"), ("partial", "Un peu"), ("yes", "Oui")]),
            q("education_level", "Niveau d'études", options=[
                ("none", "Aucun"), ("primary", "Primaire"), ("secondary", "Secondaire"), ("higher", "Supérieur")]),
            q("children_out_of_school", "Des enfants en âge scolaire ne vont pas à l'école", options=YES_NO),
            q("skills", "Savoir-faire / métiers pratiqués", "text"),
        ]},
        {"code": "income", "title": "5. Ressources et emploi", "icon": "cash", "questions": [
            q("activity_status", "Activité principale", options=[
                ("none", "Sans activité"), ("informal", "Activité informelle / irrégulière"),
                ("stable", "Emploi ou activité stable")], required=True),
            q("monthly_income", "Revenu mensuel du ménage (FCFA)", options=[
                ("none", "Aucun revenu"), ("lt25k", "Moins de 25 000"), ("25to60k", "25 000 à 60 000"),
                ("gt60k", "Plus de 60 000")], required=True),
            q("earners", "Nombre de personnes qui gagnent un revenu", "number"),
        ]},
        {"code": "health", "title": "6. Santé et handicap", "icon": "medkit", "questions": [
            q("chronic_illness", "Maladie chronique dans le ménage", options=YES_NO, sensitive=True),
            q("disability", "Situation de handicap", options=[
                ("none", "Aucune"), ("moderate", "Modérée"), ("severe", "Sévère")], sensitive=True),
            q("health_access", "Accès aux soins", options=[
                ("no", "Pas d'accès"), ("difficult", "Difficile"), ("easy", "Facile")]),
            q("health_coverage", "Couverture maladie (CMU ou équivalent)", options=YES_NO),
        ]},
        {"code": "protection", "title": "7. Protection et juridique", "icon": "shield-checkmark", "questions": [
            q("protection_risk", "Situation de risque ou de danger signalée", options=YES_NO, sensitive=True,
              hint="Ne pas détailler ici : orienter vers le canal de protection compétent."),
            q("legal_issue", "Démarche juridique ou administrative en suspens", options=YES_NO),
        ]},
        {"code": "context", "title": "8. Contexte", "icon": "warning", "questions": [
            q("shock_recent", "Choc récent (inondation, déguerpissement, décès, perte d'emploi)", options=YES_NO),
            q("shock_type", "Nature du choc", options=[
                ("flood", "Inondation"), ("eviction", "Déguerpissement"), ("death", "Décès"),
                ("job_loss", "Perte d'emploi"), ("other", "Autre")], show_if={"shock_recent": "yes"}),
        ]},
        {"code": "social_support", "title": "9. Soutien social", "icon": "people", "questions": [
            q("support_network", "Personnes sur qui compter en cas de besoin", options=[
                ("none", "Personne"), ("weak", "Peu de soutien"), ("strong", "Bon soutien")]),
            q("lives_alone", "Vit seul(e)", options=YES_NO),
        ]},
        {"code": "psychosocial", "title": "10. Perception psychosociale", "icon": "heart", "questions": [
            q("self_assessment", "Comment jugez-vous votre situation actuelle ?", options=[
                ("very_difficult", "Très difficile"), ("difficult", "Difficile"), ("manageable", "Supportable"),
                ("good", "Bonne")]),
            q("main_need", "Besoin prioritaire exprimé", options=[
                ("food", "Alimentation"), ("housing", "Logement"), ("health", "Santé"), ("training", "Formation"),
                ("work", "Travail / activité"), ("documents", "Papiers"), ("other", "Autre")]),
        ]},
        {"code": "aid_received", "title": "11. Aides reçues", "icon": "gift", "questions": [
            q("aid_last_12m", "Aide reçue au cours des 12 derniers mois", options=YES_NO),
            q("aid_source", "Source de l'aide", "text", show_if={"aid_last_12m": "yes"}),
        ]},
        {"code": "territory", "title": "12. Territoire", "icon": "map", "questions": [
            q("years_in_locality", "Années de résidence dans la localité", "number"),
            q("risk_zone_declared", "Logement en zone exposée (inondation, éboulement)", options=YES_NO,
              hint="Information de contexte : n'augmente pas le score individuel."),
        ]},
    ],
}
