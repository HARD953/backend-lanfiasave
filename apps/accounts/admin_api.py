"""
Administration des comptes (portail web → Administration).

- Un administrateur d'organisation (permission `user.manage`) crée, modifie, désactive les comptes de SON
  organisation et leur attribue des rôles.
- Le super-administrateur de la plateforme gère toutes les organisations et peut créer leur premier compte.
- Un mot de passe provisoire est généré et affiché une seule fois ; il doit être changé à la première connexion.
"""
import secrets
import string

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import mixins, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import audit
from apps.organizations.models import Organization

from .models import Membership, User
from .roles import ROLE_PERMISSIONS, Role


def temporary_password() -> str:
    alphabet = string.ascii_letters + string.digits
    core = "".join(secrets.choice(alphabet) for _ in range(10))
    return f"Lfs-{core}-{secrets.randbelow(90) + 10}"


def manageable_org_ids(user) -> set | None:
    """None = toutes les organisations (super-administrateur de la plateforme)."""
    if user.is_superuser:
        return None
    return set(user.organizations_with("user.manage"))


class CanManageUsers(permissions.BasePermission):
    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and not u.is_beneficiary
                    and (u.is_superuser or u.organizations_with("user.manage")))


class IsPlatformAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)


class RoleAssignmentSerializer(serializers.Serializer):
    organization = serializers.PrimaryKeyRelatedField(queryset=Organization.objects.all())
    roles = serializers.ListField(child=serializers.ChoiceField(choices=Role.choices), allow_empty=True)


class AdminUserSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    memberships = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "full_name", "email", "phone", "is_active",
                  "is_superuser", "must_change_password", "last_login", "date_joined", "memberships"]
        read_only_fields = ["is_superuser", "must_change_password", "last_login", "date_joined"]

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username

    def get_memberships(self, obj):
        scope = self.context.get("scope")
        ms = obj.memberships.filter(is_active=True).select_related("organization", "program")
        if scope is not None:
            ms = ms.filter(organization_id__in=scope)
        return [{"id": str(m.id), "organization": str(m.organization_id), "organization_name": m.organization.name,
                 "role": m.role, "role_label": m.get_role_display(),
                 "program": str(m.program_id) if m.program_id else None} for m in ms]


class AdminUserCreateSerializer(serializers.Serializer):
    username = serializers.RegexField(r"^[\w.@+-]{3,150}$", error_messages={
        "invalid": "Identifiant : 3 caractères minimum, lettres, chiffres et . @ + - _ uniquement."})
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150, allow_blank=True, default="")
    email = serializers.EmailField(allow_blank=True, default="")
    phone = serializers.CharField(max_length=20, allow_blank=True, default="")
    organization = serializers.PrimaryKeyRelatedField(queryset=Organization.objects.all())
    roles = serializers.ListField(child=serializers.ChoiceField(choices=Role.choices), min_length=1)
    password = serializers.CharField(required=False, allow_blank=True, write_only=True)

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise ValidationError("Cet identifiant est déjà utilisé.")
        return value


def _check_scope(request, organization_id):
    scope = manageable_org_ids(request.user)
    if scope is not None and organization_id not in scope:
        audit("access.denied", outcome="denied", data={"perm": "user.manage", "organization": str(organization_id)})
        raise PermissionDenied("Vous ne pouvez gérer que les comptes de votre organisation.")


def _set_roles(user, organization, roles, actor):
    current = {m.role: m for m in Membership.objects.filter(user=user, organization=organization, is_active=True,
                                                            program__isnull=True)}
    wanted = set(roles)
    for role, m in current.items():
        if role not in wanted:
            m.is_active = False
            m.save(update_fields=["is_active", "updated_at"])
    for role in wanted - set(current):
        Membership.objects.create(user=user, organization=organization, role=role)
    audit("account.roles_changed", obj=organization, actor=actor, organization=organization,
          data={"user": user.username, "roles": sorted(wanted)})


class AdminUserViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin,
                       viewsets.GenericViewSet):
    serializer_class = AdminUserSerializer
    permission_classes = [CanManageUsers]
    search_fields = ["username", "first_name", "last_name", "email", "phone"]

    def get_queryset(self):
        scope = manageable_org_ids(self.request.user)
        qs = User.objects.filter(beneficiary__isnull=True).prefetch_related("memberships").order_by("last_name",
                                                                                                    "username")
        org = self.request.query_params.get("organization")
        if scope is None:
            if org:
                qs = qs.filter(memberships__organization_id=org)
            return qs.distinct()
        if org:
            scope = {o for o in scope if str(o) == org}
        return qs.filter(memberships__organization_id__in=scope).distinct()

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["scope"] = manageable_org_ids(self.request.user)
        return ctx

    def create(self, request):
        ser = AdminUserCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        _check_scope(request, d["organization"].pk)
        password = d.get("password") or temporary_password()
        generated = not d.get("password")
        user = User(username=d["username"], first_name=d["first_name"], last_name=d["last_name"], email=d["email"],
                    phone=d["phone"], must_change_password=True)
        try:
            validate_password(password, user)
        except DjangoValidationError as exc:
            raise ValidationError({"password": exc.messages})
        with transaction.atomic():
            user.set_password(password)
            user.save()
            _set_roles(user, d["organization"], d["roles"], request.user)
            audit("account.created", obj=d["organization"], organization=d["organization"],
                  data={"user": user.username})
        data = AdminUserSerializer(user, context=self.get_serializer_context()).data
        if generated:
            data["temporary_password"] = password
        return Response(data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        user = serializer.instance
        if user == self.request.user and serializer.validated_data.get("is_active") is False:
            raise ValidationError({"is_active": "Vous ne pouvez pas désactiver votre propre compte."})
        if user.is_superuser and not self.request.user.is_superuser:
            raise PermissionDenied("Ce compte est géré par l'administrateur de la plateforme.")
        serializer.save()
        audit("account.updated", data={"user": user.username, "fields": list(serializer.validated_data)})

    @action(detail=True, methods=["post"])
    def roles(self, request, pk=None):
        user = self.get_object()
        ser = RoleAssignmentSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        org = ser.validated_data["organization"]
        _check_scope(request, org.pk)
        roles = ser.validated_data["roles"]
        if user == request.user and Role.ORG_ADMIN not in roles and not request.user.is_superuser:
            raise ValidationError({"roles": "Vous ne pouvez pas retirer votre propre rôle d'administrateur."})
        with transaction.atomic():
            _set_roles(user, org, roles, request.user)
        return Response(AdminUserSerializer(user, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password(self, request, pk=None):
        user = self.get_object()
        if user.is_superuser and not request.user.is_superuser:
            raise PermissionDenied("Ce compte est géré par l'administrateur de la plateforme.")
        password = temporary_password()
        user.set_password(password)
        user.must_change_password = True
        user.save(update_fields=["password", "must_change_password"])
        audit("account.password_reset", data={"user": user.username})
        return Response({"username": user.username, "temporary_password": password,
                         "message": "Communiquez ce mot de passe provisoire à la personne. Il ne sera plus affiché."})


class RolesCatalogView(APIView):
    """Liste des rôles et de leurs permissions, pour l'écran d'administration."""

    permission_classes = [CanManageUsers]

    def get(self, request):
        from .roles import P

        return Response([{"value": r.value, "label": r.label,
                          "permissions": [{"code": p, "label": P[p]} for p in sorted(ROLE_PERMISSIONS.get(r, []))]}
                         for r in Role])


class AdminOrganizationSerializer(serializers.ModelSerializer):
    members_count = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = ["id", "code", "name", "kind", "country", "status", "health_coverage_label", "members_count",
                  "created_at"]

    def get_members_count(self, obj):
        return obj.memberships.filter(is_active=True).values("user").distinct().count()


class AdminOrganizationViewSet(viewsets.ModelViewSet):
    """Organisations clientes de la plateforme (super-administrateur uniquement)."""

    queryset = Organization.objects.all().order_by("name")
    serializer_class = AdminOrganizationSerializer
    permission_classes = [IsPlatformAdmin]
    http_method_names = ["get", "post", "patch"]

    def perform_create(self, serializer):
        org = serializer.save()
        audit("organization.created", obj=org, organization=org)

    def perform_update(self, serializer):
        org = serializer.save()
        audit("organization.updated", obj=org, organization=org, data={"fields": list(serializer.validated_data)})


class ChangePasswordView(APIView):
    """Changement de mot de passe par l'utilisateur (obligatoire après un mot de passe provisoire)."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        old, new = request.data.get("old_password", ""), request.data.get("new_password", "")
        if not user.check_password(old):
            raise ValidationError({"old_password": "Mot de passe actuel incorrect."})
        if old == new:
            raise ValidationError({"new_password": "Le nouveau mot de passe doit être différent."})
        try:
            validate_password(new, user)
        except DjangoValidationError as exc:
            raise ValidationError({"new_password": exc.messages})
        user.set_password(new)
        user.must_change_password = False
        user.save(update_fields=["password", "must_change_password"])
        audit("account.password_changed", actor=user)
        return Response({"ok": True})

