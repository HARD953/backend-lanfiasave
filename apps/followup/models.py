"""
Suivi social : plaintes, orientations et visites.

Inspiré des pratiques reconnues des grands registres sociaux et filets sociaux :
- Mécanisme de gestion des plaintes (Banque mondiale ; Inua Jamii au Kenya ; Pantawid 4Ps aux Philippines) :
  canaux multiples dont anonyme, accusé de réception, délai de traitement selon la gravité, escalade,
  confidentialité renforcée pour les plaintes d'exploitation / abus sexuels (EAS) et de fraude.
- Orientation et contre-référence (SUAS au Brésil, Chile Solidario) : la personne est orientée vers un
  service, et le service confirme la prise en charge. Une orientation sans retour reste « en attente ».
- Visites de suivi (accompagnement familial PAIF au Brésil, Puente au Chili, programmes de graduation) :
  visites régulières, évolution de la situation, date de la prochaine visite.
- Revisite des ménages (Cadastro Único : mise à jour tous les 24 mois) : calculée à partir de la dernière enquête.
"""
from datetime import timedelta

from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel
from apps.operations.models import _code


class Grievance(BaseModel):
    class Channel(models.TextChoices):
        IN_PERSON = "in_person", "Au guichet / en personne"
        FIELD_AGENT = "field_agent", "Auprès d'un agent terrain"
        PHONE = "phone", "Téléphone / ligne verte"
        SMS = "sms", "SMS / WhatsApp"
        APP = "app", "Application Mon Parcours"
        LETTER = "letter", "Courrier"
        COMMUNITY = "community", "Comité communautaire / chefferie"
        SUGGESTION_BOX = "suggestion_box", "Boîte à suggestions"

    class Category(models.TextChoices):
        EXCLUSION = "exclusion", "Exclusion / non-recensement"
        INCLUSION_ERROR = "inclusion_error", "Inclusion indue signalée"
        PAYMENT_DELAY = "payment_delay", "Retard de paiement ou d'aide"
        PAYMENT_AMOUNT = "payment_amount", "Montant ou aide incomplète"
        COMPENSATION = "compensation", "Indemnisation (déguerpissement, projet)"
        RELOCATION = "relocation", "Relogement / site d'accueil"
        DATA_CORRECTION = "data_correction", "Correction de données personnelles"
        SERVICE_QUALITY = "service_quality", "Qualité de service / accueil"
        STAFF_CONDUCT = "staff_conduct", "Comportement d'un agent"
        FRAUD = "fraud", "Fraude / corruption / racket"
        SEA = "sea", "Exploitation ou abus sexuels (EAS)"
        INFORMATION = "information", "Demande d'information"
        OTHER = "other", "Autre"

    class Priority(models.TextChoices):
        URGENT = "urgent", "Urgente"
        HIGH = "high", "Haute"
        NORMAL = "normal", "Normale"

    class Status(models.TextChoices):
        RECEIVED = "received", "Reçue"
        ACKNOWLEDGED = "acknowledged", "Accusé de réception"
        INVESTIGATING = "investigating", "En instruction"
        ESCALATED = "escalated", "Escaladée"
        RESOLVED = "resolved", "Résolue"
        CLOSED = "closed", "Clôturée"
        REJECTED = "rejected", "Non fondée"

    # Catégories confidentielles : détails masqués sans habilitation aux données sensibles.
    CONFIDENTIAL = {Category.SEA, Category.FRAUD, Category.STAFF_CONDUCT}
    # Délai cible de traitement (jours) selon la priorité.
    SLA_DAYS = {Priority.URGENT: 3, Priority.HIGH: 10, Priority.NORMAL: 30}
    OPEN = {Status.RECEIVED, Status.ACKNOWLEDGED, Status.INVESTIGATING, Status.ESCALATED}
    TRANSITIONS = {
        Status.RECEIVED: {Status.ACKNOWLEDGED, Status.INVESTIGATING, Status.ESCALATED, Status.RESOLVED,
                          Status.REJECTED},
        Status.ACKNOWLEDGED: {Status.INVESTIGATING, Status.ESCALATED, Status.RESOLVED, Status.REJECTED},
        Status.INVESTIGATING: {Status.ESCALATED, Status.RESOLVED, Status.REJECTED},
        Status.ESCALATED: {Status.INVESTIGATING, Status.RESOLVED, Status.REJECTED},
        Status.RESOLVED: {Status.CLOSED, Status.INVESTIGATING},
        Status.REJECTED: {Status.CLOSED, Status.INVESTIGATING},
        Status.CLOSED: set(),
    }

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT,
                                     related_name="grievances")
    reference = models.CharField(max_length=20, unique=True, editable=False)
    person = models.ForeignKey("beneficiaries.Person", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="grievances")
    operation = models.ForeignKey("operations.Operation", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="grievances")
    program = models.ForeignKey("programs.Program", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="grievances")
    territory = models.ForeignKey("territories.Territory", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="+")
    anonymous = models.BooleanField(default=False)
    complainant_name = models.CharField(max_length=150, blank=True)
    complainant_phone = models.CharField(max_length=30, blank=True)
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.IN_PERSON)
    category = models.CharField(max_length=20, choices=Category.choices)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    subject = models.CharField(max_length=200)
    description = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RECEIVED)
    due_date = models.DateField()
    assigned_to = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+")
    escalated_to = models.CharField(max_length=150, blank=True, help_text="Instance saisie en cas d'escalade")
    resolution = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    satisfaction = models.CharField(max_length=20, blank=True, choices=[
        ("satisfied", "Satisfait"), ("partly", "Partiellement satisfait"), ("unsatisfied", "Non satisfait")])
    recorded_by = models.ForeignKey("accounts.User", null=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "status", "category"])]

    @property
    def is_confidential(self):
        return self.category in self.CONFIDENTIAL

    @property
    def is_overdue(self):
        return self.status in self.OPEN and self.due_date < timezone.localdate()

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = _code("PL")
        if not self.due_date:
            self.due_date = timezone.localdate() + timedelta(days=self.SLA_DAYS[self.priority])
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.reference} {self.subject}"


class Referral(BaseModel):
    class Service(models.TextChoices):
        HEALTH = "health", "Centre de santé / hôpital"
        CMU = "cmu", "Couverture maladie universelle (CMU)"
        NUTRITION = "nutrition", "Prise en charge nutritionnelle"
        CIVIL_REGISTRY = "civil_registry", "État civil (acte de naissance, jugement supplétif)"
        IDENTITY = "identity", "Pièce d'identité (ONECI)"
        SCHOOL = "school", "École / réinscription scolaire"
        SOCIAL_CENTER = "social_center", "Centre social"
        LEGAL = "legal", "Aide juridique / clinique juridique"
        GBV = "gbv", "Prise en charge VBG (plateforme, centre)"
        PSYCHOSOCIAL = "psychosocial", "Soutien psychosocial / santé mentale"
        CHILD_PROTECTION = "child_protection", "Protection de l'enfance"
        DISABILITY = "disability", "Services aux personnes handicapées"
        EMPLOYMENT = "employment", "Emploi / formation (AEJ, FDFP…)"
        FINANCIAL = "financial", "Inclusion financière (compte, mobile money, microfinance)"
        HOUSING = "housing", "Logement / relogement"
        OTHER = "other", "Autre service"

    class Status(models.TextChoices):
        SENT = "sent", "Orientée"
        ACCEPTED = "accepted", "Prise en charge acceptée"
        COMPLETED = "completed", "Service rendu"
        REFUSED = "refused", "Refusée par le service"
        NO_SHOW = "no_show", "Personne non présentée"
        CANCELLED = "cancelled", "Annulée"

    TRANSITIONS = {
        Status.SENT: {Status.ACCEPTED, Status.COMPLETED, Status.REFUSED, Status.NO_SHOW, Status.CANCELLED},
        Status.ACCEPTED: {Status.COMPLETED, Status.NO_SHOW, Status.CANCELLED},
        Status.NO_SHOW: {Status.SENT, Status.CANCELLED},
        Status.REFUSED: {Status.SENT, Status.CANCELLED},
        Status.COMPLETED: set(),
        Status.CANCELLED: set(),
    }
    OPEN = {Status.SENT, Status.ACCEPTED}

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT,
                                     related_name="referrals")
    reference = models.CharField(max_length=20, unique=True, editable=False)
    person = models.ForeignKey("beneficiaries.Person", on_delete=models.PROTECT, related_name="referrals")
    operation_case = models.ForeignKey("operations.OperationCase", null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="referrals")
    service = models.CharField(max_length=20, choices=Service.choices)
    partner = models.ForeignKey("operations.Partner", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="referrals", help_text="Structure qui reçoit la personne")
    service_name = models.CharField(max_length=150, blank=True, help_text="Si la structure n'est pas référencée")
    reason = models.TextField()
    urgent = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SENT)
    appointment_date = models.DateField(null=True, blank=True)
    feedback = models.TextField(blank=True, help_text="Contre-référence : retour du service")
    closed_at = models.DateTimeField(null=True, blank=True)
    referred_by = models.ForeignKey("accounts.User", null=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "status", "service"])]

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = _code("OR")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.reference} {self.get_service_display()}"


class Visit(BaseModel):
    class Kind(models.TextChoices):
        HOME = "home", "Visite à domicile"
        PHONE = "phone", "Appel téléphonique"
        OFFICE = "office", "Entretien au bureau"
        GROUP = "group", "Séance collective"
        RECERTIFICATION = "recertification", "Revisite de mise à jour"

    class Trend(models.TextChoices):
        IMPROVED = "improved", "En amélioration"
        STABLE = "stable", "Stable"
        WORSENED = "worsened", "En dégradation"
        CRITICAL = "critical", "Situation critique"

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="visits")
    person = models.ForeignKey("beneficiaries.Person", on_delete=models.PROTECT, related_name="visits")
    household = models.ForeignKey("beneficiaries.Household", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="visits")
    operation_case = models.ForeignKey("operations.OperationCase", null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="visits")
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.HOME)
    visited_at = models.DateField()
    person_present = models.BooleanField(default=True)
    trend = models.CharField(max_length=20, choices=Trend.choices, default=Trend.STABLE)
    summary = models.TextField(help_text="Observations factuelles, sans jugement")
    needs_identified = models.JSONField(default=list, blank=True)
    next_visit_date = models.DateField(null=True, blank=True)
    visited_by = models.ForeignKey("accounts.User", null=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-visited_at", "-created_at"]
        indexes = [models.Index(fields=["organization", "next_visit_date"]), models.Index(fields=["person"])]

    def __str__(self):
        return f"{self.get_kind_display()} {self.visited_at} {self.person}"
