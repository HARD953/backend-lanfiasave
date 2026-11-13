import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel

from .roles import ROLE_PERMISSIONS, Role


class User(AbstractUser):
    """
    Compte utilisateur. Un compte « bénéficiaire » est relié à un dossier Person et
    n'a aucune adhésion d'organisation : il ne voit que son propre parcours.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone = models.CharField(max_length=20, blank=True)
    beneficiary = models.OneToOneField("beneficiaries.Person", null=True, blank=True,
                                       on_delete=models.PROTECT, related_name="account")
    must_change_password = models.BooleanField(default=False)

    @property
    def is_beneficiary(self) -> bool:
        return self.beneficiary_id is not None

    def active_memberships(self):
        return self.memberships.filter(is_active=True).select_related("organization")

    def organizations_with(self, perm: str) -> set:
        """Identifiants des organisations où l'utilisateur détient `perm`."""
        cache = getattr(self, "_perm_cache", None)
        if cache is None:
            cache = {}
            for m in self.active_memberships():
                for p in ROLE_PERMISSIONS.get(m.role, set()):
                    cache.setdefault(p, set()).add(m.organization_id)
            self._perm_cache = cache
        return cache.get(perm, set())

    def has_org_perm(self, perm: str, organization_id) -> bool:
        return organization_id in self.organizations_with(perm)

    def program_restrictions(self, organization_id) -> set | None:
        """Programmes auxquels l'utilisateur est restreint dans une organisation (None = tous)."""
        ms = [m for m in self.active_memberships() if m.organization_id == organization_id]
        if any(m.program_id is None for m in ms):
            return None
        return {m.program_id for m in ms}


class Membership(BaseModel):
    """Adhésion d'un utilisateur à une organisation avec un rôle, éventuellement limitée à un programme."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT,
                                     related_name="memberships")
    role = models.CharField(max_length=30, choices=Role.choices)
    program = models.ForeignKey("programs.Program", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="memberships")
    territory = models.ForeignKey("territories.Territory", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="memberships")
    is_active = models.BooleanField(default=True)
    valid_until = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "organization", "role", "program"],
                                    condition=Q(is_active=True), name="uniq_active_membership"),
        ]

    def __str__(self):
        return f"{self.user} — {self.get_role_display()} @ {self.organization}"
