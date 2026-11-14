"""
Lutte contre la pauvreté : indice multidimensionnel, besoins non couverts, trajectoires des ménages,
et cadre de suivi (indicateurs, valeurs de référence, cibles).

Tous les chiffres sont calculés en direct à partir des données de l'organisation. Les effectifs inférieurs à
MIN_CELL sont masqués dans les ventilations (territoires, groupes) pour empêcher la ré-identification.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.db.models import F
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import audit
from apps.core.permissions import IsStaffMember

from .index import DIMENSION_OF, DIMENSIONS, INDICATORS, WEIGHTS, Assessment, assess, methodology
from .models import IndicatorTarget

MIN_CELL = 3
AID_WINDOW_DAYS = 365
FOOD_AID_WINDOW_DAYS = 180
VISIT_WINDOW_DAYS = 180
STALE_DAYS = 730  # revisite au moins tous les 24 mois

# Réponses considérées comme une prise en charge d'une privation : (catégories d'aide, services d'orientation).
RESPONSES = {
    "income_poverty": ({"cash", "livelihood", "training"}, {"employment", "financial"}),
    "no_activity": ({"livelihood", "training"}, {"employment"}),
    "food_insecurity": ({"food", "nutrition", "cash"}, set()),
    "negative_coping": ({"food", "cash", "psychosocial"}, {"child_protection", "social_center"}),
    "housing": ({"shelter", "relocation", "nfi"}, {"housing"}),
    "schooling": ({"education"}, {"school"}),
    "literacy": ({"training"}, {"employment"}),
    "health_coverage": ({"health_coverage"}, {"cmu"}),
    "health_access": ({"health"}, {"health"}),
    "malnutrition": ({"nutrition", "health"}, {"nutrition", "health"}),
    "civil_status": ({"civil_documents"}, {"civil_registry", "identity"}),
}
DISPLACED = {"evicted", "displaced", "returnee", "refugee"}


def _pct(n, d):
    return round(100 * n / d, 1) if d else None


def _report_orgs(request):
    orgs = request.user.organizations_with("report.aggregate")
    requested = request.query_params.get("organization")
    if requested:
        orgs = {o for o in orgs if str(o) == requested}
    return orgs


@dataclass
class Unit:
    key: str
    household_id: str | None = None
    code: str = ""
    territory: str | None = None
    head: dict | None = None
    persons: set = field(default_factory=set)
    history: list = field(default_factory=list)  # [(date, answers, Assessment)] trié par date
    aid_categories: set = field(default_factory=set)
    food_aid_recent: bool = False
    last_aid: date | None = None
    referral_services: set = field(default_factory=set)
    last_visit: date | None = None

    @property
    def latest(self):
        ok = [h for h in self.history if h[2].sufficient]
        return ok[-1] if ok else (self.history[-1] if self.history else None)

    @property
    def assessment(self) -> Assessment | None:
        return self.latest[2] if self.latest else None

    @property
    def answers(self) -> dict:
        return self.latest[1] if self.latest else {}

    def responded(self, code: str) -> bool:
        cats, services = RESPONSES.get(code, (set(), set()))
        if code == "food_insecurity":
            return self.food_aid_recent or bool(services & self.referral_services)
        return bool(cats & self.aid_categories) or bool(services & self.referral_services)

    @property
    def female_head(self) -> bool:
        return self.answers.get("female_head") == "yes" or bool(self.head and self.head.get("sex") == "F")

    @property
    def displaced(self) -> bool:
        return self.answers.get("displacement_status") in DISPLACED


def build_units(orgs) -> dict[str, Unit]:
    from apps.assistance.models import Assistance
    from apps.beneficiaries.models import Household, HouseholdMembership, Person
    from apps.followup.models import Referral, Visit
    from apps.surveys.models import SurveyResponse

    units: dict[str, Unit] = {}
    merged = defaultdict(lambda: defaultdict(dict))
    rows = (SurveyResponse.objects.filter(organization__in=orgs, person__merged_into__isnull=True)
            .values_list("household_id", "person_id", "answers", "collected_at").order_by("collected_at"))
    for hh, pid, answers, at in rows:
        key = str(hh) if hh else f"p:{pid}"
        u = units.setdefault(key, Unit(key=key, household_id=str(hh) if hh else None))
        u.persons.add(pid)
        day = timezone.localtime(at).date() if timezone.is_aware(at) else at.date()
        bucket = merged[key][day]
        for k, v in (answers or {}).items():
            cur = bucket.get(k)
            if cur in (None, "") or (isinstance(cur, str) and cur.startswith("__")):
                bucket[k] = v
    for key, days in merged.items():
        units[key].history = [(d, a, assess(a)) for d, a in sorted(days.items())]

    hh_ids = [u.household_id for u in units.values() if u.household_id]
    for hid, code, tname in Household.objects.filter(id__in=hh_ids).values_list("id", "public_code",
                                                                                 "territory__name"):
        u = units[str(hid)]
        u.code, u.territory = code, tname
    person_unit = {}
    for hid, pid, rel, sex, first, last, pcode in (
            HouseholdMembership.objects.filter(household_id__in=hh_ids, end_date__isnull=True)
            .values_list("household_id", "person_id", "relation", "person__sex", "person__first_name",
                         "person__last_name", "person__public_code")):
        u = units[str(hid)]
        u.persons.add(pid)
        if rel == "head":
            u.head = {"id": str(pid), "name": f"{first} {last}".strip(), "code": pcode, "sex": sex}
    for u in units.values():
        for pid in u.persons:
            person_unit.setdefault(pid, u)
    solo = [u for u in units.values() if not u.household_id]
    if solo:
        info = {p["id"]: p for p in Person.objects.filter(id__in=[next(iter(u.persons)) for u in solo]).values(
            "id", "first_name", "last_name", "public_code", "sex", "territory__name")}
        for u in solo:
            p = info.get(next(iter(u.persons)))
            if p:
                u.code, u.territory = p["public_code"], p["territory__name"]
                u.head = {"id": str(p["id"]), "name": f"{p['first_name']} {p['last_name']}".strip(),
                          "code": p["public_code"], "sex": p["sex"]}

    now = timezone.now()
    for pid, hid, cat, at in (Assistance.objects.filter(organization__in=orgs, status="delivered",
                                                        delivered_at__gte=now - timedelta(days=AID_WINDOW_DAYS))
                              .values_list("person_id", "household_id", "category", "delivered_at")):
        u = units.get(str(hid)) if hid else None
        u = u or person_unit.get(pid)
        if not u:
            continue
        u.aid_categories.add(cat)
        d = timezone.localtime(at).date()
        u.last_aid = max(u.last_aid or d, d)
        if cat in {"food", "nutrition", "cash"} and at >= now - timedelta(days=FOOD_AID_WINDOW_DAYS):
            u.food_aid_recent = True
    for pid, service in (Referral.objects.filter(organization__in=orgs,
                                                 created_at__gte=now - timedelta(days=AID_WINDOW_DAYS))
                         .exclude(status="cancelled").values_list("person_id", "service")):
        if pid in person_unit:
            person_unit[pid].referral_services.add(service)
    for pid, day in Visit.objects.filter(organization__in=orgs).values_list("person_id", "visited_at"):
        u = person_unit.get(pid)
        if u:
            u.last_visit = max(u.last_visit or day, day)
    return units


def _rates(units):
    assessed = [u for u in units if u.assessment and u.assessment.sufficient]
    poor = [u for u in assessed if u.assessment.poor]
    return {
        "assessed": len(assessed),
        "poor": len(poor),
        "rate": _pct(len(poor), len(assessed)),
        "intensity": _pct(sum(u.assessment.score for u in poor), len(poor)),
        "mpi": round(len(poor) / len(assessed) * (sum(u.assessment.score for u in poor) / len(poor)), 3)
        if poor else (0.0 if assessed else None),
    }


def _masked(row):
    if row["assessed"] < MIN_CELL:
        return {**row, "poor": None, "rate": None, "intensity": None, "mpi": None, "suppressed": True}
    return {**row, "suppressed": False}


def _quarter_ends(today: date, n: int = 6):
    out = []
    y, q = today.year, (today.month - 1) // 3 + 1
    for _ in range(n):
        end = date(y, 3 * q, 1)
        end = (end.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        out.append((f"T{q} {y}", min(end, today)))
        q -= 1
        if q == 0:
            y, q = y - 1, 4
    return list(reversed(out))


def poverty_stats(orgs, *, with_priority=False):
    today = timezone.localdate()
    all_units = list(build_units(orgs).values())
    assessed = [u for u in all_units if u.assessment and u.assessment.sufficient]
    poor = [u for u in assessed if u.assessment.poor]
    severe = [u for u in assessed if u.assessment.severe]
    main = _rates(all_units)

    # Contribution de chaque indicateur à l'IPM (poids renormalisés par ménage).
    contrib = Counter()
    for u in poor:
        a = u.assessment
        for code in INDICATORS:
            if a.deprived(code):
                contrib[code] += WEIGHTS[code] / a.answered_weight
    total_score = sum(u.assessment.score for u in poor) or 1
    indicators = []
    for code, ind in INDICATORS.items():
        answered = [u for u in assessed if u.assessment.deprivations.get(code) is not None]
        deprived = [u for u in answered if u.assessment.deprived(code)]
        responsive = code in RESPONSES
        handled = [u for u in deprived if u.responded(code)] if responsive else []
        indicators.append({
            "code": code, "label": ind.label, "dimension": DIMENSION_OF[code], "response": ind.response,
            "answered": len(answered), "deprived": len(deprived), "rate": _pct(len(deprived), len(answered)),
            "censored_rate": _pct(sum(1 for u in deprived if u.assessment.poor), len(assessed)),
            "contribution": _pct(contrib[code], total_score),
            "responsive": responsive,
            "handled": len(handled) if responsive else None,
            "unhandled": len(deprived) - len(handled) if responsive else None,
            "response_rate": _pct(len(handled), len(deprived)) if responsive else None,
        })
    dimensions = [{"code": d, "label": label,
                   "contribution": round(sum(i["contribution"] or 0 for i in indicators if i["dimension"] == d), 1)}
                  for d, label, _ in DIMENSIONS]

    by_territory = defaultdict(list)
    for u in all_units:
        by_territory[u.territory or "Non localisé"].append(u)
    territories = []
    for name, us in by_territory.items():
        row = _rates(us)
        top = Counter(c for u in us if u.assessment and u.assessment.poor for c in INDICATORS
                      if u.assessment.deprived(c)).most_common(1)
        row = _masked({"name": name, **row})
        row["main_deprivation"] = INDICATORS[top[0][0]].label if top and not row["suppressed"] else None
        territories.append(row)
    territories.sort(key=lambda r: (r["suppressed"], -(r["rate"] or 0), -r["assessed"]))

    def group(label, pred):
        yes = [u for u in all_units if pred(u)]
        no = [u for u in all_units if not pred(u)]
        return {"label": label, "yes": _masked(_rates(yes)), "no": _masked(_rates(no))}

    groups = [
        group("Ménage dirigé par une femme", lambda u: u.female_head),
        group("Ménage déguerpi, déplacé ou réfugié", lambda u: u.displaced),
        group("Ménage avec un enfant de moins de 5 ans",
              lambda u: (u.answers.get("children_under5") or 0) not in (0, "0", "") and
              not str(u.answers.get("children_under5", "")).startswith("__")),
    ]

    followed = [u for u in all_units if sum(1 for h in u.history if h[2].sufficient) >= 2]
    moves = Counter()
    change = []
    for u in followed:
        ok = [h for h in u.history if h[2].sufficient]
        first, last = ok[0][2], ok[-1][2]
        moves[("exited" if first.poor and not last.poor else "entered" if last.poor and not first.poor
               else "stayed_poor" if first.poor else "stayed_non_poor")] += 1
        change.append(last.score - first.score)
    trajectories = {
        "followed": len(followed), **{k: moves.get(k, 0) for k in
                                      ("exited", "entered", "stayed_poor", "stayed_non_poor")},
        "exit_rate": _pct(moves["exited"], moves["exited"] + moves["stayed_poor"]),
        "avg_score_change": round(100 * sum(change) / len(change), 1) if change else None,
    }

    series = []
    for label, end in _quarter_ends(today):
        snap = []
        for u in all_units:
            ok = [h for h in u.history if h[0] <= end and h[2].sufficient]
            if ok:
                snap.append(ok[-1][2])
        p = [a for a in snap if a.poor]
        series.append({"period": label, "end": end, "assessed": len(snap), "rate": _pct(len(p), len(snap)),
                       "mpi": round(len(p) / len(snap) * sum(a.score for a in p) / len(p), 3) if p else
                       (0.0 if snap else None)})

    def untreated(code):
        return [u for u in assessed if u.assessment.deprived(code) and not u.responded(code)]

    stale_cut = today - timedelta(days=STALE_DAYS)
    visit_cut = today - timedelta(days=VISIT_WINDOW_DAYS)
    poor_no_aid = [u for u in poor if not u.aid_categories]
    gaps = [
        {"code": "malnutrition", "severity": "critical", "count": len(untreated("malnutrition")),
         "label": "Enfants malnutris sans prise en charge",
         "action": "Orienter sans attendre vers un centre de santé ou de nutrition.", "href": "/orientations"},
        {"code": "hunger", "severity": "high", "count": len(untreated("food_insecurity")),
         "label": "Ménages qui ont faim, sans aide alimentaire depuis 6 mois",
         "action": "Programmer des vivres ou un transfert monétaire.", "href": "/aides"},
        {"code": "poor_without_aid", "severity": "high", "count": len(poor_no_aid),
         "label": "Ménages pauvres sans aucune aide depuis 12 mois",
         "action": "Inscrire à un programme ou planifier une aide.", "href": "/pauvrete#prioritaires"},
        {"code": "schooling", "severity": "medium", "count": len(untreated("schooling")),
         "label": "Enfants non scolarisés sans démarche engagée",
         "action": "Orienter vers l'école (réinscription, kit scolaire).", "href": "/orientations"},
        {"code": "health_coverage", "severity": "medium", "count": len(untreated("health_coverage")),
         "label": "Ménages sans CMU ni inscription en cours",
         "action": "Faire l'inscription CMU.", "href": "/orientations"},
        {"code": "civil_status", "severity": "medium", "count": len(untreated("civil_status")),
         "label": "Personnes sans acte de naissance, sans démarche engagée",
         "action": "Orienter vers l'état civil (jugement supplétif).", "href": "/orientations"},
        {"code": "not_visited", "severity": "medium",
         "count": sum(1 for u in poor if not u.last_visit or u.last_visit < visit_cut),
         "label": "Ménages pauvres non visités depuis 6 mois",
         "action": "Planifier une visite de suivi.", "href": "/visites"},
        {"code": "stale", "severity": "low",
         "count": sum(1 for u in all_units if u.latest and u.latest[0] < stale_cut),
         "label": "Ménages évalués il y a plus de 24 mois",
         "action": "Refaire le questionnaire pour mettre la situation à jour.", "href": "/visites"},
    ]

    aided = [u for u in assessed if u.aid_categories]
    responsive_codes = list(RESPONSES)
    deprived_total = sum(1 for u in assessed for c in responsive_codes if u.assessment.deprived(c))
    handled_total = sum(1 for u in assessed for c in responsive_codes if u.assessment.deprived(c) and u.responded(c))
    monetary = next(i for i in indicators if i["code"] == "income_poverty")
    food = next(i for i in indicators if i["code"] == "food_insecurity")
    out = {
        "generated_at": timezone.now(),
        "summary": {
            "units": len(all_units),
            "assessed": len(assessed),
            "incomplete": len(all_units) - len(assessed),
            "poor": len(poor),
            "severe": len(severe),
            "poverty_rate": main["rate"],
            "severe_rate": _pct(len(severe), len(assessed)),
            "intensity": main["intensity"],
            "mpi": main["mpi"],
            "monetary_poverty_rate": monetary["rate"],
            "food_insecurity_rate": food["rate"],
            "poor_covered": len(poor) - len(poor_no_aid),
            "poor_coverage_rate": _pct(len(poor) - len(poor_no_aid), len(poor)),
            "aided_assessed": len(aided),
            "targeting_rate": _pct(sum(1 for u in aided if u.assessment.poor), len(aided)),
            "deprivations_found": deprived_total,
            "deprivations_handled": handled_total,
            "response_rate": _pct(handled_total, deprived_total),
            "fresh_rate": _pct(sum(1 for u in all_units if u.latest and u.latest[0] >= stale_cut), len(all_units)),
        },
        "dimensions": dimensions,
        "indicators": indicators,
        "territories": territories,
        "groups": groups,
        "trajectories": trajectories,
        "series": series,
        "gaps": gaps,
        "methodology": methodology(),
        "min_cell": MIN_CELL,
    }
    if with_priority:
        ranked = sorted(poor, key=lambda u: (not u.assessment.deprived("malnutrition"), bool(u.aid_categories),
                                             -u.assessment.score))
        out["priority"] = [{
            "household": u.household_id, "code": u.code, "territory": u.territory, "head": u.head,
            "score": round(100 * u.assessment.score), "severe": u.assessment.severe,
            "assessed_on": u.latest[0],
            "deprivations": [INDICATORS[c].label for c in INDICATORS if u.assessment.deprived(c)],
            "unhandled": [INDICATORS[c].label for c in RESPONSES
                          if u.assessment.deprived(c) and not u.responded(c)],
            "last_aid": u.last_aid, "last_visit": u.last_visit,
        } for u in ranked[:30]]
    return out


class PovertyReportView(APIView):
    """Tableau « Lutte contre la pauvreté »."""

    permission_classes = [IsStaffMember]

    def get(self, request):
        orgs = _report_orgs(request)
        can_see_people = bool(orgs) and orgs <= request.user.organizations_with("person.view")
        return Response(poverty_stats(orgs, with_priority=can_see_people))


# --- Cadre de suivi --------------------------------------------------------------------------------------------

LEVELS = {"impact": "Impact : la pauvreté recule-t-elle ?",
          "outcome": "Effets : les conditions de vie s'améliorent-elles ?",
          "output": "Mise en œuvre : l'aide atteint-elle les bonnes personnes, à temps ?",
          "accountability": "Redevabilité : les personnes sont-elles écoutées ?"}


def _ind(code, level, label, definition, unit, direction, frequency, source, value, num=None, den=None,
         disaggregation="Territoire, sexe du chef de ménage, situation de déplacement"):
    return {"code": code, "level": level, "label": label, "definition": definition, "unit": unit,
            "direction": direction, "frequency": frequency, "source": source, "value": value,
            "numerator": num, "denominator": den, "disaggregation": disaggregation}


def indicator_values(orgs):
    from apps.assistance.models import Assistance
    from apps.followup.models import Grievance, Referral, Visit

    p = poverty_stats(orgs)
    s, t = p["summary"], p["trajectories"]
    by_code = {i["code"]: i for i in p["indicators"]}

    def complement(code):
        i = by_code[code]
        return (round(100 - i["rate"], 1) if i["rate"] is not None else None,
                i["answered"] - i["deprived"], i["answered"])

    now = timezone.now()
    year_ago = now - timedelta(days=365)
    visits = Visit.objects.filter(organization__in=orgs, visited_at__gte=year_ago.date())
    improved = visits.filter(trend="improved").count()
    refs = (Referral.objects.filter(organization__in=orgs).exclude(status__in=Referral.OPEN)
            .exclude(status="cancelled"))
    completed = refs.filter(status="completed").count()
    delays = [(d - c).days for c, d in Assistance.objects.filter(
        organization__in=orgs, status="delivered", delivered_at__gte=year_ago).values_list("created_at",
                                                                                         "delivered_at")]
    done = Grievance.objects.filter(organization__in=orgs, resolved_at__isnull=False)
    on_time = done.filter(resolved_at__date__lte=F("due_date")).count()
    rated = Grievance.objects.filter(organization__in=orgs).exclude(satisfaction="")
    happy = rated.filter(satisfaction__in=["satisfied", "partly"]).count()
    cov, cov_num, cov_den = complement("health_coverage")
    act, act_num, act_den = complement("no_activity")

    survey = "Questionnaire de recensement et revisites"
    return [
        _ind("poverty_rate", "impact", "Taux de pauvreté multidimensionnelle",
             "Part des ménages évalués dont le score de privation atteint 1/3 (5 dimensions).", "%", "down",
             "Trimestrielle", survey, s["poverty_rate"], s["poor"], s["assessed"]),
        _ind("severe_rate", "impact", "Taux de pauvreté sévère",
             "Part des ménages évalués dont le score de privation atteint 1/2.", "%", "down", "Trimestrielle",
             survey, s["severe_rate"], s["severe"], s["assessed"]),
        _ind("mpi", "impact", "Indice de pauvreté multidimensionnelle (IPM)",
             "Taux de pauvreté × intensité moyenne des privations des ménages pauvres (de 0 à 1).", "indice",
             "down", "Trimestrielle", survey, s["mpi"]),
        _ind("monetary_poverty_rate", "impact", "Ménages sous le seuil de revenu",
             f"Revenu mensuel estimé par personne inférieur à {p['methodology']['poverty_line_monthly']:,} FCFA."
             .replace(",", " "), "%", "down", "Trimestrielle", survey, s["monetary_poverty_rate"]),
        _ind("food_insecurity_rate", "impact", "Ménages en insécurité alimentaire",
             "Moins de 2 repas par jour ou au moins 3 jours sans assez à manger sur 7 jours.", "%", "down",
             "Trimestrielle", survey, s["food_insecurity_rate"]),
        _ind("exit_rate", "impact", "Taux de sortie de la pauvreté",
             "Parmi les ménages pauvres à leur première évaluation et réévalués depuis, part qui ne sont plus "
             "pauvres.", "%", "up", "Semestrielle", "Comparaison première / dernière évaluation", t["exit_rate"],
             t["exited"], t["exited"] + t["stayed_poor"]),
        _ind("schooling_gap", "outcome", "Ménages avec un enfant non scolarisé",
             "Ménages déclarant au moins un enfant en âge scolaire qui ne va pas à l'école.", "%", "down",
             "Annuelle (rentrée)", survey, by_code["schooling"]["rate"], by_code["schooling"]["deprived"],
             by_code["schooling"]["answered"]),
        _ind("health_coverage_rate", "outcome", "Ménages couverts par la CMU",
             "Ménages déclarant une couverture maladie (CMU ou équivalent).", "%", "up", "Semestrielle", survey,
             cov, cov_num, cov_den),
        _ind("civil_status_gap", "outcome", "Ménages avec une personne sans acte de naissance",
             "Adulte ou enfant du ménage sans acte de naissance ni jugement supplétif.", "%", "down",
             "Semestrielle", survey, by_code["civil_status"]["rate"], by_code["civil_status"]["deprived"],
             by_code["civil_status"]["answered"]),
        _ind("activity_rate", "outcome", "Ménages ayant au moins une activité",
             "Ménages où au moins une personne a une activité ou un revenu.", "%", "up", "Semestrielle", survey,
             act, act_num, act_den),
        _ind("improving_visits", "outcome", "Visites constatant une amélioration",
             "Part des visites de suivi des 12 derniers mois notées « en amélioration ».", "%", "up", "Mensuelle",
             "Comptes rendus de visite", _pct(improved, visits.count()), improved, visits.count(),
             "Territoire, agent"),
        _ind("poor_coverage_rate", "output", "Ménages pauvres ayant reçu une aide",
             "Ménages pauvres ayant reçu au moins une aide remise (avec preuve) au cours des 12 derniers mois.",
             "%", "up", "Mensuelle", "Aides apportées + indice", s["poor_coverage_rate"], s["poor_covered"],
             s["poor"]),
        _ind("targeting_rate", "output", "Part des ménages aidés qui sont pauvres",
             "Parmi les ménages évalués ayant reçu une aide en 12 mois, part de ménages pauvres (qualité du "
             "ciblage).", "%", "up", "Trimestrielle", "Aides apportées + indice", s["targeting_rate"],
             None, s["aided_assessed"]),
        _ind("response_rate", "output", "Besoins constatés pris en charge",
             "Privations constatées (faim, école, CMU, état civil, soins, logement, revenu) ayant reçu une aide "
             "ou une orientation adaptée en 12 mois.", "%", "up", "Mensuelle", "Indice + aides + orientations",
             s["response_rate"], s["deprivations_handled"], s["deprivations_found"]),
        _ind("referral_completion", "output", "Orientations abouties",
             "Orientations closes avec « service rendu », hors annulations.", "%", "up", "Mensuelle",
             "Orientations", _pct(completed, refs.count()), completed, refs.count(), "Service, territoire"),
        _ind("aid_delay", "output", "Délai moyen de remise d'une aide",
             "Jours entre l'enregistrement d'une aide et sa remise, sur 12 mois.", "jours", "down", "Mensuelle",
             "Aides apportées", round(sum(delays) / len(delays), 1) if delays else None, None, len(delays),
             "Catégorie d'aide, partenaire"),
        _ind("fresh_rate", "output", "Ménages à jour (évalués depuis moins de 24 mois)",
             "Part des ménages dont la dernière évaluation date de moins de 24 mois.", "%", "up", "Trimestrielle",
             survey, s["fresh_rate"], None, s["units"], "Territoire"),
        _ind("grievance_on_time", "accountability", "Plaintes traitées dans les délais",
             "Plaintes clôturées avant leur date limite (3, 10 ou 30 jours selon la gravité).", "%", "up",
             "Mensuelle", "Plaintes & réclamations", _pct(on_time, done.count()), on_time, done.count(),
             "Catégorie, canal, territoire"),
        _ind("grievance_satisfaction", "accountability", "Personnes satisfaites de la réponse",
             "Avis « oui » ou « en partie » donnés par les personnes sur la réponse à leur plainte.", "%", "up",
             "Trimestrielle", "Avis dans l'app Mon Parcours", _pct(happy, rated.count()), happy, rated.count(),
             "Catégorie de plainte"),
    ]


def _status(ind, target):
    v = ind["value"]
    if target is None:
        return "no_target"
    if v is None:
        return "no_data"
    goal = float(target.target)
    up = ind["direction"] == "up"
    if (up and v >= goal) or (not up and v <= goal):
        return "achieved"
    if target.baseline is not None:
        base = float(target.baseline)
        if (up and v < base) or (not up and v > base):
            return "off_track"
    return "in_progress"


def _progress(ind, target):
    if target is None or ind["value"] is None or target.baseline is None:
        return None
    base, goal = float(target.baseline), float(target.target)
    if goal == base:
        return None
    return max(0, min(100, round(100 * (ind["value"] - base) / (goal - base))))


class IndicatorFrameworkView(APIView):
    """Cadre de suivi : indicateurs, valeur actuelle, référence et cible."""

    permission_classes = [IsStaffMember]

    def get(self, request):
        orgs = _report_orgs(request)
        rows = indicator_values(orgs)
        targets = {}
        org = next(iter(orgs)) if len(orgs) == 1 else None
        if org:
            targets = {t.code: t for t in IndicatorTarget.objects.filter(organization_id=org)}
        editable = bool(org) and org in (request.user.organizations_with("program.manage")
                                         | request.user.organizations_with("organization.manage_members"))
        for r in rows:
            t = targets.get(r["code"])
            r["target"] = {"baseline": t.baseline, "target": t.target, "deadline": t.deadline, "note": t.note} \
                if t else None
            r["status"] = _status(r, t)
            r["progress"] = _progress(r, t)
        return Response({"levels": LEVELS, "indicators": rows, "can_edit_targets": editable,
                         "generated_at": timezone.now()})


class TargetSerializer(serializers.Serializer):
    organization = serializers.UUIDField()
    code = serializers.CharField(max_length=40)
    baseline = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    target = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    deadline = serializers.DateField(required=False, allow_null=True)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True)


INDICATOR_CODES = {"poverty_rate", "severe_rate", "mpi", "monetary_poverty_rate", "food_insecurity_rate",
                   "exit_rate", "schooling_gap", "health_coverage_rate", "civil_status_gap", "activity_rate",
                   "improving_visits", "poor_coverage_rate", "targeting_rate", "response_rate",
                   "referral_completion", "aid_delay", "fresh_rate", "grievance_on_time", "grievance_satisfaction"}


class IndicatorTargetView(APIView):
    """Fixe (ou retire, si `target` est vide) la valeur de référence et la cible d'un indicateur."""

    permission_classes = [IsStaffMember]

    def post(self, request):
        ser = TargetSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        org = d["organization"]
        allowed = (request.user.organizations_with("program.manage")
                   | request.user.organizations_with("organization.manage_members"))
        if org not in allowed:
            raise PermissionDenied("Seuls les responsables de programme et administrateurs fixent les cibles.")
        if d["code"] not in INDICATOR_CODES:
            raise serializers.ValidationError({"code": "Indicateur inconnu."})
        if d.get("target") is None:
            IndicatorTarget.objects.filter(organization_id=org, code=d["code"]).delete()
            from apps.organizations.models import Organization
            audit("indicator.target_removed", organization=Organization.objects.get(pk=org),
                  data={"code": d["code"]})
            return Response({"code": d["code"], "target": None})
        t, _ = IndicatorTarget.objects.update_or_create(
            organization_id=org, code=d["code"],
            defaults={"baseline": d.get("baseline"), "target": d["target"], "deadline": d.get("deadline"),
                      "note": d.get("note", ""), "updated_by": request.user})
        audit("indicator.target_set", obj=t, data={"code": t.code, "baseline": str(t.baseline),
                                                   "target": str(t.target)})
        return Response({"code": t.code, "baseline": t.baseline, "target": t.target, "deadline": t.deadline,
                         "note": t.note})
