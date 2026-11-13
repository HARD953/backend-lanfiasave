from django.db import models

from apps.core.models import BaseModel


class Territory(BaseModel):
    """
    Référentiel territorial hiérarchique (cahier §3). Les niveaux et effectifs ne sont jamais codés en dur :
    un territoire a un parent, une source, une période de validité et un niveau de fiabilité.
    """

    class Level(models.TextChoices):
        COUNTRY = "country", "Pays"
        DISTRICT = "district", "District"
        REGION = "region", "Région"
        DEPARTMENT = "department", "Département"
        SUBPREFECTURE = "subprefecture", "Sous-préfecture"
        COMMUNE = "commune", "Commune"
        NEIGHBORHOOD = "neighborhood", "Quartier"
        VILLAGE = "village", "Village"
        SITE = "site", "Site / lieu de regroupement"

    class Status(models.TextChoices):
        ACTIVE = "active", "Actif"
        INACTIVE = "inactive", "Inactif"

    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=160)
    level = models.CharField(max_length=20, choices=Level.choices)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    country = models.CharField(max_length=2, default="CI")
    source = models.CharField(max_length=200, blank=True)
    reliability = models.CharField(max_length=20, default="declared",
                                   choices=[("official", "Officiel"), ("declared", "Déclaré"),
                                            ("estimated", "Estimé")])
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    class Meta:
        ordering = ["level", "name"]
        indexes = [models.Index(fields=["parent", "level"])]

    def __str__(self):
        return f"{self.name} ({self.get_level_display()})"

    def path(self) -> list["Territory"]:
        node, out = self, []
        while node is not None:
            out.append(node)
            node = node.parent
        return list(reversed(out))
