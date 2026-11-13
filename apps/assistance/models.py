"""
Suivi des aides apportées aux personnes (cahier §8, AID-01/02).

Chaque aide est rattachée à une personne et, le cas échéant, à son dossier d'opération et à un programme.
Une aide « planifiée » n'est pas une aide « remise » : seule la confirmation avec preuve change son statut.
Rien n'est effacé : une aide remise par erreur est annulée avec un motif (contre-écriture), jamais supprimée.
"""
from django.db import models

from apps.core.models import BaseModel


class Assistance(BaseModel):
    class Category(models.TextChoices):
        CASH = "cash", "Transfert monétaire"
        COMPENSATION = "compensation", "Indemnisation"
        RELOCATION = "relocation", "Aide au relogement / loyer"
        FOOD = "food", "Vivres / kit alimentaire"
        NFI = "nfi", "Kit non alimentaire (nattes, ustensiles, hygiène)"
        SHELTER = "shelter", "Abri / hébergement d'urgence"
        HEALTH = "health", "Soins / prise en charge médicale"
        NUTRITION = "nutrition", "Prise en charge nutritionnelle"
        EDUCATION = "education", "Scolarité (frais, kits, réinscription)"
        CIVIL_DOCUMENTS = "civil_documents", "Actes d'état civil / pièces d'identité"
        LEGAL = "legal", "Assistance juridique"
        PSYCHOSOCIAL = "psychosocial", "Soutien psychosocial"
        TRAINING = "training", "Formation professionnelle"
        LIVELIHOOD = "livelihood", "Appui à une activité (AGR, équipement)"
        HEALTH_COVERAGE = "health_coverage", "Inscription / prise en charge CMU"
        REFERRAL = "referral", "Orientation vers un service"
        OTHER = "other", "Autre"

    class Status(models.TextChoices):
        PLANNED = "planned", "Planifiée"
        DELIVERED = "delivered", "Remise"
        CANCELLED = "cancelled", "Annulée"

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT,
                                     related_name="assistances")
    person = models.ForeignKey("beneficiaries.Person", on_delete=models.PROTECT, related_name="assistances")
    household = models.ForeignKey("beneficiaries.Household", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="assistances")
    operation_case = models.ForeignKey("operations.OperationCase", null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="assistances")
    program = models.ForeignKey("programs.Program", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="assistances")
    provider = models.ForeignKey("operations.Partner", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="assistances", help_text="Structure qui apporte l'aide")
    category = models.CharField(max_length=30, choices=Category.choices)
    description = models.CharField(max_length=255, blank=True)
    quantity = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    unit = models.CharField(max_length=30, blank=True, help_text="kg, kit, mois, séance…")
    amount = models.DecimalField(max_digits=14, decimal_places=0, null=True, blank=True,
                                 help_text="Montant ou valeur estimée en FCFA")
    currency = models.CharField(max_length=3, default="XOF")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)
    planned_date = models.DateField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    delivery_proof = models.CharField(max_length=255, blank=True,
                                      help_text="Référence de la preuve : reçu, liste émargée, n° transaction…")
    delivery_channel = models.CharField(max_length=30, blank=True, choices=[
        ("hand", "Remise en main propre"), ("mobile_money", "Mobile money"), ("bank", "Virement bancaire"),
        ("voucher", "Bon / coupon"), ("service", "Prestation de service"), ("other", "Autre")])
    cancel_reason = models.TextField(blank=True)
    recorded_by = models.ForeignKey("accounts.User", null=True, on_delete=models.PROTECT, related_name="+")
    delivered_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "status", "category"]), models.Index(fields=["person"])]

    def __str__(self):
        return f"{self.get_category_display()} → {self.person}"
