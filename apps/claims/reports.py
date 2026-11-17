"""
Tableau de bord agrégé du dispositif pour le ministère : AUCUN nom, aucune donnée nominative.
Mise à jour à chaque consultation. Toute zone de moins de 10 ménages est fusionnée avec la zone parente
(sous-préfecture → département → région), puis regroupée dans « Autres zones ».

Indicateurs du pilote (diapositives 30 et 31) : 3 indicateurs décident de l'extension, 7 complètent le suivi.
"""
from collections import Counter, defaultdict
from datetime import timedelta
from statistics import median

from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.assistance.models import Assistance
from apps.core.permissions import IsStaffMember, require_org_perm
from apps.followup.models import Grievance
from apps.organizations.models import Organization

from .models import DECISION_DAYS, URGENT_HOURS, Claim, ClaimAppeal, ClaimDecision, ProtectedReport, UrgentMeasure
from .referential import IMPACT_LABELS, SECTORS

MIN_ZONE = 10
PROXIMITY = {Claim.Channel.RELAY, Claim.Channel.PHONE, Claim.Channel.SMS, Claim.Channel.PERMANENCE,
             Claim.Channel.PAPER}
ELIGIBLE = {ClaimDecision.Outcome.SUPPORT, ClaimDecision.Outcome.ORIENTATION,
            ClaimDecision.Outcome.SUPPORT_ORIENTATION}


def _pct(n, d):
    return round(100 * n / d, 1) if d else None


def _median(values):
    values = [v for v in values if v is not None]
    return round(median(values), 1) if values else None


def _zone_for(territory, cache):
    """Remonte jusqu'à la sous-préfecture (ou au niveau le plus fin connu au-dessus du village)."""
    if territory is None:
        return None
    if territory.pk in cache:
        return cache[territory.pk]
    t = territory
    while t is not None and t.level in {"village", "neighborhood", "site"}:
        t = t.parent
    cache[territory.pk] = t or territory
    return cache[territory.pk]


def _merge_small(rows):
    """Fusionne les zones de moins de MIN_ZONE ménages avec leur parent, jusqu'à « Autres zones »."""
    groups = dict(rows)
    for _ in range(4):
        small = [t for t, data in groups.items() if t is not None and len(data) < MIN_ZONE]
        if not small:
            break
        for t in small:
            parent = t.parent if t.parent and t.parent.level not in {"country"} else None
            groups.setdefault(parent, []).extend(groups.pop(t))
    return groups


def _decisions_by_claim(claims):
    current = {}
    for d in ClaimDecision.objects.filter(claim__in=claims, validation=ClaimDecision.Validation.VALIDATED) \
            .order_by("validated_at"):
        current[d.claim_id] = d
    return current


def claim_indicators(org_ids):
    claims = list(Claim.objects.filter(organization_id__in=org_ids).select_related("territory__parent__parent"))
    ids = [c.pk for c in claims]
    decisions = _decisions_by_claim(ids)
    today = timezone.localdate()

    decided = [c for c in claims if c.notified_at]
    delays = [c.decision_days for c in decided]
    in_time = sum(1 for d in delays if d is not None and d <= DECISION_DAYS)
    open_claims = [c for c in claims if c.status != Claim.Status.CLOSED]
    overdue = sum(1 for c in open_claims if c.is_overdue)

    eligible = [c for c in claims if c.pk in decisions and decisions[c.pk].outcome in ELIGIBLE]
    paid = set(Assistance.objects.filter(claim_id__in=ids, status="delivered").values_list("claim_id", flat=True))
    accompanied = [c for c in eligible if c.pk in paid or (
        decisions[c.pk].outcome == ClaimDecision.Outcome.ORIENTATION and c.status in
        {Claim.Status.FOLLOWUP, Claim.Status.CLOSED})]

    pay_delays = []
    for a in Assistance.objects.filter(claim_id__in=ids, status="delivered").select_related("claim"):
        d = decisions.get(a.claim_id)
        if d and d.validated_at and a.delivered_at:
            pay_delays.append((a.delivered_at - d.validated_at).total_seconds() / 86400)

    urgent = UrgentMeasure.objects.filter(claim_id__in=ids)
    urgent_total = urgent.count()
    urgent_in_time = sum(1 for m in urgent if m.executed_in_time)

    grievances = Grievance.objects.filter(organization_id__in=org_ids)
    protected = ProtectedReport.objects.filter(organization_id__in=org_ids)
    appeals = ClaimAppeal.objects.filter(organization_id__in=org_ids)
    complaints_total = grievances.count() + protected.count() + appeals.count()
    complaints_resolved = grievances.filter(status__in=["resolved", "closed"]).count() + \
        protected.filter(status__in=["resolved", "closed"]).count() + appeals.exclude(status="open").count()

    transmitted = [c for c in claims if c.transmitted_to_operator_at]
    operator_on_time = sum(1 for c in transmitted if c.operator_responded_at and
                           timezone.localtime(c.operator_responded_at).date() <= c.operator_response_due)
    reprisals = protected.filter(kind=ProtectedReport.Kind.REPRISAL)
    reprisals_on_time = sum(1 for r in reprisals if r.resolved_at and r.resolved_at <= r.due_at or
                            (r.status == "investigating" and r.updated_at <= r.due_at))
    women = sum(1 for c in claims if c.claimant_sex == "F" or c.women_head)
    proximity = sum(1 for c in claims if c.channel in PROXIMITY)

    key = [
        {"code": "decision_delay", "label": "Délai de traitement", "value": _median(delays), "unit": "jours",
         "target": "≤ 30 jours (médiane) ; 80 % dans les délais", "secondary": _pct(in_time, len(decided)),
         "secondary_label": "dossiers décidés dans les 30 jours",
         "ok": bool(delays) and _median(delays) <= DECISION_DAYS and (_pct(in_time, len(decided)) or 0) >= 80},
        {"code": "accompanied_rate", "label": "Ménages éligibles accompagnés", "value": _pct(len(accompanied),
                                                                                              len(eligible)),
         "unit": "%", "target": "≥ 70 %", "numerator": len(accompanied), "denominator": len(eligible),
         "ok": (_pct(len(accompanied), len(eligible)) or 0) >= 70},
        {"code": "complaints_resolved", "label": "Plaintes résolues", "value": _pct(complaints_resolved,
                                                                                   complaints_total),
         "unit": "%", "target": "≥ 70 %", "numerator": complaints_resolved, "denominator": complaints_total,
         "ok": (_pct(complaints_resolved, complaints_total) or 0) >= 70},
    ]
    contested = appeals.count()
    modified = appeals.filter(decision_modified=True).count()
    complementary = [
        {"code": "proximity_share", "label": "Accès par le canal de proximité", "value": _pct(proximity, len(claims)),
         "unit": "%", "target": "Suivi ; une part faible déclenche une revue"},
        {"code": "women_share", "label": "Demandes de femmes", "value": _pct(women, len(claims)), "unit": "%",
         "target": "Comparée à la population concernée"},
        {"code": "urgent_in_time", "label": "Mesures d'urgence sous 72 h", "value": _pct(urgent_in_time, urgent_total),
         "unit": "%", "target": "≥ 90 %", "numerator": urgent_in_time, "denominator": urgent_total},
        {"code": "payment_delay", "label": "Délai de paiement", "value": _median(pay_delays), "unit": "jours",
         "target": "≤ 10 jours (médiane)"},
        {"code": "contested_modified", "label": "Décisions contestées puis modifiées", "value": modified,
         "unit": "dossiers", "target": "Suivi ; un taux élevé signale un défaut d'examen",
         "secondary": contested, "secondary_label": "contestations reçues"},
        {"code": "operator_on_time", "label": "Réponses de l'exploitant dans le délai",
         "value": _pct(operator_on_time, len(transmitted)), "unit": "%", "target": "≥ 80 %",
         "numerator": operator_on_time, "denominator": len(transmitted)},
        {"code": "reprisals", "label": "Représailles signalées traitées sous 72 h",
         "value": _pct(reprisals_on_time, reprisals.count()), "unit": "%", "target": "Chacune sous 72 h",
         "numerator": reprisals_on_time, "denominator": reprisals.count()},
    ]

    # Ventilations
    outcomes = Counter(decisions[c.pk].outcome for c in claims if c.pk in decisions)
    impacts = Counter(code for c in claims for code in c.impacts)
    zones, cache = defaultdict(list), {}
    for c in claims:
        zones[_zone_for(c.territory, cache)].append(c)
    merged = _merge_small(zones)
    territory_rows = []
    for t, items in sorted(merged.items(), key=lambda kv: -len(kv[1])):
        d = [c.decision_days for c in items if c.decision_days is not None]
        territory_rows.append({
            "name": t.name if t else "Autres zones", "level": t.get_level_display() if t else "",
            "claims": len(items), "open": sum(1 for c in items if c.status != Claim.Status.CLOSED),
            "overdue": sum(1 for c in items if c.status != Claim.Status.CLOSED and c.is_overdue),
            "women_share": _pct(sum(1 for c in items if c.claimant_sex == "F" or c.women_head), len(items)),
            "median_delay": _median(d), "urgent": sum(1 for c in items if c.urgent)})

    months = []
    start = today.replace(day=1)
    for i in range(5, -1, -1):
        m = (start - timedelta(days=31 * i)).replace(day=1)
        nxt = (m + timedelta(days=32)).replace(day=1)
        months.append({
            "month": m.isoformat(),
            "registered": sum(1 for c in claims if m <= c.registered_day < nxt),
            "decided": sum(1 for c in decided if m <= timezone.localtime(c.notified_at).date() < nxt)})

    return {
        "generated_at": timezone.now(),
        "summary": {"claims": len(claims), "open": len(open_claims), "overdue": overdue,
                    "urgent": sum(1 for c in claims if c.urgent), "decided": len(decided),
                    "eligible": len(eligible), "accompanied": len(accompanied),
                    "rejected": outcomes.get(ClaimDecision.Outcome.REJECTED, 0),
                    "absent_from_operator_list": sum(1 for c in claims if c.in_operator_list is False),
                    "rsu_referred": sum(1 for c in claims if c.rsu_status == Claim.RsuStatus.REFERRED),
                    "protected": protected.count(), "appeals": contested, "appeals_open":
                        appeals.filter(status="open").count()},
        "key_indicators": key,
        "complementary_indicators": complementary,
        "by_channel": [{"code": v, "label": label, "count": sum(1 for c in claims if c.channel == v)}
                       for v, label in Claim.Channel.choices],
        "by_sector": [{"code": v, "label": label, "count": sum(1 for c in claims if c.sector == v)}
                      for v, label in SECTORS],
        "by_impact": [{"code": k, "label": IMPACT_LABELS.get(k, k), "count": n} for k, n in impacts.most_common()],
        "by_status": [{"code": v, "label": label, "count": sum(1 for c in claims if c.status == v)}
                      for v, label in Claim.Status.choices],
        "decisions": [{"code": v, "label": label, "count": outcomes.get(v, 0)}
                      for v, label in ClaimDecision.Outcome.choices],
        "protected_by_kind": [{"code": v, "label": label, "count": protected.filter(kind=v).count()}
                              for v, label in ProtectedReport.Kind.choices],
        "territories": territory_rows,
        "months": months,
        "min_zone": MIN_ZONE,
        "delays": {"decision_days": DECISION_DAYS, "urgent_hours": URGENT_HOURS},
    }


def fairness(org_ids, days=92):
    """Contrôle trimestriel des écarts par sexe, âge et localité (le score n'utilise aucun de ces critères)."""
    since = timezone.now() - timedelta(days=days)
    claims = list(Claim.objects.filter(organization_id__in=org_ids, registered_at__gte=since)
                  .select_related("territory__parent"))
    decisions = _decisions_by_claim([c.pk for c in claims])
    year = timezone.localdate().year

    def age_band(c):
        if not c.claimant_birth_year:
            return "Âge non renseigné"
        age = year - c.claimant_birth_year
        return "Moins de 30 ans" if age < 30 else ("30 à 59 ans" if age < 60 else "60 ans et plus")

    cache = {}
    dims = {
        "Sexe": lambda c: {"F": "Femmes", "M": "Hommes"}.get(c.claimant_sex, "Non renseigné"),
        "Âge": age_band,
        "Localité": lambda c: (_zone_for(c.territory, cache).name if c.territory_id else "Non localisée"),
    }
    out = []
    for dim, key in dims.items():
        groups = defaultdict(list)
        for c in claims:
            groups[key(c)].append(c)
        rows = []
        for name, items in groups.items():
            decided = [c for c in items if c.pk in decisions]
            rows.append({
                "group": name, "claims": len(items) if len(items) >= 3 else None,
                "avg_priority": round(sum(c.effective_priority for c in items) / len(items), 1)
                if len(items) >= 3 else None,
                "eligible_rate": _pct(sum(1 for c in decided if decisions[c.pk].outcome in ELIGIBLE), len(decided))
                if len(decided) >= 3 else None,
                "median_delay": _median([c.decision_days for c in decided]) if len(decided) >= 3 else None,
                "suppressed": len(items) < 3})
        rates = [r["eligible_rate"] for r in rows if r["eligible_rate"] is not None]
        out.append({"dimension": dim, "rows": sorted(rows, key=lambda r: r["group"]),
                    "gap": round(max(rates) - min(rates), 1) if len(rates) > 1 else None,
                    "alert": len(rates) > 1 and max(rates) - min(rates) > 15})
    return {"period_days": days, "since": since.date(), "claims": len(claims), "dimensions": out,
            "note": "Écart d'éligibilité de plus de 15 points entre groupes : à examiner en comité de pilotage."}


class _OrgReport(APIView):
    permission_classes = [IsStaffMember]

    def _orgs(self, request):
        org_id = request.query_params.get("organization")
        orgs = [Organization.objects.filter(pk=org_id).values_list("pk", flat=True).first()] if org_id else \
            list(request.user.organizations_with("report.ministry"))
        for o in orgs:
            require_org_perm(request.user, "report.ministry", o)
        return orgs


class MinistryDashboardView(_OrgReport):
    def get(self, request):
        return Response(claim_indicators(self._orgs(request)))


class FairnessView(_OrgReport):
    def get(self, request):
        return Response(fairness(self._orgs(request), int(request.query_params.get("days") or 92)))

