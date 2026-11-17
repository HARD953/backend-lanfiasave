"""
Demandes des ménages affectés par les activités minières, pétrolières et énergétiques (proposition MMPE).

Circuit : enregistrement et récépissé (48 h) → tri (5 j ouvrés) → vérification (15 j ouvrés) → décision motivée
proposée par le comité local et validée par le préfet (30 jours au plus) → appui ou orientation → suivi (3 mois)
→ clôture motivée. Urgences (eau, alimentation, santé, sécurité) : mesure ordonnée par le sous-préfet sous 72 h.
Contestation : 30 jours devant une commission de recours avec un membre indépendant, réponse en 15 jours.

L'outil ne décide de rien : il trace qui a examiné, proposé, validé et payé, et dans quels délais.
Aucun droit, indemnisation ou recours n'est subordonné à l'inscription dans l'outil.
"""
import secrets
from datetime import date, timedelta

from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel

from .referential import IMPACT_LABELS, PROFILES, SECTORS, URGENCY_REASONS

TRIAGE_BUSINESS_DAYS = 5
VERIFICATION_BUSINESS_DAYS = 15
DECISION_DAYS = 30
URGENT_HOURS = 72
OPERATOR_TRANSMIT_BUSINESS_DAYS = 5
OPERATOR_RESPONSE_DAYS = 30
FOLLOWUP_DAYS = 90
APPEAL_WINDOW_DAYS = 30
APPEAL_RESPONSE_DAYS = 15


def add_business_days(start: date, days: int) -> date:
    d = start
    while days > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days -= 1
    return d


def _code(prefix: str) -> str:
    alphabet = "ACDEFGHJKMNPQRTUVWXY34679"
    return f"{prefix}-" + "".join(secrets.choice(alphabet) for _ in range(4)) + "-" + \
        "".join(secrets.choice(alphabet) for _ in range(4))


class Claim(BaseModel):
    class Channel(models.TextChoices):
        RELAY = "relay", "Relais communautaire"
        AGENT = "agent", "Agent de la cellule locale"
        PERMANENCE = "permanence", "Permanence au village"
        PHONE = "phone", "Appel (numéro court)"
        SMS = "sms", "SMS"
        APP = "app", "Application Mon Parcours"
        PAPER = "paper", "Dossier papier"

    class Status(models.TextChoices):
        REGISTERED = "registered", "Enregistrée"
        TRIAGED = "triaged", "Triée et orientée"
        VERIFYING = "verifying", "En vérification"
        PROPOSED = "proposed", "Décision proposée par le comité"
        DECIDED = "decided", "Décision validée et notifiée"
        SUPPORT = "support", "Appui ou orientation en cours"
        FOLLOWUP = "followup", "En suivi"
        CLOSED = "closed", "Clôturée"

    class RsuStatus(models.TextChoices):
        UNKNOWN = "unknown", "Non vérifié"
        REGISTERED = "registered", "Inscrit au RSU"
        NOT_REGISTERED = "not_registered", "Non inscrit"
        REFERRED = "referred", "Orienté vers une inscription"

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="claims")
    reference = models.CharField(max_length=20, unique=True, editable=False)
    client_uuid = models.UUIDField(null=True, blank=True, unique=True,
                                   help_text="Identifiant de la saisie hors ligne (évite les doublons à l'envoi)")
    operation = models.ForeignKey("operations.Operation", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="claims", help_text="Site ou projet concerné")
    operator = models.ForeignKey("operations.Partner", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="claims", help_text="Exploitant concerné")
    territory = models.ForeignKey("territories.Territory", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="claims", help_text="Village ou sous-préfecture")
    person = models.ForeignKey("beneficiaries.Person", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="claims")
    household = models.ForeignKey("beneficiaries.Household", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="claims")
    # Données minimales (diapositive 21) — jamais d'ethnie, de religion, d'opinion, de biométrie ni de diagnostic.
    claimant_name = models.CharField(max_length=160)
    claimant_sex = models.CharField(max_length=1, choices=[("F", "Femme"), ("M", "Homme")], blank=True)
    claimant_birth_year = models.PositiveSmallIntegerField(null=True, blank=True)
    claimant_phone = models.CharField(max_length=20, blank=True, help_text="Facultatif, avec consentement")
    language = models.CharField(max_length=40, blank=True, help_text="Langue du ménage (récépissé lu à voix haute)")
    village = models.CharField(max_length=120, blank=True)
    household_size = models.PositiveSmallIntegerField(default=1)
    women_head = models.BooleanField(default=False, help_text="Femme cheffe de ménage")
    has_dependent_person = models.BooleanField(default=False,
                                               help_text="Personne dépendante dans le ménage (aucun diagnostic)")
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.RELAY)
    sector = models.CharField(max_length=20, choices=SECTORS, blank=True)
    impacts = models.JSONField(default=list, help_text="Codes du référentiel des impacts")
    profile = models.CharField(max_length=20, choices=[(c, label) for c, label, _ in PROFILES], blank=True)
    sources = models.JSONField(default=list, help_text="Sources de repérage")
    impact_date = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True, help_text="Ce qui a été perdu, avec les mots du ménage")
    assets_lost = models.TextField(blank=True)
    income_before = models.PositiveIntegerField(null=True, blank=True, help_text="Revenu mensuel avant (FCFA)")
    income_after = models.PositiveIntegerField(null=True, blank=True, help_text="Revenu mensuel après (FCFA)")
    aid_received = models.TextField(blank=True, help_text="Aides ou indemnisations déjà reçues")
    no_identity_document = models.BooleanField(default=False)
    community_validated = models.BooleanField(default=False,
                                              help_text="Validation communautaire (tient lieu de pièce d'identité)")
    community_validation_note = models.CharField(max_length=255, blank=True)
    consent_operator_contact = models.BooleanField(default=False,
                                                   help_text="Accepte que l'exploitant reçoive ses coordonnées")
    in_operator_list = models.BooleanField(null=True, blank=True,
                                           help_text="Présent sur la liste des personnes affectées de l'exploitant")
    rsu_status = models.CharField(max_length=20, choices=RsuStatus.choices, default=RsuStatus.UNKNOWN)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REGISTERED)
    urgent = models.BooleanField(default=False)
    urgency_reason = models.CharField(max_length=20, choices=URGENCY_REASONS, blank=True)
    registered_at = models.DateTimeField(default=timezone.now)
    receipt_issued_at = models.DateTimeField(null=True, blank=True)
    triaged_at = models.DateTimeField(null=True, blank=True)
    verification_started_at = models.DateTimeField(null=True, blank=True)
    proposed_at = models.DateTimeField(null=True, blank=True)
    notified_at = models.DateTimeField(null=True, blank=True)
    support_started_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closure_motive = models.TextField(blank=True)

    assigned_to = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="claims_assigned", help_text="Examinateur de la cellule locale")
    social_inquiry = models.TextField(blank=True)
    technical_service = models.CharField(max_length=20, blank=True)
    technical_opinion = models.TextField(blank=True)
    link_confirmed = models.CharField(max_length=10, blank=True, choices=[
        ("yes", "Lien avec l'activité confirmé"), ("no", "Lien non établi"), ("uncertain", "Lien incertain")])

    transmitted_to_operator_at = models.DateTimeField(null=True, blank=True)
    operator_response = models.TextField(blank=True)
    operator_responded_at = models.DateTimeField(null=True, blank=True)

    priority_score = models.PositiveSmallIntegerField(default=0)
    priority_factors = models.JSONField(default=list)
    priority_override = models.PositiveSmallIntegerField(null=True, blank=True)
    override_motive = models.TextField(blank=True)

    anonymized_at = models.DateTimeField(null=True, blank=True)
    recorded_by = models.ForeignKey("accounts.User", null=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-registered_at"]
        indexes = [models.Index(fields=["organization", "status"])]

    def __str__(self):
        return f"{self.reference} {self.claimant_name}"

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = _code("DM")
        self.priority_score, self.priority_factors = compute_priority(self)
        super().save(*args, **kwargs)

    # --- échéances ----------------------------------------------------------
    @property
    def registered_day(self) -> date:
        return timezone.localtime(self.registered_at).date()

    @property
    def receipt_due(self):
        return self.registered_at + timedelta(hours=48)

    @property
    def triage_due(self) -> date:
        return add_business_days(self.registered_day, TRIAGE_BUSINESS_DAYS)

    @property
    def verification_due(self) -> date:
        start = timezone.localtime(self.triaged_at).date() if self.triaged_at else self.registered_day
        return add_business_days(start, VERIFICATION_BUSINESS_DAYS)

    @property
    def decision_due(self) -> date:
        return self.registered_day + timedelta(days=DECISION_DAYS)

    @property
    def urgent_due(self):
        return self.registered_at + timedelta(hours=URGENT_HOURS) if self.urgent else None

    @property
    def operator_transmit_due(self):
        if self.operator_id is None or not self.triaged_at:
            return None
        return add_business_days(timezone.localtime(self.triaged_at).date(), OPERATOR_TRANSMIT_BUSINESS_DAYS)

    @property
    def operator_response_due(self):
        if not self.transmitted_to_operator_at:
            return None
        return timezone.localtime(self.transmitted_to_operator_at).date() + timedelta(days=OPERATOR_RESPONSE_DAYS)

    @property
    def followup_due(self):
        if not self.support_started_at:
            return None
        return timezone.localtime(self.support_started_at).date() + timedelta(days=FOLLOWUP_DAYS)

    @property
    def appeal_deadline(self):
        if not self.notified_at:
            return None
        return timezone.localtime(self.notified_at).date() + timedelta(days=APPEAL_WINDOW_DAYS)

    def current_step_due(self):
        """Échéance de l'étape en cours (pour les alertes de retard)."""
        return {
            self.Status.REGISTERED: self.triage_due,
            self.Status.TRIAGED: self.verification_due,
            self.Status.VERIFYING: min(self.verification_due, self.decision_due),
            self.Status.PROPOSED: self.decision_due,
            self.Status.SUPPORT: self.followup_due,
            self.Status.FOLLOWUP: self.followup_due,
        }.get(self.status)

    @property
    def is_overdue(self) -> bool:
        due = self.current_step_due()
        return bool(due and timezone.localdate() > due)

    @property
    def decision_days(self):
        if not self.notified_at:
            return None
        return (timezone.localtime(self.notified_at).date() - self.registered_day).days

    @property
    def effective_priority(self) -> int:
        return self.priority_override if self.priority_override is not None else self.priority_score

    def impact_labels(self):
        return [IMPACT_LABELS.get(c, c) for c in self.impacts]


def compute_priority(claim) -> tuple[int, list]:
    """
    Ordre d'examen (0–100). Il ordonne l'examen et signale les urgences, il ne décide JAMAIS de l'éligibilité
    ni du montant. Critères publics ; ni le sexe, ni l'âge, ni la localité ne sont notés (contrôlés à part).
    """
    factors = []

    def add(points, label):
        if points:
            factors.append({"label": label, "points": points})

    if claim.urgent:
        add(30, "Urgence signalée (" + dict(URGENCY_REASONS).get(claim.urgency_reason, "à préciser") + ")")
    add({"vulnerable": 15, "weakened": 10, "indirect": 5}.get(claim.profile, 0), "Profil du ménage")
    if claim.income_before:
        lost = max(0, claim.income_before - (claim.income_after or 0)) / claim.income_before
        add(round(min(lost, 1) * 25), f"Revenu perdu ({round(lost * 100)} %)")
    if "water_access" in (claim.impacts or []):
        add(10, "Accès à l'eau touché")
    if claim.has_dependent_person:
        add(10, "Personne dépendante dans le ménage")
    if claim.household_size >= 6:
        add(10, "Ménage de 6 personnes ou plus")
    elif claim.household_size >= 4:
        add(5, "Ménage de 4 ou 5 personnes")
    if not (claim.aid_received or "").strip():
        add(5, "Aucune aide ni indemnisation reçue")
    if claim.in_operator_list is False:
        add(5, "Absent de la liste de l'exploitant")
    return min(100, sum(f["points"] for f in factors)), factors


class ClaimEvent(BaseModel):
    """Historique horodaté de chaque étape (journal lisible du dossier)."""

    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=40)
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, blank=True)
    note = models.TextField(blank=True)
    actor = models.ForeignKey("accounts.User", null=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["created_at"]


class ClaimDecision(BaseModel):
    class Outcome(models.TextChoices):
        SUPPORT = "support", "Appui accordé"
        ORIENTATION = "orientation", "Orientation vers le service compétent"
        SUPPORT_ORIENTATION = "support_orientation", "Appui et orientation"
        REJECTED = "rejected", "Demande rejetée (motif écrit)"
        DEFERRED = "deferred", "Examen ajourné (compléments demandés)"

    class Validation(models.TextChoices):
        PENDING = "pending", "En attente de validation"
        VALIDATED = "validated", "Validée"
        RETURNED = "returned", "Renvoyée au comité"

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="+")
    claim = models.ForeignKey(Claim, on_delete=models.PROTECT, related_name="decisions")
    outcome = models.CharField(max_length=30, choices=Outcome.choices)
    motive = models.TextField(help_text="Motif compréhensible, lu au ménage")
    measures = models.JSONField(default=list, help_text="Appuis et orientations décidés")
    amount = models.PositiveIntegerField(null=True, blank=True, help_text="Montant de l'appui (FCFA)")
    committee_members = models.JSONField(default=list)
    proposed_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="+")
    validation = models.CharField(max_length=20, choices=Validation.choices, default=Validation.PENDING)
    validated_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+")
    validated_at = models.DateTimeField(null=True, blank=True)
    validation_note = models.TextField(blank=True)
    after_appeal = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]


class UrgentMeasure(BaseModel):
    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="urgent_measures")
    reason = models.CharField(max_length=20, choices=URGENCY_REASONS)
    measure = models.TextField()
    ordered_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="+")
    executed_at = models.DateTimeField(null=True, blank=True)
    execution_note = models.TextField(blank=True)

    @property
    def executed_in_time(self):
        if not self.executed_at:
            return None
        return self.executed_at <= self.claim.registered_at + timedelta(hours=URGENT_HOURS)


class ClaimAppeal(BaseModel):
    class Status(models.TextChoices):
        OPEN = "open", "Déposée"
        UPHELD = "upheld", "Accueillie : nouvel examen"
        DISMISSED = "dismissed", "Rejetée (motif écrit)"

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="+")
    claim = models.ForeignKey(Claim, on_delete=models.PROTECT, related_name="appeals")
    decision = models.ForeignKey(ClaimDecision, on_delete=models.PROTECT, related_name="appeals")
    reference = models.CharField(max_length=20, unique=True, editable=False)
    motive = models.TextField()
    filed_by = models.ForeignKey("accounts.User", null=True, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    commission_members = models.JSONField(default=list)
    independent_member = models.CharField(max_length=160, blank=True)
    resolution = models.TextField(blank=True)
    resolved_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+")
    resolved_at = models.DateTimeField(null=True, blank=True)
    decision_modified = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = _code("CT")
        super().save(*args, **kwargs)

    @property
    def response_due(self) -> date:
        return timezone.localtime(self.created_at or timezone.now()).date() + timedelta(days=APPEAL_RESPONSE_DAYS)

    @property
    def is_overdue(self):
        return self.status == self.Status.OPEN and timezone.localdate() > self.response_due


class ProtectedReport(BaseModel):
    """
    Plainte confidentielle (canal séparé, hors de la chaîne de l'exploitant) ou signalement de représailles.
    Traitée par le membre indépendant de la commission de recours. Identité levée seulement avec l'accord écrit.
    """

    class Kind(models.TextChoices):
        OPERATOR = "operator", "Plainte visant l'exploitant"
        SUBCONTRACTOR = "subcontractor", "Plainte visant un sous-traitant"
        LOCAL_AUTHORITY = "local_authority", "Plainte visant une autorité locale"
        RELAY = "relay", "Plainte visant un relais ou un agent"
        REPRISAL = "reprisal", "Représailles après une demande ou une plainte"
        AID_CONDITIONED = "aid_conditioned", "Aide conditionnée au retrait d'une plainte"
        SEA = "sea", "Exploitation ou abus sexuels"
        OTHER = "other", "Autre"

    class Status(models.TextChoices):
        RECEIVED = "received", "Reçue"
        INVESTIGATING = "investigating", "En traitement"
        RESOLVED = "resolved", "Traitée"
        CLOSED = "closed", "Clôturée"

    PRIORITY_KINDS = {Kind.REPRISAL, Kind.AID_CONDITIONED, Kind.SEA}

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="+")
    reference = models.CharField(max_length=20, unique=True, editable=False)
    claim = models.ForeignKey(Claim, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    territory = models.ForeignKey("territories.Territory", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="+")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    channel = models.CharField(max_length=20, choices=Claim.Channel.choices, default=Claim.Channel.PHONE)
    description = models.TextField()
    reporter_name = models.CharField(max_length=160, blank=True)
    reporter_phone = models.CharField(max_length=20, blank=True)
    consent_disclosure = models.BooleanField(default=False, help_text="Accord écrit pour lever l'identité")
    consent_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RECEIVED)
    due_at = models.DateTimeField(null=True, blank=True)
    prefect_notified_at = models.DateTimeField(null=True, blank=True)
    followup_1m_done_at = models.DateTimeField(null=True, blank=True)
    followup_3m_done_at = models.DateTimeField(null=True, blank=True)
    resolution = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    handled_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+")
    recorded_by = models.ForeignKey("accounts.User", null=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = _code("PC")
        if not self.due_at:
            start = self.created_at or timezone.now()
            self.due_at = start + (timedelta(hours=URGENT_HOURS) if self.kind in self.PRIORITY_KINDS
                                   else timedelta(days=APPEAL_RESPONSE_DAYS))
        super().save(*args, **kwargs)

    @property
    def is_priority(self):
        return self.kind in self.PRIORITY_KINDS

    @property
    def is_overdue(self):
        return self.status in {self.Status.RECEIVED, self.Status.INVESTIGATING} and timezone.now() > self.due_at

    @property
    def followup_1m_due(self):
        return (self.created_at + timedelta(days=30)).date() if self.kind == self.Kind.REPRISAL else None

    @property
    def followup_3m_due(self):
        return (self.created_at + timedelta(days=90)).date() if self.kind == self.Kind.REPRISAL else None


class OperatorListEntry(BaseModel):
    """Ligne de la liste des personnes affectées transmise par l'exploitant (avec son accord)."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="+")
    operation = models.ForeignKey("operations.Operation", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="+")
    operator = models.ForeignKey("operations.Partner", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="+")
    batch = models.CharField(max_length=40)
    full_name = models.CharField(max_length=160)
    village = models.CharField(max_length=120, blank=True)
    household_size = models.PositiveSmallIntegerField(null=True, blank=True)
    assets = models.CharField(max_length=255, blank=True)
    cutoff_date = models.DateField(null=True, blank=True)
    matched_claim = models.ForeignKey(Claim, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    imported_by = models.ForeignKey("accounts.User", null=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["full_name"]


class EvaluationSample(BaseModel):
    """Échantillon tiré au sort pour l'évaluateur indépendant ; chaque consultation est journalisée."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="+")
    evaluator = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="+")
    claims = models.ManyToManyField(Claim, related_name="+")
    seed = models.CharField(max_length=40)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
