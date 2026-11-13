"""
Moteur de scoring de vulnérabilité de référence (cahier §5, SRC-03).

Règles non négociables :
- 7 dimensions, total 100 points ; seuils historiques <40 / 40–59 / 60–74 / ≥75.
- Le score est CONSULTATIF : il ne déclenche ni refus ni versement (IA-03).
- Une question non répondue n'est jamais comptée comme 0 favorable : elle est listée dans `missing`
  et réduit la complétude (IA-02).
- Calcul pur et déterministe : même dossier + même version = même résultat (IA-01).
- Le contexte territorial n'ajoute aucun bonus au score individuel (§14, URG-03).
"""
from dataclasses import dataclass, field

from apps.surveys.definitions import SPECIAL_VALUES

SCORING_V1 = {
    "code": "VULN",
    "version": 1,
    "min_completeness": 0.7,
    "bands": [
        {"min": 75, "code": "very_high", "label": "Très élevée"},
        {"min": 60, "code": "high", "label": "Élevée"},
        {"min": 40, "code": "medium", "label": "Moyenne"},
        {"min": 0, "code": "low", "label": "Faible"},
    ],
    "dimensions": [
        {"code": "A", "label": "Conditions de vie", "cap": 20, "items": [
            {"question": "housing_type", "points": {"precarious": 8, "shared": 4, "rented": 3, "owned": 0}},
            {"question": "water_access", "points": {"none": 6, "shared": 3, "private": 0}},
            {"question": "electricity", "points": {"none": 6, "informal": 3, "yes": 0}},
        ]},
        {"code": "B", "label": "Santé et handicap", "cap": 20, "items": [
            {"question": "chronic_illness", "points": {"yes": 6, "no": 0}},
            {"question": "disability", "points": {"severe": 8, "moderate": 5, "none": 0}},
            {"question": "health_access", "points": {"no": 6, "difficult": 3, "easy": 0}},
        ]},
        {"code": "C", "label": "Revenus et emploi", "cap": 20, "items": [
            {"question": "monthly_income", "points": {"none": 8, "lt25k": 6, "25to60k": 3, "gt60k": 0}},
            {"question": "activity_status", "points": {"none": 6, "informal": 3, "stable": 0}},
            {"question": "dependents", "ranges": [
                {"min": 5, "points": 6}, {"min": 3, "points": 4}, {"min": 1, "points": 2}, {"min": 0, "points": 0}]},
        ]},
        {"code": "D", "label": "Éducation", "cap": 15, "items": [
            {"question": "literacy", "points": {"no": 8, "partial": 4, "yes": 0}},
            {"question": "children_out_of_school", "points": {"yes": 7, "no": 0}},
        ]},
        {"code": "E", "label": "Protection et juridique", "cap": 10, "items": [
            {"question": "birth_certificate", "points": {"no": 5, "yes": 0}},
            {"question": "protection_risk", "points": {"yes": 5, "no": 0}},
        ]},
        {"code": "F", "label": "Réseau et isolement", "cap": 10, "items": [
            {"question": "support_network", "points": {"none": 6, "weak": 3, "strong": 0}},
            {"question": "lives_alone", "points": {"yes": 4, "no": 0}},
        ]},
        {"code": "G", "label": "Psychosocial et perception", "cap": 5, "items": [
            {"question": "self_assessment", "points": {"very_difficult": 5, "difficult": 3, "manageable": 1,
                                                       "good": 0}},
        ]},
    ],
}


@dataclass
class DimensionResult:
    code: str
    label: str
    cap: int
    score: int
    max_answered: int
    max_possible: int
    contributions: list = field(default_factory=list)
    missing: list = field(default_factory=list)

    @property
    def completeness(self) -> float:
        return round(self.max_answered / self.max_possible, 3) if self.max_possible else 1.0


@dataclass
class ScoreOutcome:
    total: int
    band: dict
    completeness: float
    is_reliable: bool
    dimensions: list
    missing: list

    def as_dict(self):
        return {
            "total": self.total,
            "band": self.band,
            "completeness": self.completeness,
            "is_reliable": self.is_reliable,
            "dimensions": [
                {"code": d.code, "label": d.label, "cap": d.cap, "score": d.score, "completeness": d.completeness,
                 "contributions": d.contributions, "missing": d.missing}
                for d in self.dimensions
            ],
            "missing": self.missing,
        }


def _item_points(item: dict, value):
    if "points" in item:
        return item["points"].get(value)
    if "ranges" in item and isinstance(value, (int, float)) and not isinstance(value, bool):
        for r in item["ranges"]:
            if value >= r["min"]:
                return r["points"]
    return None


def _item_max(item: dict) -> int:
    if "points" in item:
        return max(item["points"].values())
    return max(r["points"] for r in item["ranges"])


def compute_score(answers: dict, config: dict = SCORING_V1) -> ScoreOutcome:
    dimensions, missing = [], []
    for dim in config["dimensions"]:
        result = DimensionResult(code=dim["code"], label=dim["label"], cap=dim["cap"], score=0, max_answered=0,
                                 max_possible=0)
        raw = 0
        for item in dim["items"]:
            qcode = item["question"]
            item_max = _item_max(item)
            result.max_possible += item_max
            value = answers.get(qcode)
            if value is None or value in SPECIAL_VALUES:
                reason = SPECIAL_VALUES.get(value, "Non renseigné") if value is not None else "Non renseigné"
                result.missing.append({"question": qcode, "reason": reason})
                missing.append({"dimension": dim["code"], "question": qcode, "reason": reason})
                continue
            pts = _item_points(item, value)
            if pts is None:
                result.missing.append({"question": qcode, "reason": "Valeur non prise en compte"})
                missing.append({"dimension": dim["code"], "question": qcode, "reason": "Valeur non prise en compte"})
                continue
            raw += pts
            result.max_answered += item_max
            result.contributions.append({"question": qcode, "value": value, "points": pts, "max": item_max})
        result.score = min(raw, dim["cap"])
        dimensions.append(result)

    total = sum(d.score for d in dimensions)
    possible = sum(d.max_possible for d in dimensions)
    answered = sum(d.max_answered for d in dimensions)
    completeness = round(answered / possible, 3) if possible else 1.0
    band = next(b for b in config["bands"] if total >= b["min"])
    return ScoreOutcome(total=total, band={"code": band["code"], "label": band["label"]},
                        completeness=completeness, is_reliable=completeness >= config["min_completeness"],
                        dimensions=dimensions, missing=missing)
