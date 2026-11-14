"""
Indice de pauvreté multidimensionnelle (IPM) calculé à partir du recensement LanfiaSave.

Méthode (double seuil) :
1. Pour chaque ménage, chaque indicateur dit si le ménage est « privé » (oui / non / inconnu).
2. Les cinq dimensions pèsent chacune 1/5 ; le poids d'une dimension est partagé entre ses indicateurs.
3. Le score de privation d'un ménage est la somme des poids des privations, rapportée aux poids renseignés.
   Un ménage dont moins de la moitié des poids est renseignée est « à compléter » et n'entre pas dans le calcul.
4. Un ménage est pauvre si son score atteint 1/3 ; en pauvreté sévère s'il atteint 1/2.
5. Taux de pauvreté H = ménages pauvres / ménages évalués ; intensité A = score moyen des ménages pauvres ;
   IPM = H × A (entre 0 et 1).

Les questions sensibles (maladie, handicap, violences) ne sont jamais utilisées ici.
Le seuil de revenu est un paramètre (LANFIA_POVERTY_LINE) à aligner sur le seuil national en vigueur.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

POVERTY_CUTOFF = 1 / 3
SEVERE_CUTOFF = 1 / 2
MIN_ANSWERED_WEIGHT = 0.5
POVERTY_LINE = int(os.getenv("LANFIA_POVERTY_LINE", "29000"))  # FCFA par personne et par mois

# Point médian des tranches de revenu mensuel du ménage (questionnaires v1 et v2).
INCOME_MIDPOINT = {"none": 0, "lt25k": 15_000, "25to60k": 42_500, "60to120k": 90_000, "gt120k": 150_000,
                   "gt60k": 90_000}


def _val(a: dict, key: str):
    v = a.get(key)
    if v in (None, "") or (isinstance(v, str) and v.startswith("__")):
        return None
    return v


def _num(a: dict, key: str):
    v = _val(a, key)
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _is(a, key, deprived: set, ok: set | None = None):
    """Privé si la réponse est dans `deprived` ; non privé pour toute autre réponse connue."""
    v = _val(a, key)
    if v is None:
        return None
    if ok is not None and v not in deprived and v not in ok:
        return None
    return v in deprived


def income_per_person(a: dict) -> float | None:
    band = _val(a, "monthly_income")
    if band not in INCOME_MIDPOINT:
        return None
    size = _num(a, "household_size") or 1
    return INCOME_MIDPOINT[band] / max(size, 1)


def _income_poverty(a):
    per = income_per_person(a)
    return None if per is None else per < POVERTY_LINE


def _no_activity(a):
    act = _val(a, "activity_status")
    if act is None:
        return None
    earners = _num(a, "earners")
    return act == "none" or earners == 0


def _food(a):
    meals = _val(a, "meals_per_day")
    days = _num(a, "days_without_food")
    if meals is None and days is None:
        return None
    return meals in {"none", "one"} or (days is not None and days >= 3)


def _civil(a):
    adult = _val(a, "birth_certificate")
    child = _val(a, "children_without_birth_certificate")
    if adult is None and child is None:
        return None
    return adult == "no" or child == "yes"


@dataclass(frozen=True)
class Indicator:
    code: str
    label: str
    test: object
    response: str = ""  # Ce que la plateforme propose quand le ménage est privé


DIMENSIONS = [
    ("income", "Revenus et emploi", [
        Indicator("income_poverty", "Revenu par personne sous le seuil", _income_poverty,
                  "Transfert monétaire, appui à une activité (AGR)"),
        Indicator("no_activity", "Aucune personne avec une activité", _no_activity,
                  "Orientation emploi / formation, appui AGR"),
    ]),
    ("food", "Alimentation", [
        Indicator("food_insecurity", "Moins de 2 repas par jour ou 3 jours sans assez à manger", _food,
                  "Vivres, transfert monétaire"),
        Indicator("negative_coping", "Vente de biens, mendicité ou travail des enfants pour manger",
                  lambda a: _is(a, "coping_strategy", {"sold_assets", "begging"}), "Aide d'urgence, protection"),
    ]),
    ("living", "Logement et services de base", [
        Indicator("housing", "Sans abri ou logement précaire",
                  lambda a: _is(a, "housing_type", {"street", "precarious"}), "Abri, aide au relogement"),
        Indicator("water", "Pas d'accès à l'eau potable", lambda a: _is(a, "water_access", {"none"}),
                  "Orientation services techniques / mairie"),
        Indicator("sanitation", "Pas de toilettes", lambda a: _is(a, "sanitation", {"none"}),
                  "Orientation services techniques / mairie"),
        Indicator("electricity", "Pas d'électricité", lambda a: _is(a, "electricity", {"none"}),
                  "Orientation branchement social"),
    ]),
    ("education", "Éducation", [
        Indicator("schooling", "Enfant en âge scolaire non scolarisé",
                  lambda a: _is(a, "children_out_of_school", {"yes"}), "Réinscription scolaire, kit scolaire"),
        Indicator("literacy", "Adulte ne sachant ni lire ni écrire", lambda a: _is(a, "literacy", {"no"}),
                  "Alphabétisation, formation"),
    ]),
    ("health", "Santé et état civil", [
        Indicator("health_coverage", "Sans couverture maladie (CMU)", lambda a: _is(a, "health_coverage", {"no"}),
                  "Inscription CMU"),
        Indicator("health_access", "Pas d'accès aux soins", lambda a: _is(a, "health_access", {"no"}),
                  "Orientation centre de santé"),
        Indicator("malnutrition", "Enfant malnutri ou très amaigri", lambda a: _is(a, "child_malnutrition", {"yes"}),
                  "Prise en charge nutritionnelle (urgent)"),
        Indicator("civil_status", "Sans acte de naissance (adulte ou enfant)", _civil,
                  "Acte de naissance, jugement supplétif"),
    ]),
]

WEIGHTS = {}
INDICATORS = {}
DIMENSION_OF = {}
for _dim, _label, _inds in DIMENSIONS:
    for _ind in _inds:
        WEIGHTS[_ind.code] = 1 / len(DIMENSIONS) / len(_inds)
        INDICATORS[_ind.code] = _ind
        DIMENSION_OF[_ind.code] = _dim


@dataclass
class Assessment:
    deprivations: dict = field(default_factory=dict)  # code -> True / False / None
    score: float = 0.0
    answered_weight: float = 0.0

    @property
    def sufficient(self) -> bool:
        return self.answered_weight >= MIN_ANSWERED_WEIGHT - 1e-9

    @property
    def poor(self) -> bool:
        return self.sufficient and self.score >= POVERTY_CUTOFF - 1e-9

    @property
    def severe(self) -> bool:
        return self.sufficient and self.score >= SEVERE_CUTOFF - 1e-9

    def deprived(self, code: str) -> bool:
        return self.deprivations.get(code) is True


def assess(answers: dict) -> Assessment:
    out = Assessment()
    total = 0.0
    for code, ind in INDICATORS.items():
        try:
            d = ind.test(answers)
        except Exception:  # réponse mal formée : considérée comme inconnue
            d = None
        out.deprivations[code] = d
        if d is not None:
            out.answered_weight += WEIGHTS[code]
            if d:
                total += WEIGHTS[code]
    out.score = total / out.answered_weight if out.answered_weight else 0.0
    return out


def methodology() -> dict:
    return {
        "poverty_cutoff": round(POVERTY_CUTOFF, 3),
        "severe_cutoff": SEVERE_CUTOFF,
        "min_answered_weight": MIN_ANSWERED_WEIGHT,
        "poverty_line_monthly": POVERTY_LINE,
        "dimensions": [{
            "code": dim, "label": label, "weight": round(1 / len(DIMENSIONS), 3),
            "indicators": [{"code": i.code, "label": i.label, "weight": round(WEIGHTS[i.code], 3),
                            "response": i.response} for i in inds],
        } for dim, label, inds in DIMENSIONS],
    }
