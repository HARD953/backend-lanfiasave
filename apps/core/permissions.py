"""
Contrôle d'accès par organisation, appliqué côté serveur (GOV-01, MCD-01, SEC-01).

Chaque ViewSet « métier » déclare la permission requise par action. Le queryset est filtré sur les
organisations où l'utilisateur détient cette permission : un identifiant d'un autre tenant renvoie 404,
jamais la donnée.
"""
from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied

from .audit import audit


def require_org_perm(user, perm: str, organization_id, *, obj=None):
    if not user.has_org_perm(perm, organization_id):
        audit("access.denied", obj=obj, outcome="denied", data={"perm": perm, "organization": str(organization_id)})
        raise PermissionDenied("Vous n'avez pas l'habilitation requise pour cette action.")


class IsStaffMember(permissions.BasePermission):
    """Utilisateur ayant au moins une adhésion active (exclut les comptes bénéficiaires)."""

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and not u.is_beneficiary and u.active_memberships().exists())


class IsBeneficiary(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_beneficiary)


class OrgScopedViewSetMixin:
    """
    `perms` : dict action -> permission. Les actions absentes utilisent `perms["default"]`.
    `org_field` : chemin ORM vers l'organisation.
    `program_field` : chemin ORM vers le programme (restriction par programme), facultatif.
    """

    perms: dict = {}
    org_field = "organization"
    program_field = None
    permission_classes = [IsStaffMember]

    def required_perm(self):
        return self.perms.get(self.action) or self.perms.get("default")

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        perm = self.required_perm()
        org_ids = user.organizations_with(perm)
        qs = qs.filter(**{f"{self.org_field}__in": org_ids})
        if self.program_field:
            from django.db.models import Q

            cond = Q()
            for org_id in org_ids:
                allowed = user.program_restrictions(org_id)
                if allowed is None:
                    cond |= Q(**{self.org_field: org_id})
                else:
                    cond |= Q(**{self.org_field: org_id, f"{self.program_field}__in": allowed})
            qs = qs.filter(cond) if org_ids else qs.none()
        return qs

    def check_create_scope(self, organization_id):
        require_org_perm(self.request.user, self.required_perm(), organization_id)
